# smart_agent/bot/utils/redis_repo.py
#Всегда пиши код без «поддержки старых версий». Если они есть в коде - удаляй.

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

try:
    # redis-py 5.x с async API
    from redis.asyncio import Redis
except Exception as e:
    raise RuntimeError("Install redis>=5.0: pip install redis>=5.0") from e

LOG = logging.getLogger(__name__)

def _make_redis() -> Redis:
    """
    Подключение берётся из REDIS_URL (например: redis://localhost:6379/0).
    Если переменная не задана — используем локальный по умолчанию.
    """
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # decode_responses=True → str вместо bytes
    return Redis.from_url(
        url,
        decode_responses=True,
        health_check_interval=30,
        socket_timeout=5,
    )

_redis = _make_redis()

def _now_ts() -> int:
    """Unix time seconds."""
    return int(time.time())


async def set_nx_with_ttl(key: str, value: str = "1", ttl_sec: int = 14 * 24 * 3600) -> bool:
    """
    Устанавливает ключ, только если он ещё не существует (NX) + TTL.
    Возвращает True, если ключ был создан, и False — если уже существовал.
    """
    try:
        # redis>=5 asyncio API: set(..., ex=seconds, nx=True) → True/False
        return bool(await _redis.set(key, value, ex=ttl_sec, nx=True))
    except Exception as e:
        LOG.warning("set_nx_with_ttl(%s) failed: %s", key, e)
        return False


async def invalidate_payment_ok_cache(user_id: int) -> None:
    """
    Сбрасывает кэш платёжной пригодности пользователя.
    Основной ключ:    payment_ok:{user_id}
    На всякий случай: {prefix}:payment_ok:{user_id} (если где-то использовали с префиксом).
    """
    try:
        # без префикса (как в ТЗ)
        await _redis.delete(f"payment_ok:{user_id}")
        # с префиксом (на случай использования общего префикса приложения)
        pref = os.getenv("REDIS_PREFIX", "sa")
        await _redis.delete(f"{pref}:payment_ok:{user_id}")
    except Exception as e:
        LOG.warning("invalidate_payment_ok_cache failed for user %s: %s", user_id, e)


class FeedbackRedisRepo:
    """
    Мини-репозиторий для хранения статуса и промежуточных данных флоу отзывов.
    Данные кладём в Hash (HSET), потоки/чанки — в List (RPUSH).
    Ключи:
      - {prefix}:fb:{user_id}          — Hash с основными полями
      - {prefix}:fb:{user_id}:buffer   — List с промежуточными чанками (если нужно)
    """

    def __init__(self, redis: Redis, prefix: str = "sa"):
        self.r = redis
        self.prefix = prefix

    def _key(self, user_id: int) -> str:
        return f"{self.prefix}:fb:{user_id}"

    def _buf_key(self, user_id: int, buf_name: str = "buffer") -> str:
        return f"{self._key(user_id)}:{buf_name}"

    # ---- lifecycle ----
    async def start(self, user_id: int, *, ttl: int = 86400, meta: Optional[Dict[str, Any]] = None) -> None:
        k = self._key(user_id)
        now = int(time.time())
        mapping: Dict[str, Any] = {"status": "start", "updated_at": now}
        if meta:
            mapping["meta"] = json.dumps(meta, ensure_ascii=False)
        pipe = self.r.pipeline()
        pipe.hset(k, mapping=mapping)
        pipe.expire(k, ttl)
        await pipe.execute()

    async def finish(self, user_id: int, *, ttl: int = 7 * 86400) -> None:
        await self.set_fields(user_id, {"status": "done"})
        # Длиннее храним финальный результат
        await self.r.expire(self._key(user_id), ttl)

    async def clear(self, user_id: int) -> None:
        k = self._key(user_id)
        pipe = self.r.pipeline()
        pipe.delete(k)
        pipe.delete(self._buf_key(user_id))
        await pipe.execute()

    # ---- fields ----
    async def set_stage(self, user_id: int, stage: str) -> None:
        k = self._key(user_id)
        await self.r.hset(k, mapping={"stage": stage, "updated_at": int(time.time())})

    async def set_fields(self, user_id: int, mapping: Dict[str, Any], *, ttl: Optional[int] = None) -> None:
        """
        Сохраняем пачку полей. dict/list сериализуем в JSON, остальные в str.
        """
        k = self._key(user_id)
        prepared: Dict[str, str] = {}
        for f, v in (mapping or {}).items():
            if isinstance(v, (dict, list)):
                prepared[f] = json.dumps(v, ensure_ascii=False)
            else:
                prepared[f] = "" if v is None else str(v)
        prepared["updated_at"] = str(int(time.time()))
        pipe = self.r.pipeline()
        pipe.hset(k, mapping=prepared)
        if ttl:
            pipe.expire(k, ttl)
        await pipe.execute()

    async def set_error(self, user_id: int, error: str) -> None:
        await self.set_fields(user_id, {"status": "error", "error": error})

    async def snapshot(self, user_id: int) -> Dict[str, Any]:
        """
        Возвращаем все поля hash + раскодируем известные JSON-поля.
        """
        k = self._key(user_id)
        h = await self.r.hgetall(k)
        if not h:
            return {}
        for f in ("meta", "payload", "variants_json"):
            if f in h and h[f]:
                try:
                    h[f] = json.loads(h[f])
                except Exception:
                    pass
        return h

    # ---- buffers (опционально) ----
    async def append_chunk(self, user_id: int, chunk: str, *, buf_name: str = "buffer") -> int:
        """
        RPUSH в буфер (например, для стриминга генерации).
        Возвращает новую длину списка.
        """
        n = await self.r.rpush(self._buf_key(user_id, buf_name), chunk or "")
        await self.r.hset(self._key(user_id), mapping={"updated_at": int(time.time())})
        return n

    async def read_buffer(self, user_id: int, *, buf_name: str = "buffer") -> List[str]:
        return await self.r.lrange(self._buf_key(user_id, buf_name), 0, -1)


# === Summary (саммари переговоров) ===========================================
class SummaryRedisRepo:
    """
    Хранение черновика саммари в Redis (Hash + TTL).
    Ключи:
      - {prefix}:sum:{user_id}              — Hash с полями:
          status, stage, updated_at
          input_json        — {"type":"text","text":"..."} | {"type":"audio","local_path":"...","telegram":{...}}
          last_payload      — payload, который отправляли исполнителю
          last_result       — финальный результат анализа
          meta              — произвольная мета (JSON)
      - при необходимости можно использовать списки-буферы как в FeedbackRedisRepo
    """

    def __init__(self, redis: Redis, prefix: str = "sa"):
        self.r = redis
        self.prefix = prefix

    def _key(self, user_id: int) -> str:
        return f"{self.prefix}:sum:{user_id}"

    # ---- lifecycle ----
    async def start(self, user_id: int, *, ttl: int = 86400, meta: Optional[Dict[str, Any]] = None) -> None:
        k = self._key(user_id)
        now = int(time.time())
        mapping: Dict[str, Any] = {"status": "start", "stage": "idle", "updated_at": now}
        if meta:
            mapping["meta"] = json.dumps(meta, ensure_ascii=False)
        pipe = self.r.pipeline()
        pipe.hset(k, mapping=mapping)
        pipe.expire(k, ttl)
        await pipe.execute()

    async def clear(self, user_id: int) -> None:
        await self.r.delete(self._key(user_id))

    async def finish(self, user_id: int, *, ttl: int = 7 * 86400) -> None:
        # Продлим жизнь записи с результатом
        await self.r.hset(self._key(user_id), mapping={"status": "done", "updated_at": int(time.time())})
        await self.r.expire(self._key(user_id), ttl)

    async def set_stage(self, user_id: int, stage: str) -> None:
        await self.r.hset(self._key(user_id), mapping={"stage": stage, "updated_at": int(time.time())})

    # ---- input: text / audio ----
    async def set_input_text(self, user_id: int, text: str, *, append: bool = False) -> None:
        k = self._key(user_id)
        cur_raw = await self.r.hget(k, "input_json")
        if append and cur_raw:
            try:
                cur = json.loads(cur_raw)
            except Exception:
                cur = {}
            prev = (cur.get("text") or "") if (cur.get("type") == "text") else ""
            text = ((prev + "\n" + (text or "")).strip()) if prev else (text or "")
        input_obj = {"type": "text", "text": text or ""}
        await self.r.hset(k, mapping={"input_json": json.dumps(input_obj, ensure_ascii=False), "updated_at": int(time.time())})

    async def set_input_audio(self, user_id: int, *, local_path: str, telegram_meta: Optional[Dict[str, Any]] = None) -> None:
        input_obj = {"type": "audio", "local_path": local_path}
        if telegram_meta:
            input_obj["telegram"] = telegram_meta
        await self.r.hset(self._key(user_id), mapping={
            "input_json": json.dumps(input_obj, ensure_ascii=False),
            "updated_at": int(time.time())
        })

    # ---- payload/result ----
    async def set_last_payload(self, user_id: int, payload: Dict[str, Any]) -> None:
        await self.r.hset(self._key(user_id), mapping={
            "last_payload": json.dumps(payload, ensure_ascii=False),
            "updated_at": int(time.time())
        })

    async def set_last_result(self, user_id: int, result: Dict[str, Any]) -> None:
        await self.r.hset(self._key(user_id), mapping={
            "last_result": json.dumps(result, ensure_ascii=False),
            "updated_at": int(time.time())
        })

    async def set_error(self, user_id: int, error: str) -> None:
        await self.r.hset(self._key(user_id), mapping={"status": "error", "error": error, "updated_at": int(time.time())})

    # ---- чтение ----
    async def get_draft(self, user_id: int) -> Dict[str, Any]:
        """
        Возвращает нормализованный dict с декодированными JSON-полями.
        """
        h = await self.r.hgetall(self._key(user_id))
        if not h:
            return {}
        out: Dict[str, Any] = {
            "status": h.get("status"),
            "stage": h.get("stage"),
            "updated_at": int(h.get("updated_at") or 0) if h.get("updated_at") else None,
        }
        for f, dst in (("input_json", "input"), ("last_payload", "last_payload"), ("last_result", "last_result"), ("meta", "meta")):
            if f in h and h[f]:
                try:
                    out[dst] = json.loads(h[f])
                except Exception:
                    out[dst] = h[f]
        # совместимость: если нет input_json, вернём пустой input
        if "input" not in out:
            out["input"] = {}
        return out


# === Quota (ограничения на количество действий в скользящем окне) ============
class QuotaRedisRepo:
    """
    Лёгкий лимитер на Redis с ZSET и скользящим окном.
    Ключ: {prefix}:q:{scope}:{user_id}  (ZSET с таймстемпами попыток)

    Поддерживает N попыток за window_sec (например, 3 за 86400 секунд).
    """
    def __init__(self, redis: Redis, prefix: str = "sa"):
        self.r = redis
        self.prefix = prefix

    def _key(self, user_id: int, scope: str) -> str:
        return f"{self.prefix}:q:{scope}:{user_id}"

    async def _purge_old(self, key: str, *, now_ts: Optional[int], window_sec: int) -> None:
        now_ts = _now_ts() if now_ts is None else now_ts
        cutoff = now_ts - window_sec
        # Удаляем всё, что старше окна
        await self.r.zremrangebyscore(key, 0, cutoff)

    async def get_count(self, user_id: int, *, scope: str, window_sec: int = 86400) -> int:
        key = self._key(user_id, scope)
        await self._purge_old(key, now_ts=None, window_sec=window_sec)
        return await self.r.zcard(key)

    async def try_consume(
        self,
        user_id: int,
        *,
        scope: str,
        limit: int,
        window_sec: int = 86400,
        now_ts: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        """
        Пытаемся «потратить» один токен.
        Возвращает (ok, remaining, reset_at_ts).
          ok          — можно ли выполнять действие сейчас
          remaining   — сколько попыток осталось в окне после (успешного) расхода; если ok=False — сколько осталось (0)
          reset_at_ts — когда полностью снимется блок (секундный UNIX ts)
        """
        now_ts = _now_ts() if now_ts is None else now_ts
        key = self._key(user_id, scope)
        pipe = self.r.pipeline()
        # 1) подчистим окно
        pipe.zremrangebyscore(key, 0, now_ts - window_sec)
        # 2) узнаем текущий размер
        pipe.zcard(key)
        res = await pipe.execute()
        cur = int(res[1] or 0)

        if cur >= limit:
            # вычислим ближайший reset_at: это min timestamp в ZSET + window_sec
            oldest = await self.r.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_ts = int(oldest[0][1])
                reset_at = oldest_ts + window_sec
            else:
                reset_at = now_ts + window_sec
            return False, 0, reset_at

        # есть квота — записываем попытку
        pipe = self.r.pipeline()
        pipe.zadd(key, {str(now_ts): now_ts})
        # TTL как страховка (чуть больше окна)
        pipe.expire(key, window_sec + 3600)
        await pipe.execute()
        remaining = max(0, limit - (cur + 1))
        # reset_at по самой старой записи после добавления
        oldest = await self.r.zrange(key, 0, 0, withscores=True)
        reset_at = (int(oldest[0][1]) + window_sec) if oldest else (now_ts + window_sec)
        return True, remaining, reset_at


# Глобальные экземпляры
feedback_repo = FeedbackRedisRepo(_redis, prefix=os.getenv("REDIS_PREFIX", "sa"))
summary_repo  = SummaryRedisRepo(_redis,  prefix=os.getenv("REDIS_PREFIX", "sa"))
quota_repo    = QuotaRedisRepo(_redis,    prefix=os.getenv("REDIS_PREFIX", "sa"))


# === YooKassa Webhook Idempotency ============================================
class YooWebhookDedupRepo:
    """
    Идемпотентность wh-событий YooKassa по payment_id.
    Ключ: {prefix}:yk:pay:{payment_id}  (HASH)
      поля: status, updated_at
    TTL: 144 часа (6 суток)
    Политика:
      - нет ключа        -> записываем new_status, allow=True
      - status == new    -> allow=False
      - status == 'waiting_for_capture' и new in {'succeeded','canceled','expired'} -> обновляем, allow=True
      - иначе (ранг(new) <= ранг(old)) -> allow=False
    """
    STATUS_RANK = {
        "waiting_for_capture": 1,
        "succeeded": 2,
        "canceled": 2,   # финальный
        "expired": 2,    # финальный
    }

    def __init__(self, redis: Redis, prefix: str = "sa", ttl_sec: int = 144 * 3600):
        self.r = redis
        self.prefix = prefix
        self.ttl = ttl_sec

    def _key(self, payment_id: str) -> str:
        return f"{self.prefix}:yk:pay:{payment_id}"

    @asynccontextmanager
    async def _watched(self, key: str):
        pipe = self.r.pipeline()
        await pipe.watch(key)
        try:
            yield pipe
        finally:
            await pipe.reset()

    async def should_process(self, payment_id: str, new_status: str) -> bool:
        """
        Атомично решает, надо ли обрабатывать событие.
        Также обновляет HASH и TTL, если решение = True (CAS через WATCH/MULTI).
        """
        key = self._key(payment_id)
        new_status = (new_status or "").strip().lower()
        new_rank = self.STATUS_RANK.get(new_status, 0)
        async with self._watched(key) as pipe:
            cur = await self.r.hget(key, "status")
            cur_status = (cur or "").strip().lower()
            if not cur_status:
                # первый раз видим этот payment_id
                pipe.multi()
                pipe.hset(key, mapping={"status": new_status, "updated_at": _now_ts()})
                pipe.expire(key, self.ttl)
                await pipe.execute()
                return True

            if cur_status == new_status:
                return False

            cur_rank = self.STATUS_RANK.get(cur_status, 0)
            # Разрешаем апгрейд статуса (например, waiting_for_capture -> succeeded/…)
            if new_rank > cur_rank:
                pipe.multi()
                pipe.hset(key, mapping={"status": new_status, "updated_at": _now_ts()})
                pipe.expire(key, self.ttl)
                await pipe.execute()
                return True

            # Иначе — статус «не новее», повторная обработка не нужна
            return False


# Глобальный экземпляр
yookassa_dedup = YooWebhookDedupRepo(_redis, prefix=os.getenv("REDIS_PREFIX", "sa"))