# smart_agent/bot/utils/redis_repo.py
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

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


# Глобальные экземпляры
feedback_repo = FeedbackRedisRepo(_redis, prefix=os.getenv("REDIS_PREFIX", "sa"))
summary_repo  = SummaryRedisRepo(_redis,  prefix=os.getenv("REDIS_PREFIX", "sa"))
