# smart_agent/bot/utils/billing_db.py
from __future__ import annotations

from typing import Optional, Any, List, Dict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    create_engine, text,
    String, Integer, BigInteger, ForeignKey, DateTime, Text
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    sessionmaker, Session
)

from bot.config import DB_URL  # <— общий DSN для биллинга
import json

MSK = ZoneInfo("Europe/Moscow")

# ──────────────────────────────────────────────────────────────────────────────
# UTC helpers
# ──────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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

    # Храним провайдерский токен карты (строка от YooKassa), а НЕ числовой ID:
    payment_method_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")   # active|canceled
    next_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
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
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


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

    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# =========================
#       Repository
# =========================
def init_schema() -> None:
    Base.metadata.create_all(bind=engine)


class BillingRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

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
            now = now_utc()
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
        payment_method_id: Optional[int],
        next_charge_at: Optional[datetime],
        status: str = "active",
    ) -> int:
        with self._session() as s, s.begin():
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.plan_code == plan_code)
                .one_or_none()
            )
            if rec is None:
                rec = Subscription(
                    user_id=user_id,
                    plan_code=plan_code,
                    interval_months=interval_months,
                    amount_value=amount_value,
                    amount_currency=amount_currency,
                    payment_method_id=payment_method_id,
                    next_charge_at=next_charge_at,
                    status=status,
                )
                s.add(rec)
                s.flush()
                return rec.id
            else:
                rec.interval_months = interval_months
                rec.amount_value = amount_value
                rec.amount_currency = amount_currency or rec.amount_currency
                rec.payment_method_id = payment_method_id  # можно None, чтобы отвязать
                rec.next_charge_at = next_charge_at
                rec.status = status
                rec.updated_at = now_utc()
                s.flush()
                return rec.id

    def subscription_cancel_for_user(self, *, user_id: int) -> int:
        with self._session() as s, s.begin():
            q = s.query(Subscription).filter(Subscription.user_id == user_id, Subscription.status == "active")
            updated = 0
            now = now_utc()
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
                rec.last_charge_at = now_utc()
                rec.next_charge_at = next_charge_at
                rec.updated_at = now_utc()

    def subscription_mark_charged_for_user(self, user_id: int, *, next_charge_at: datetime) -> Optional[int]:
        with self._session() as s, s.begin():
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                .first()
            )
            if not rec:
                return None
            rec.last_charge_at = now_utc()
            rec.next_charge_at = next_charge_at
            rec.updated_at = now_utc()
            s.flush()
            return rec.id

    # --- retries / attempts ---
    def record_charge_attempt(self, *, subscription_id: int, user_id: int, payment_id: Optional[str], status: str) -> int:
        with self._session() as s, s.begin():
            rec = ChargeAttempt(subscription_id=subscription_id, user_id=user_id, payment_id=payment_id, status=status)
            s.add(rec)
            s.flush()
            return rec.id

    def mark_charge_attempt_status(self, *, payment_id: str, status: str) -> None:
        with self._session() as s, s.begin():
            rec = s.query(ChargeAttempt).filter(ChargeAttempt.payment_id == payment_id).one_or_none()
            if rec:
                rec.status = status

    def subscriptions_due(self, *, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        short_cooldown = timedelta(minutes=30)
        long_window = timedelta(hours=24)

        with self._session() as s:
            q = (
                s.query(Subscription)
                .filter(
                    Subscription.status == "active",
                    Subscription.next_charge_at != None,                     # noqa: E711
                    Subscription.next_charge_at <= now,
                    Subscription.payment_method_id != None,                  # noqa: E711
                )
                .order_by(Subscription.next_charge_at.asc())
                .limit(limit * 3)
            )
            subs = list(q)

            since_long = now - long_window
            blocked_ids_long = {
                sub_id for (sub_id,) in
                s.query(ChargeAttempt.subscription_id)
                 .filter(ChargeAttempt.attempted_at >= since_long)
                 .group_by(ChargeAttempt.subscription_id)
                 .having(text("COUNT(*) >= 2"))
                 .all()
            }

            since_short = now - short_cooldown
            blocked_ids_short = {
                sub_id for (sub_id,) in
                s.query(ChargeAttempt.subscription_id)
                 .filter(ChargeAttempt.attempted_at >= since_short)
                 .group_by(ChargeAttempt.subscription_id)
                 .all()
            }

            items: List[Dict[str, Any]] = []
            for rec in subs:
                if rec.id in blocked_ids_long or rec.id in blocked_ids_short:
                    continue
                items.append({
                    "id": rec.id,
                    "user_id": rec.user_id,
                    "plan_code": rec.plan_code,
                    "interval_months": rec.interval_months,
                    "amount_value": rec.amount_value,
                    "amount_currency": rec.amount_currency,
                    "payment_method_id": rec.payment_method_id,
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
        with self._session() as s, s.begin():
            rec = s.get(PaymentLog, payment_id)
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
            raw_payload_json = json.dumps(raw_payload or {}, ensure_ascii=False)
            if rec is None:
                rec = PaymentLog(
                    payment_id=payment_id,
                    user_id=user_id,
                    amount_value=amount_value,
                    amount_currency=amount_currency or "RUB",
                    event=event,
                    status=status,
                    metadata_json=metadata_json,
                    raw_payload_json=raw_payload_json,
                )
                s.add(rec)
            else:
                if user_id and not rec.user_id:
                    rec.user_id = user_id
                rec.amount_value = amount_value
                rec.amount_currency = amount_currency or rec.amount_currency
                rec.event = event or rec.event
                rec.status = status or rec.status
                rec.metadata_json = metadata_json
                rec.raw_payload_json = raw_payload_json

    def payment_log_is_processed(self, payment_id: str) -> bool:
        with self._session() as s:
            rec = s.get(PaymentLog, payment_id)
            return bool(rec and rec.processed_at is not None)

    def payment_log_mark_processed(self, payment_id: str) -> None:
        with self._session() as s, s.begin():
            rec = s.get(PaymentLog, payment_id)
            if rec is None:
                rec = PaymentLog(payment_id=payment_id, processed_at=now_utc())
                s.add(rec)
            else:
                rec.processed_at = now_utc()


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

def delete_user_card_and_detach_subscriptions(*, user_id: int) -> int:
    return _repo.delete_user_card_and_detach_subscriptions(user_id=user_id)

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
                        amount_currency: str, payment_method_id: Optional[int],
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

def subscription_mark_charged_for_user(user_id: int, *, next_charge_at: datetime) -> Optional[int]:
    return _repo.subscription_mark_charged_for_user(user_id, next_charge_at=next_charge_at)

# Retries / Scheduler
def subscriptions_due(now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
    return _repo.subscriptions_due(now=now, limit=limit)

def record_charge_attempt(*, subscription_id: int, user_id: int, payment_id: Optional[str], status: str) -> int:
    return _repo.record_charge_attempt(subscription_id=subscription_id, user_id=user_id, payment_id=payment_id, status=status)

def mark_charge_attempt_status(*, payment_id: str, status: str) -> None:
    _repo.mark_charge_attempt_status(payment_id=payment_id, status=status)

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
