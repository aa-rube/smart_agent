# smart_agent/bot/utils/billing_db.py
#I'm using MYSQL8+ for this proj.
from __future__ import annotations

from typing import Optional, Any, List, Dict
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine, text, inspect,
    String, Integer, BigInteger, ForeignKey, DateTime, Text,
    or_, select
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
)

from bot.config import DB_URL  # <— общий DSN для биллинга
from bot.utils.redis_repo import _redis as _redis_client  # используем уже настроенный Redis из проекта
from bot.utils.time_helpers import (
    now_msk, to_aware_msk, to_utc_for_db, from_db_naive
)
import json


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    eng = create_engine(
        DB_URL,
        future=True,
        echo=False,
        pool_pre_ping=True,
    )
    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# =========================
#          Models
# =========================
class Subscription(Base):
    """
    Подписки (рекуррентные списания).
    """
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)

    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)               # 1m / 3m / 12m
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_value: Mapped[str] = mapped_column(String(32), nullable=False)            # "19900.00"
    amount_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")

    # Храним провайдерский токен карты (строка от YooKassa)
    payment_method_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")   # active|canceled
    next_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # новый слой защиты ретраев:
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # троттлинг уведомлений:
    last_fail_notice_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    __table_args__ = (
        # быстрые выборки по дью и статусам
        {'sqlite_autoincrement': True},
    )


class PaymentMethod(Base):
    """
    Привязанные платёжные методы (карты).
    """
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="yookassa")
    provider_pm_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)  # токен провайдера

    brand: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 'VISA', 'MC', 'Mir'
    first6: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    exp_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exp_year:  Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ChargeAttempt(Base):
    """
    Попытки авто-списаний (для лимита ретраев).
    """
    __tablename__ = "charge_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # id платежа у провайдера
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")  # created|succeeded|canceled|expired
    # Якорь "одного платежа/цикла": значение next_charge_at на момент ПЕРВОЙ попытки этого цикла
    # Все попытки с одинаковым due_at относятся к одной "истории платежа"
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)
    __table_args__ = (
        # для быстрых окон по попыткам
        {},
    )


class PaymentLog(Base):
    """
    Журнал webhook-событий провайдера (идемпотентность по payment_id).
    """
    __tablename__ = "payment_log"

    payment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id:    Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    amount_value:    Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    amount_currency: Mapped[Optional[str]] = mapped_column(String(8),  nullable=True)
    event:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    metadata_json:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# =========================
#       Repository
# =========================
def init_schema() -> None:
    Base.metadata.create_all(bind=engine)
    # Нормализованная миграция: проверяем через Inspector и добавляем отсутствующие поля/индексы.
    with engine.begin() as conn:
        insp = inspect(conn)

        # ---- subscriptions: колонки ----
        subs_cols = {c["name"] for c in insp.get_columns("subscriptions")}
        dialect = conn.dialect.name  # 'mysql' | 'postgresql' | 'sqlite' | ...

        # Подбираем типы колонок для разных СУБД (без TIMESTAMPTZ для MySQL/SQLite)
        dt_type = "TIMESTAMPTZ" if dialect in ("postgresql",) else "DATETIME"
        int_type = "INTEGER"

        if "last_attempt_at" not in subs_cols:
            conn.exec_driver_sql(f"ALTER TABLE subscriptions ADD COLUMN last_attempt_at {dt_type} NULL")
        if "consecutive_failures" not in subs_cols:
            # MySQL требует DEFAULT прямо в ADD COLUMN
            default_expr = "DEFAULT 0" if dialect in ("mysql",) else "DEFAULT 0"
            conn.exec_driver_sql(f"ALTER TABLE subscriptions ADD COLUMN consecutive_failures {int_type} NOT NULL {default_expr}")
        if "last_fail_notice_at" not in subs_cols:
            conn.exec_driver_sql(f"ALTER TABLE subscriptions ADD COLUMN last_fail_notice_at {dt_type} NULL")

        # ---- subscriptions: индексы ----
        subs_indexes = {ix["name"] for ix in insp.get_indexes("subscriptions")}
        if "idx_sub_status_next" not in subs_indexes:
            conn.exec_driver_sql("CREATE INDEX idx_sub_status_next ON subscriptions (status, next_charge_at)")
        if "idx_sub_user_status" not in subs_indexes:
            conn.exec_driver_sql("CREATE INDEX idx_sub_user_status ON subscriptions (user_id, status)")

        # ---- charge_attempts: индексы ----
        attempts_indexes = {ix["name"] for ix in insp.get_indexes("charge_attempts")}
        if "idx_attempt_sub_time" not in attempts_indexes:
            conn.exec_driver_sql("CREATE INDEX idx_attempt_sub_time ON charge_attempts (subscription_id, attempted_at)")
        # Новое поле для привязки попыток к текущему платежному циклу
        attempts_cols = {c["name"] for c in insp.get_columns("charge_attempts")}
        if "due_at" not in attempts_cols:
            conn.exec_driver_sql(f"ALTER TABLE charge_attempts ADD COLUMN due_at {dt_type} NULL")
        # Индекс по (subscription_id, due_at) для быстрых подсчётов попыток "в рамках одного платежа"
        if "idx_attempt_sub_due" not in attempts_indexes:
            conn.exec_driver_sql("CREATE INDEX idx_attempt_sub_due ON charge_attempts (subscription_id, due_at)")


class BillingRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # ──────────────────────────────────────────────────────────────────────
    # Trial starts (по факту — момент первой привязки карты)
    # ──────────────────────────────────────────────────────────────────────
    def get_trial_started_at(self, user_id: int) -> Optional[datetime]:
        """
        Возвращает UTC-время первой привязки карты (created_at первой не-удалённой записи).
        Это считается стартом триала.
        """
        with self._session() as s:
            rec = (
                s.query(PaymentMethod.created_at)
                 .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                 .order_by(PaymentMethod.created_at.asc())
                 .first()
            )
            if not rec:
                return None
            dt: datetime = rec[0]
            return from_db_naive(dt)

    def list_trial_started_map(self, user_ids: List[int]) -> Dict[int, datetime]:
        """
        Для пачки пользователей возвращает {user_id: trial_started_at_utc}.
        Берём МИНИМАЛЬНЫЙ created_at по не-удалённым картам.
        """
        if not user_ids:
            return {}
        with self._session() as s:
            rows = (
                s.query(PaymentMethod.user_id, PaymentMethod.created_at)
                 .filter(PaymentMethod.user_id.in_(user_ids), PaymentMethod.deleted_at.is_(None))
                 .order_by(PaymentMethod.user_id.asc(), PaymentMethod.created_at.asc())
                 .all()
            )
            out: Dict[int, datetime] = {}
            for uid, created_at in rows:
                if uid not in out:
                    out[uid] = from_db_naive(created_at)
            return out

    def precharge_guard_and_attempt(self, *, subscription_id: int, now: datetime, user_id: int) -> Optional[int]:
        """
        В одной транзакции: перечитать подписку FOR UPDATE, проверить щиты,
        записать ChargeAttempt(status='created'), обновить last_attempt_at.
        Возвращает id попытки или None, если щит не пропустил.
        now должен быть в МСК.
        """
        # Нормализуем now к МСК
        from bot.config import TIMEZONE
        now = to_aware_msk(now) if now.tzinfo is None else now.astimezone(TIMEZONE)
        
        with self._session() as s, s.begin():
            rec: Subscription | None = s.query(Subscription).with_for_update().filter(Subscription.id == subscription_id).one_or_none()
            if rec is None or rec.status != "active" or rec.payment_method_id is None:
                return None
            # щит: макс фейлов В ТЕКУЩЕМ ЦИКЛЕ (consecutive_failures копится с последнего успеха)
            if (rec.consecutive_failures or 0) >= 6:
                return None
            # щит: пауза 12ч (last_attempt_at может быть naive → нормализуем к МСК)
            last_attempt_aware = from_db_naive(rec.last_attempt_at)
            if last_attempt_aware is not None and (now - last_attempt_aware) < timedelta(hours=12):
                return None
            # щит: 2 попытки/24ч
            since_24h = now - timedelta(hours=24)
            # Конвертируем для сравнения с БД (БД хранит в UTC)
            since_24h_utc = to_utc_for_db(since_24h)
            cnt_24h = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id == subscription_id,
                ChargeAttempt.attempted_at >= since_24h_utc
            ).count()
            if cnt_24h >= 2:
                return None
            # due_anchor — это "какой платёж сейчас пытаемся закрыть" (значение next_charge_at на момент первой попытки)
            # Проверяем существующие незавершённые попытки для этой подписки
            existing_attempt = (
                s.query(ChargeAttempt)
                .filter(
                    ChargeAttempt.subscription_id == subscription_id,
                    ChargeAttempt.status == "created"
                )
                .order_by(ChargeAttempt.attempted_at.desc())
                .first()
            )
            if existing_attempt and existing_attempt.due_at:
                # Используем due_at из существующей попытки
                due_anchor = from_db_naive(existing_attempt.due_at)
            else:
                # Используем текущий next_charge_at, округляем до секунд
                due_anchor_raw = from_db_naive(rec.next_charge_at)
                if due_anchor_raw:
                    due_anchor = due_anchor_raw.replace(microsecond=0)
                else:
                    due_anchor = None
            # записываем попытку и след на подписке (конвертируем в UTC для БД)
            attempt = ChargeAttempt(
                subscription_id=subscription_id,
                user_id=user_id,
                payment_id=None,
                status="created",
                attempted_at=to_utc_for_db(now),
                due_at=to_utc_for_db(due_anchor) if due_anchor else None,
            )
            s.add(attempt)
            rec.last_attempt_at = to_utc_for_db(now)
            rec.updated_at = to_utc_for_db(now)
            s.flush()
            return attempt.id

    def link_payment_to_attempt(self, *, attempt_id: int, payment_id: str) -> None:
        with self._session() as s, s.begin():
            rec = s.query(ChargeAttempt).filter(ChargeAttempt.id == attempt_id).one_or_none()
            if rec:
                rec.payment_id = payment_id
            else:
                import logging
                logging.warning("ChargeAttempt with id=%s not found when linking payment_id=%s", attempt_id, payment_id)

    def list_mailing_eligible_users(self) -> List[int]:
        """
        Пользователи с ПРИВЯЗАННОЙ картой и активной подпиской, у которой не исчерпан лимит фейлов:
          - subscriptions.status == 'active'
          - subscriptions.payment_method_id IS NOT NULL  (токен провайдера известен)
          - subscriptions.consecutive_failures < 6       (или NULL трактуем как 0)
          - существует PaymentMethod(user_id) с deleted_at IS NULL
        next_charge_at НЕ проверяем — такие пользователи должны получать контент,
        даже если платёж просрочен и идут ретраи.
        """
        with self._session() as s:
            sub_q = (
                s.query(Subscription.user_id)
                 .filter(
                     Subscription.status == "active",
                     Subscription.payment_method_id.isnot(None),
                     or_(Subscription.consecutive_failures.is_(None), Subscription.consecutive_failures < 6),
                 )
                 .group_by(Subscription.user_id)
                 .subquery()
            )
            rows = (
                s.query(PaymentMethod.user_id)
                 .filter(
                     PaymentMethod.deleted_at.is_(None),
                     PaymentMethod.user_id.in_(select(sub_q.c.user_id)),
                 )
                 .group_by(PaymentMethod.user_id)
                 .all()
            )
            return [uid for (uid,) in rows]

    def is_user_payment_ok(self, user_id: int) -> bool:
        """
        Быстрая проверка «можно ли слать рассылку» пользователю ИЗ ПЛАТЁЖНОЙ ПЛОСКОСТИ:
          - есть привязанная карта (не удалена)
          - есть активная подписка со связанным payment_method_id
          - consecutive_failures < 6
        Результат кэшируется в Redis на 3 часа (ключ: payment_ok:{user_id}).
        """
        key = f"payment_ok:{user_id}"
        try:
            cached = _redis_client and _redis_client.sync_get(key)  # redis>=5: есть sync_* методы у клиента
        except Exception:
            cached = None

        if cached is not None:
            return str(cached) == "1"

        with self._session() as s:
            has_card = (
                s.query(PaymentMethod.id)
                 .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                 .first()
                is not None
            )
            if not has_card:
                ok = False
            else:
                ok = (
                    s.query(Subscription.id)
                     .filter(
                         Subscription.user_id == user_id,
                         Subscription.status == "active",
                         Subscription.payment_method_id.isnot(None),
                         or_(Subscription.consecutive_failures.is_(None), Subscription.consecutive_failures < 6),
                     )
                     .first()
                    is not None
                )

        # кэш 3 часа
        try:
            if _redis_client:
                _redis_client.sync_setex(key, 10800, "1" if ok else "0")
        except Exception:
            pass
        return ok

    # --- cards ---
    def has_saved_card(self, user_id: int) -> bool:
        with self._session() as s:
            return (
                s.query(PaymentMethod)
                .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                .first()
                is not None
            )

    def get_user_card(self, user_id: int) -> Optional[dict]:
        with self._session() as s:
            rec = (
                s.query(PaymentMethod)
                .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                .order_by(PaymentMethod.id.desc())
                .first()
            )
            if not rec:
                return None
            return {
                "payment_method_id": rec.id,
                "provider": rec.provider,
                "brand": rec.brand or "",
                "first6": rec.first6 or "",
                "last4": rec.last4 or "",
                "exp_month": rec.exp_month,
                "exp_year": rec.exp_year,
            }

    def list_user_payment_methods(self, user_id: int) -> List[Dict[str, Optional[str]]]:
        """
        Возвращает все не-удалённые платёжные методы пользователя.
        Поля: provider ('bank_card' | 'sbp' | ...), brand, last4, provider_pm_token.
        """
        with self._session() as s:
            rows = (
                s.query(PaymentMethod.provider, PaymentMethod.brand, PaymentMethod.last4, PaymentMethod.provider_pm_token)
                 .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                 .all()
            )
            out: List[Dict[str, Optional[str]]] = []
            for provider, brand, last4, token in rows:
                out.append({
                    "provider": provider,
                    "brand": brand or "",
                    "last4": last4 or "",
                    "provider_pm_token": token or "",
                })
            return out

    def has_saved_sbp(self, user_id: int) -> bool:
        """Есть ли у пользователя активный рекуррентный токен СБП."""
        with self._session() as s:
            return (
                s.query(PaymentMethod)
                 .filter(
                     PaymentMethod.user_id == user_id,
                     PaymentMethod.deleted_at.is_(None),
                     PaymentMethod.provider == "sbp",
                 )
                 .first()
                is not None
            )

    def delete_user_sbp_and_detach_subscriptions(self, *, user_id: int) -> int:
        """
        Мягко удаляет ВСЕ активные СБП-токены (provider='sbp') и отвязывает их от активных подписок
        только если payment_method_id совпадает с удаляемыми токенами.
        Возвращает количество затронутых подписок.
        """
        with self._session() as s, s.begin():
            now_msk_val = now_msk()
            now = to_utc_for_db(now_msk_val)  # Для БД храним в UTC
            # какие СБП-токены удаляем
            sbp_tokens = [
                token for (token,) in
                s.query(PaymentMethod.provider_pm_token)
                 .filter(
                     PaymentMethod.user_id == user_id,
                     PaymentMethod.deleted_at.is_(None),
                     PaymentMethod.provider == "sbp",
                 )
                 .all()
            ]
            # пометить как удалённые
            for pm in s.query(PaymentMethod).filter(
                PaymentMethod.user_id == user_id,
                PaymentMethod.deleted_at.is_(None),
                PaymentMethod.provider == "sbp",
            ).all():
                pm.deleted_at = now

            if not sbp_tokens:
                return 0

            # отвязать у подписок только эти токены
            cnt = 0
            for sub in s.query(Subscription).filter(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.payment_method_id.in_(sbp_tokens),
            ).all():
                sub.payment_method_id = None
                sub.updated_at = now
                cnt += 1
            return cnt

    def card_upsert_from_provider(
        self,
        *,
        user_id: int,
        provider: str,
        pm_token: str,
        brand: Optional[str],
        first6: Optional[str],
        last4: Optional[str],
        exp_month: Optional[int],
        exp_year: Optional[int],
    ) -> int:
        with self._session() as s, s.begin():
            rec = (
                s.query(PaymentMethod)
                .filter(PaymentMethod.provider_pm_token == pm_token)
                .one_or_none()
            )
            if rec is None:
                rec = PaymentMethod(
                    user_id=user_id, provider=provider, provider_pm_token=pm_token,
                    brand=brand, first6=first6, last4=last4,
                    exp_month=exp_month, exp_year=exp_year,
                )
                s.add(rec)
                s.flush()
                return rec.id
            # обновляем и «воскрешаем»
            rec.user_id = user_id
            rec.provider = provider or rec.provider
            rec.brand = brand or rec.brand
            rec.first6 = first6 or rec.first6
            rec.last4 = last4 or rec.last4
            rec.exp_month = exp_month or rec.exp_month
            rec.exp_year = exp_year or rec.exp_year
            rec.deleted_at = None
            s.flush()
            return rec.id

    def delete_user_card_and_detach_subscriptions(self, *, user_id: int) -> int:
        """
        Мягко удаляет ВСЕ активные карты и отвязывает их от активных подписок.
        Возвращает количество затронутых подписок.
        """
        with self._session() as s, s.begin():
            now_msk_val = now_msk()
            now = to_utc_for_db(now_msk_val)  # Для БД храним в UTC
            # пометить все карты как deleted
            for pm in s.query(PaymentMethod).filter(
                PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None)
            ).all():
                pm.deleted_at = now

            # обнулить ссылку на карту у активных подписок
            cnt = 0
            for sub in s.query(Subscription).filter(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.payment_method_id.isnot(None),
            ).all():
                sub.payment_method_id = None
                sub.updated_at = now
                cnt += 1
            return cnt

    # --- subscriptions ---
    def subscription_upsert(
        self,
        *,
        user_id: int,
        plan_code: str,
        interval_months: int,
        amount_value: str,
        amount_currency: str = "RUB",
        payment_method_id: Optional[str],
        next_charge_at: Optional[datetime],
        status: str = "active",
        update_payment_method: bool = True,
    ) -> int:
        """
        Создаёт или обновляет подписку.
        Использует SELECT FOR UPDATE для защиты от race condition.
        update_payment_method: если False и payment_method_id=None, не обновляет существующую карту.
        """
        with self._session() as s, s.begin():
            # Проверяем все активные подписки пользователя с этим plan_code для защиты от дублей
            rec = (
                s.query(Subscription)
                .with_for_update()
                .filter(
                    Subscription.user_id == user_id, 
                    Subscription.plan_code == plan_code,
                    Subscription.status == "active"
                )
                .first()
            )
            # Если нет активной, проверяем любую (для обновления canceled)
            if not rec:
                rec = (
                    s.query(Subscription)
                    .with_for_update()
                    .filter(Subscription.user_id == user_id, Subscription.plan_code == plan_code)
                    .first()
                )
            if rec is None:
                # Конвертируем next_charge_at в UTC для БД если передан
                next_charge_at_utc = None
                if next_charge_at is not None:
                    next_charge_at_utc = to_utc_for_db(to_aware_msk(next_charge_at))
                rec = Subscription(
                    user_id=user_id,
                    plan_code=plan_code,
                    interval_months=interval_months,
                    amount_value=amount_value,
                    amount_currency=amount_currency,
                    payment_method_id=payment_method_id,
                    next_charge_at=next_charge_at_utc,
                    status=status,
                )
                s.add(rec)
                s.flush()
                return rec.id
            else:
                rec.interval_months = interval_months
                rec.amount_value = amount_value
                rec.amount_currency = amount_currency or rec.amount_currency
                # Не обновляем payment_method_id если передан None и карта уже привязана (если update_payment_method=False)
                if update_payment_method:
                    rec.payment_method_id = payment_method_id
                elif payment_method_id is not None:
                    # Явно передан новый payment_method_id - обновляем
                    rec.payment_method_id = payment_method_id
                # Иначе (payment_method_id=None и update_payment_method=False) - не трогаем существующую карту
                # Конвертируем next_charge_at в UTC для БД если передан
                if next_charge_at is not None:
                    rec.next_charge_at = to_utc_for_db(to_aware_msk(next_charge_at))
                else:
                    rec.next_charge_at = next_charge_at
                rec.status = status
                rec.updated_at = to_utc_for_db(now_msk())
                s.flush()
                return rec.id

    def subscription_cancel_for_user(self, *, user_id: int) -> int:
        with self._session() as s, s.begin():
            q = s.query(Subscription).filter(Subscription.user_id == user_id, Subscription.status == "active")
            updated = 0
            now_msk_val = now_msk()
            now = to_utc_for_db(now_msk_val)  # Для БД храним в UTC
            for rec in q:
                rec.status = "canceled"
                rec.cancel_at = now
                rec.updated_at = now
                rec.payment_method_id = None
                rec.next_charge_at = None
                updated += 1
            return updated

    def subscription_mark_charged(self, sub_id: int, *, next_charge_at: datetime) -> None:
        with self._session() as s, s.begin():
            rec = s.get(Subscription, sub_id)
            if rec:
                now_msk_val = now_msk()
                rec.last_charge_at = to_utc_for_db(now_msk_val)
                rec.next_charge_at = to_utc_for_db(to_aware_msk(next_charge_at))
                rec.updated_at = to_utc_for_db(now_msk_val)

    def subscription_mark_charged_for_user(
        self, 
        user_id: int, 
        *, 
        next_charge_at: datetime,
        subscription_id: Optional[int] = None,
        plan_code: Optional[str] = None
    ) -> Optional[int]:
        """
        Обновляет подписку после успешного платежа.
        next_charge_at должен быть в МСК.
        Если передан subscription_id - обновляет конкретную подписку (только если она active).
        Если передан plan_code - ищет по plan_code.
        Иначе - использует текущую логику (первая активная подписка).
        """
        import logging
        logger = logging.getLogger(__name__)
        
        with self._session() as s, s.begin():
            rec = None
            
            if subscription_id:
                rec = s.get(Subscription, subscription_id)
                if rec:
                    if rec.user_id != user_id:
                        logger.warning(
                            "subscription_mark_charged_for_user: subscription_id=%s belongs to different user_id=%s (expected %s)",
                            subscription_id, rec.user_id, user_id
                        )
                        rec = None
                    elif rec.status != "active":
                        logger.warning(
                            "subscription_mark_charged_for_user: subscription_id=%s has status=%s (expected 'active'), user_id=%s. "
                            "Will try fallback search.",
                            subscription_id, rec.status, user_id
                        )
                        rec = None
                        # Fallback: попробуем найти активную подписку по plan_code или по умолчанию
                        if plan_code:
                            rec = (
                                s.query(Subscription)
                                .filter(
                                    Subscription.user_id == user_id,
                                    Subscription.plan_code == plan_code,
                                    Subscription.status == "active"
                                )
                                .first()
                            )
                            if rec:
                                logger.info(
                                    "subscription_mark_charged_for_user: Found active subscription by plan_code=%s as fallback, user_id=%s",
                                    plan_code, user_id
                                )
                        if not rec:
                            # Последняя попытка: любая активная подписка пользователя
                            rec = (
                                s.query(Subscription)
                                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                                .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                                .first()
                            )
                            if rec:
                                logger.info(
                                    "subscription_mark_charged_for_user: Found active subscription by default fallback, user_id=%s, subscription_id=%s",
                                    user_id, rec.id
                                )
            elif plan_code:
                rec = (
                    s.query(Subscription)
                    .filter(
                        Subscription.user_id == user_id,
                        Subscription.plan_code == plan_code,
                        Subscription.status == "active"
                    )
                    .first()
                )
                if not rec:
                    logger.warning(
                        "subscription_mark_charged_for_user: No active subscription found for user_id=%s, plan_code=%s. "
                        "Trying default fallback.",
                        user_id, plan_code
                    )
                    # Fallback: любая активная подписка пользователя
                    rec = (
                        s.query(Subscription)
                        .filter(Subscription.user_id == user_id, Subscription.status == "active")
                        .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                        .first()
                    )
                    if rec:
                        logger.info(
                            "subscription_mark_charged_for_user: Found active subscription by default fallback, user_id=%s, subscription_id=%s (requested plan_code=%s)",
                            user_id, rec.id, plan_code
                        )
            else:
                rec = (
                    s.query(Subscription)
                    .filter(Subscription.user_id == user_id, Subscription.status == "active")
                    .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                    .first()
                )
            
            if not rec:
                logger.warning(
                    "subscription_mark_charged_for_user: No subscription found to update for user_id=%s, subscription_id=%s, plan_code=%s",
                    user_id, subscription_id, plan_code
                )
                return None
            
            logger.info(
                "subscription_mark_charged_for_user: Updating subscription_id=%s for user_id=%s, next_charge_at=%s",
                rec.id, user_id, next_charge_at
            )
            now_msk_val = now_msk()
            rec.last_charge_at = to_utc_for_db(now_msk_val)
            rec.next_charge_at = to_utc_for_db(to_aware_msk(next_charge_at))
            rec.updated_at = to_utc_for_db(now_msk_val)
            rec.consecutive_failures = 0  # Сбрасываем счётчик неудач
            s.flush()
            return rec.id

    def list_active_subscription_user_ids(self, now: Optional[datetime] = None) -> List[int]:
        """
        Все пользователи с активной подпиской, доступной на момент now:
        status='active' и next_charge_at > now (т.е.оплаченный доступ ещё не истёк).
        now должен быть в МСК.
        """
        now_msk = to_aware_msk(now) if now else now_msk()
        now_utc = to_utc_for_db(now_msk)  # Для сравнения с БД (БД хранит в UTC)
        with self._session() as s:
            rows = (
                s.query(Subscription.user_id)
                 .filter(
                    Subscription.status == "active",
                    Subscription.next_charge_at != None,   # noqa: E711
                    Subscription.next_charge_at > now_utc,
                 )
                 .group_by(Subscription.user_id)
                 .all()
            )
            return [uid for (uid,) in rows]

    # --- retries / attempts ---
    def record_charge_attempt(self, *, subscription_id: int, user_id: int, payment_id: Optional[str], status: str, due_at: Optional[datetime] = None) -> int:
        with self._session() as s, s.begin():
            # due_at должен быть в МСК, округляем до секунд и конвертируем в UTC для БД
            due_at_utc = None
            if due_at:
                due_at_msk = to_aware_msk(due_at).replace(microsecond=0)
                due_at_utc = to_utc_for_db(due_at_msk)
            rec = ChargeAttempt(
                subscription_id=subscription_id,
                user_id=user_id,
                payment_id=payment_id,
                status=status,
                due_at=due_at_utc,
                attempted_at=to_utc_for_db(now_msk()),
            )
            s.add(rec)
            s.flush()
            return rec.id

    def mark_charge_attempt_status(
        self, 
        *, 
        payment_id: Optional[str] = None,
        subscription_id: Optional[int] = None,
        status: str
    ) -> None:
        """
        Обновляет статус попытки списания.
        Ищет по payment_id, если не найден - ищет по subscription_id (для fallback).
        """
        with self._session() as s, s.begin():
            rec = None
            if payment_id:
                rec = s.query(ChargeAttempt).filter(ChargeAttempt.payment_id == payment_id).one_or_none()
            if not rec and subscription_id:
                # Fallback: ищем последнюю попытку для подписки
                rec = (
                    s.query(ChargeAttempt)
                    .filter(ChargeAttempt.subscription_id == subscription_id)
                    .order_by(ChargeAttempt.attempted_at.desc())
                    .first()
                )
            if rec:
                rec.status = status

    def subscriptions_due(self, *, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Возвращает подписки, требующие списания.
        now должен быть в МСК.
        """
        now_msk = to_aware_msk(now)
        now_utc = to_utc_for_db(now_msk)  # Для сравнения с БД (БД хранит в UTC)

        # Политика ретраев авто-списаний:
        # 1) Не чаще 2-х попыток в сутки (окно 24h).
        # 2) Минимальный интервал между попытками — 12 часов.
        # 3) Максимум 6 НЕуспешных попыток В РАМКАХ ОДНОГО ПЛАТЕЖНОГО ЦИКЛА
        #    (т.е. для той же пары subscription_id + due_at = next_charge_at),
        #    считаются только status IN ('canceled','expired').
        window_24h = timedelta(hours=24)
        min_gap = timedelta(hours=12)

        with self._session() as s:
            q = (
                s.query(Subscription)
                .filter(
                    Subscription.status == "active",
                    Subscription.next_charge_at != None,                     # noqa: E711
                    Subscription.next_charge_at <= now_utc,  # Сравниваем с UTC (БД хранит в UTC)
                    Subscription.payment_method_id != None,                  # noqa: E711
                )
                .order_by(Subscription.next_charge_at.asc())
                .limit(limit * 3)
            )
            # Некоторым диалектам не нравится параметризация LIMIT — подстрахуемся.
            try:
                subs = list(q.limit(int(limit * 3)))
            except Exception:
                subs = list(q)
            # Ограничение 2 попытки в сутки
            since_24h_msk = now_msk - window_24h
            since_24h_utc = to_utc_for_db(since_24h_msk)
            blocked_ids_day2 = {
                sub_id for (sub_id,) in
                s.query(ChargeAttempt.subscription_id)
                 .filter(ChargeAttempt.attempted_at >= since_24h_utc)
                 .group_by(ChargeAttempt.subscription_id)
                 .having(text("COUNT(*) >= 2"))
                 .all()
            }

            # Минимальный интервал 12 часов (любая последняя попытка, независимо от статуса)
            since_12h_msk = now_msk - min_gap
            since_12h_utc = to_utc_for_db(since_12h_msk)
            blocked_ids_gap12h = {
                sub_id for (sub_id,) in
                s.query(ChargeAttempt.subscription_id)
                 .filter(ChargeAttempt.attempted_at >= since_12h_utc)
                 .group_by(ChargeAttempt.subscription_id)
                 .all()
            }

            # Лимит 6 НЕуспешных попыток в рамках ТЕКУЩЕГО цикла (due_at == rec.next_charge_at)
            # Сначала соберём пары (sub_id, due_at) для кандидатов
            # Округляем due_at до секунд для сравнения
            candidate_pairs: List[tuple[int, Optional[datetime]]] = []
            for rec in subs:
                due_at_raw = from_db_naive(rec.next_charge_at)
                if due_at_raw:
                    due_at_rounded = due_at_raw.replace(microsecond=0)
                    candidate_pairs.append((rec.id, due_at_rounded))
                else:
                    candidate_pairs.append((rec.id, None))
            
            # Вычислим counts по всем парам разом: group by (subscription_id, due_at)
            from sqlalchemy import func
            failed_counts = {}
            if candidate_pairs:
                # Конвертируем due_at в UTC для сравнения с БД и округляем
                due_at_utc_set = {
                    to_utc_for_db(due_at).replace(microsecond=0) 
                    for (_, due_at) in candidate_pairs 
                    if due_at is not None
                }
                if due_at_utc_set:
                    failed_rows = (
                        s.query(
                            ChargeAttempt.subscription_id,
                            ChargeAttempt.due_at,
                            func.count("*")
                        )
                        .filter(
                            ChargeAttempt.status.in_(('canceled', 'expired')),
                            ChargeAttempt.subscription_id.in_([sid for (sid, _) in candidate_pairs]),
                            ChargeAttempt.due_at.in_(due_at_utc_set)
                        )
                        .group_by(ChargeAttempt.subscription_id, ChargeAttempt.due_at)
                        .all()
                    )
                    # Маппинг обратно на МСК для сравнения
                    for sid, due_at_utc, cnt in failed_rows:
                        due_at_msk = from_db_naive(due_at_utc)
                        if due_at_msk:
                            due_at_msk = due_at_msk.replace(microsecond=0)
                        # Находим соответствующую пару
                        for pair_sid, pair_due in candidate_pairs:
                            if pair_sid == sid:
                                if pair_due and due_at_msk:
                                    # Сравниваем округлённые значения
                                    if pair_due.replace(microsecond=0) == due_at_msk:
                                        failed_counts[(sid, pair_due)] = cnt
                                        break
                                elif pair_due is None and due_at_msk is None:
                                    failed_counts[(sid, None)] = cnt
                                    break

            items: List[Dict[str, Any]] = []
            for rec in subs:
                # Быстрые проверки на самой подписке — второй щит
                if rec.consecutive_failures is not None and rec.consecutive_failures >= 6:
                    # skip: max failures reached (>=6)
                    continue
                # last_attempt_at может быть naive → привести к aware МСК
                last_attempt_msk = from_db_naive(rec.last_attempt_at)
                if last_attempt_msk is not None and (now_msk - last_attempt_msk) < min_gap:
                    # skip: 12h gap not passed
                    continue
                # Лимиты по окнам + лимит 6 фейлов В ТЕКУЩЕМ ЦИКЛЕ
                # (по паре (subscription_id, due_at = rec.next_charge_at))
                next_charge_msk = from_db_naive(rec.next_charge_at)
                if next_charge_msk:
                    next_charge_msk = next_charge_msk.replace(microsecond=0)
                pair = (rec.id, next_charge_msk)
                failed6_now = failed_counts.get(pair, 0) >= 6
                if (rec.id in blocked_ids_day2) or (rec.id in blocked_ids_gap12h) or failed6_now:
                    continue
                items.append({
                    "id": rec.id,
                    "user_id": rec.user_id,
                    "plan_code": rec.plan_code,
                    "interval_months": rec.interval_months,
                    "amount_value": rec.amount_value,
                    "amount_currency": rec.amount_currency,
                    "payment_method_id": rec.payment_method_id,
                    "consecutive_failures": rec.consecutive_failures,
                    "last_attempt_at": rec.last_attempt_at,
                })
                if len(items) >= limit:
                    break
            return items

    # --- webhooks / log ---
    def payment_log_upsert(
        self,
        *,
        payment_id: str,
        user_id: Optional[int],
        amount_value: Optional[str],
        amount_currency: Optional[str],
        event: Optional[str],
        status: Optional[str],
        metadata: Optional[Dict[str, Any]],
        raw_payload: Optional[Dict[str, Any]],
    ) -> None:
        """
        Создаёт или обновляет запись в payment_log.
        Использует INSERT ... ON DUPLICATE KEY UPDATE для защиты от race condition.
        """
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        raw_payload_json = json.dumps(raw_payload or {}, ensure_ascii=False)
        with self._session() as s, s.begin():
            # Используем SQL INSERT ... ON DUPLICATE KEY UPDATE для атомарности
            # Это защищает от race condition при одновременной обработке webhook'ов
            from sqlalchemy.dialects.mysql import insert as mysql_insert
            # MySQL поддерживает INSERT ... ON DUPLICATE KEY UPDATE
            stmt = (
                mysql_insert(PaymentLog)
                .values(
                    payment_id=payment_id,
                    user_id=user_id,
                    amount_value=amount_value,
                    amount_currency=amount_currency or "RUB",
                    event=event,
                    status=status,
                    metadata_json=metadata_json,
                    raw_payload_json=raw_payload_json,
                    created_at=to_utc_for_db(now_msk()),
                )
                .on_duplicate_key_update(
                    user_id=user_id if user_id else PaymentLog.user_id,
                    amount_value=amount_value,
                    amount_currency=amount_currency or PaymentLog.amount_currency,
                    event=event or PaymentLog.event,
                    status=status or PaymentLog.status,
                    metadata_json=metadata_json,
                    raw_payload_json=raw_payload_json,
                )
            )
            s.execute(stmt)

    def payment_log_is_processed(self, payment_id: str) -> bool:
        with self._session() as s:
            rec = s.get(PaymentLog, payment_id)
            return bool(rec and rec.processed_at is not None)

    def payment_log_mark_processed(self, payment_id: str) -> None:
        with self._session() as s, s.begin():
            rec = s.get(PaymentLog, payment_id)
            if rec is None:
                rec = PaymentLog(payment_id=payment_id, processed_at=to_utc_for_db(now_msk()))
                s.add(rec)
            else:
                rec.processed_at = to_utc_for_db(now_msk())


# Глобальный репозиторий (billing DB)
_repo = BillingRepository(SessionLocal)
init_schema()

# ========= Facade =========
def init_billing_db() -> None:
    init_schema()

# Cards / Settings UI
def has_saved_card(user_id: int) -> bool:
    return _repo.has_saved_card(user_id)

def get_user_card(user_id: int) -> Optional[dict]:
    return _repo.get_user_card(user_id)

def list_user_payment_methods(user_id: int) -> List[Dict[str, Optional[str]]]:
    return _repo.list_user_payment_methods(user_id)

def has_saved_sbp(user_id: int) -> bool:
    return _repo.has_saved_sbp(user_id)

def delete_user_card_and_detach_subscriptions(*, user_id: int) -> int:
    return _repo.delete_user_card_and_detach_subscriptions(user_id=user_id)

def delete_user_sbp_and_detach_subscriptions(*, user_id: int) -> int:
    return _repo.delete_user_sbp_and_detach_subscriptions(user_id=user_id)

def card_upsert_from_provider(
    *, user_id: int, provider: str, pm_token: str,
    brand: Optional[str], first6: Optional[str], last4: Optional[str],
    exp_month: Optional[int], exp_year: Optional[int],
) -> int:
    return _repo.card_upsert_from_provider(
        user_id=user_id, provider=provider, pm_token=pm_token,
        brand=brand, first6=first6, last4=last4,
        exp_month=exp_month, exp_year=exp_year,
    )

# Subscriptions
def subscription_upsert(*, user_id: int, plan_code: str, interval_months: int, amount_value: str,
                        amount_currency: str, payment_method_id: Optional[str],
                        next_charge_at: Optional[datetime], status: str = "active") -> int:
    return _repo.subscription_upsert(
        user_id=user_id, plan_code=plan_code, interval_months=interval_months,
        amount_value=amount_value, amount_currency=amount_currency,
        payment_method_id=payment_method_id, next_charge_at=next_charge_at, status=status
    )

def subscription_cancel_for_user(*, user_id: int) -> int:
    return _repo.subscription_cancel_for_user(user_id=user_id)

def subscription_mark_charged(sub_id: int, *, next_charge_at: datetime) -> None:
    _repo.subscription_mark_charged(sub_id, next_charge_at=next_charge_at)

def subscription_mark_charged_for_user(
    user_id: int, 
    *, 
    next_charge_at: datetime,
    subscription_id: Optional[int] = None,
    plan_code: Optional[str] = None
) -> Optional[int]:
    return _repo.subscription_mark_charged_for_user(
        user_id, 
        next_charge_at=next_charge_at,
        subscription_id=subscription_id,
        plan_code=plan_code
    )

# Retries / Scheduler
def subscriptions_due(now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
    return _repo.subscriptions_due(now=now, limit=limit)

def record_charge_attempt(*, subscription_id: int, user_id: int, payment_id: Optional[str], status: str, due_at: Optional[datetime] = None) -> int:
    return _repo.record_charge_attempt(subscription_id=subscription_id, user_id=user_id, payment_id=payment_id, status=status, due_at=due_at)

def mark_charge_attempt_status(
    *, 
    payment_id: Optional[str] = None,
    subscription_id: Optional[int] = None,
    status: str
) -> None:
    _repo.mark_charge_attempt_status(
        payment_id=payment_id,
        subscription_id=subscription_id,
        status=status
    )

def precharge_guard_and_attempt(*, subscription_id: int, now: datetime, user_id: int) -> Optional[int]:
    return _repo.precharge_guard_and_attempt(subscription_id=subscription_id, now=now, user_id=user_id)

def link_payment_to_attempt(*, attempt_id: int, payment_id: str) -> None:
    return _repo.link_payment_to_attempt(attempt_id=attempt_id, payment_id=payment_id)

# Webhooks / Log
def payment_log_upsert(*, payment_id: str, user_id: Optional[int], amount_value: Optional[str],
                       amount_currency: Optional[str], event: Optional[str], status: Optional[str],
                       metadata: Optional[dict], raw_payload: Optional[dict]) -> None:
    _repo.payment_log_upsert(
        payment_id=payment_id, user_id=user_id, amount_value=amount_value,
        amount_currency=amount_currency, event=event, status=status,
        metadata=metadata, raw_payload=raw_payload
    )

def payment_log_is_processed(payment_id: str) -> bool:
    return _repo.payment_log_is_processed(payment_id)

def payment_log_mark_processed(payment_id: str) -> None:
    _repo.payment_log_mark_processed(payment_id)

# Recipients helper
def list_active_subscription_user_ids(now: Optional[datetime] = None) -> List[int]:
    return _repo.list_active_subscription_user_ids(now)

def list_mailing_eligible_users(now: Optional[datetime] = None) -> List[int]:
    return _repo.list_mailing_eligible_users()

def is_user_payment_ok(user_id: int) -> bool:
    return _repo.is_user_payment_ok(user_id)

# Trial helpers
def get_trial_started_at(user_id: int) -> Optional[datetime]:
    return _repo.get_trial_started_at(user_id)

def list_trial_started_map(user_ids: List[int]) -> Dict[int, datetime]:
    return _repo.list_trial_started_map(user_ids)