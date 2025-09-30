# smart_agent/bot/utils/database.py

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

from bot.config import DB_URL
import json

MSK = ZoneInfo("Europe/Moscow")

# ──────────────────────────────────────────────────────────────────────────────
# UTC helpers (современный подход: aware-дата/время в UTC + ISO8601 с 'Z')
# ──────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    """Timezone-aware 'now' в UTC."""
    return datetime.now(timezone.utc)

def iso_utc_z(dt: datetime) -> str:
    """ISO8601 в UTC с 'Z' без микросекунд."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    bluhbluhbluh = create_engine(
        DB_URL,  # Используем DB_URL вместо DB_PATH
        future=True,
        echo=False,
        pool_pre_ping=True,
    )

    return bluhbluhbluh


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# =========================
#          Models
# =========================
class User(Base):
    __tablename__ = "users"

    # Telegram user_id может быть > 2^31 → используем BIGINT
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    variables: Mapped[list["Variable"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # удобная связь для истории
    history: Mapped[list["ReviewHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # история саммари переговоров
    summaries: Mapped[list["SummaryHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # история "Описание объекта" (большие тексты)
    descriptions: Mapped[list["DescriptionHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Variable(Base):
    __tablename__ = "variables"

    # Композитный PK: (user_id, variable_name)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True
    )
    # MySQL требует длину для VARCHAR; 191 безопасно для utf8mb4 (PK/индексы)
    variable_name: Mapped[str] = mapped_column(String(191), primary_key=True)
    # Значения переменных могут быть длинными → используем TEXT
    variable_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="variables")


class ReviewHistory(Base):
    """
    История сгенерированных черновиков.
    Храним «плоские» поля payload, чтобы не городить JSON в SQLite.
    """
    __tablename__ = "review_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    # payload
    client_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_name:  Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # CSV кодов (sale,buy,...) + 'custom'
    deal_type:   Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    deal_custom: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    situation:   Mapped[Optional[str]] = mapped_column(Text,   nullable=True)
    style:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # результат
    final_text:  Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="history")


class SummaryHistory(Base):
    """
    История саммари переговоров (универсальные JSON-поля).
    """
    __tablename__ = "summary_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    # метаданные запроса
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "text" | "audio" | "unknown"
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # данные
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)   # вход/контекст
    result_json:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # результат анализа

    user: Mapped[User] = relationship(back_populates="summaries")


class DescriptionHistory(Base):
    """
    История запросов/результатов по генерации «описания объекта».
    Отдельная таблица под большие тексты.
    """
    __tablename__ = "description_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    # Входные поля (что отправляли в executor) — как JSON
    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Результат генерации — большой текст
    result_text: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="descriptions")


class PaymentLog(Base):
    """
    Журнал платежей YooKassa (аудит + идемпотентность по payment_id).
    """
    __tablename__ = "payment_log"

    payment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id:    Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), index=True, nullable=True)
    amount_value:    Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    amount_currency: Mapped[Optional[str]] = mapped_column(String(8),  nullable=True)
    event:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    metadata_json:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base):
    """
    Подписки пользователей (рекуррентные списания).
    """
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)               # 1m / 3m ...
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_value: Mapped[str] = mapped_column(String(32), nullable=False)            # "2490.00"
    amount_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")

    payment_method_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")   # active|canceled
    next_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_charge_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ChargeAttempt(Base):
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

class PaymentMethod(Base):
    """
    Привязанные платёжные методы (карты) пользователей.
    Храним метаданные карты для UI и токен провайдера для списаний.
    """
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="yookassa")
    pm_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)  # токен провайдера
    brand: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)       # 'VISA', 'MC', ...
    first6: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    exp_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exp_year:  Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(backref="subscriptions")


class ChargeAttempt(Base):
    """
    Попытки авто-списаний по подписке (для лимита ретраев).
    """
    __tablename__ = "charge_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True, nullable=False)
    payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # id платежа в YooKassa (если есть)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")  # created|succeeded|canceled|expired
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


# =========================
#       Repository
# =========================
def init_schema() -> None:
    Base.metadata.create_all(bind=engine)


class UserRepository:
    """
    никакого сырого SQL.
    """

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory


    # --- utils ---
    def _session(self) -> Session:
        return self._session_factory()

    # --- users ---
    def ensure_user(self, user_id: int) -> bool:
        """
        Возвращает True, если пользователь уже был; False — если только что создан.
        Дефолты: have_sub=0, trial_until=now+72h.
        """
        with self._session() as s, s.begin():
            existed = s.get(User, user_id) is not None
            if not existed:
                u = User(user_id=user_id)
                s.add(u)
                trial_until = iso_utc_z(now_utc() + timedelta(hours=72))
                s.add_all([
                    Variable(user_id=user_id, variable_name="have_sub", variable_value="0"),
                    Variable(user_id=user_id, variable_name="trial_until", variable_value=trial_until),
                ])
            return existed

    # --- variables ---
    def set_var(self, user_id: int, name: str, value: Any) -> str:
        value_str = str(value)
        with self._session() as s, s.begin():
            # гарантируем, что пользователь есть
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            v = s.get(Variable, (user_id, name))
            if v is None:
                s.add(Variable(user_id=user_id, variable_name=name, variable_value=value_str))
            else:
                v.variable_value = value_str
        return value_str

    def get_var(self, user_id: int, name: str) -> Optional[str]:
        with self._session() as s:
            v = s.get(Variable, (user_id, name))
            return v.variable_value if v else None

    # --- payments ---
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
                # обновляем известные поля; processed_at не трогаем
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

    # --- payment method presence ---
    def has_saved_card(self, user_id: int) -> bool:
        "True, если есть НЕ удалённый payment_method для пользователя."
        with self._session() as s:
            return (
                s.query(PaymentMethod)
                 .filter(PaymentMethod.user_id == user_id, PaymentMethod.deleted_at.is_(None))
                 .first()
                is not None
            )

    def get_user_card(self, user_id: int) -> Optional[dict]:
        "Возвращает первую активную карту пользователя (для UI)."
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
                "pm_id": rec.pm_id,
                "brand": rec.brand or "",
                "first6": rec.first6 or "",
                "last4": rec.last4 or "",
                "exp_month": rec.exp_month,
                "exp_year": rec.exp_year,
            }

    def debug_payment_state(self, user_id: int) -> dict:
        """
        Диагностика состояния «привязанной карты»:
          - var_raw/var_norm — как хранится токен в variables
          - subs_active_with_pm — количество активных подписок с payment_method_id
        """
        with self._session() as s:
            v = s.get(Variable, (user_id, "yk:payment_method_id"))
            raw = (v.variable_value if v else "") or ""
            norm = raw.strip().lower()
            subs_active_with_pm = (
                s.query(Subscription)
                 .filter(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                    Subscription.payment_method_id.isnot(None),
                 )
                 .count()
            )
            return {
                "var_raw": raw,
                "var_norm": norm,
                "subs_active_with_pm": int(subs_active_with_pm),
            }

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
    ) -> int:
        with self._session() as s, s.begin():
            # одна активная подписка на пользователя и план
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
                if payment_method_id:
                    rec.payment_method_id = payment_method_id
                rec.next_charge_at = next_charge_at
                rec.status = status
                rec.updated_at = now_utc()
                s.flush()
                return rec.id

    def subscription_cancel_for_user(self, *, user_id: int) -> int:
        """
        Отменяет ВСЕ активные подписки пользователя: ставит status='canceled',
        обнуляет payment_method_id и next_charge_at, фиксирует cancel_at.
        Возвращает количество изменённых записей.
        """
        with self._session() as s, s.begin():
            q = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
            )
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
        """
        Помечает как списанную актуальную (active) подписку пользователя.
        Выбираем «самую релевантную» запись:
          1) статус active
          2) упорядочиваем по next_charge_at DESC (последняя ближайшая дата),
             затем по updated_at DESC — берём первую.
        Возвращает id подписки, если удалось обновить, иначе None.
        """
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

    def delete_user_card_and_detach_subscriptions(self, *, user_id: int) -> int:
        """
        Мягко удаляет карту (deleted_at = now) и для ВСЕХ активных подписок пользователя
        выставляет payment_method_id = NULL. Статус подписок не меняем.
        Возвращает количество «затронутых» подписок.
        """
        with self._session() as s, s.begin():
            # пометить все карты как deleted (если несколько — удалим все)
            now = now_utc()
            for pm in s.query(PaymentMethod).filter(
                PaymentMethod.user_id == user_id,
                PaymentMethod.deleted_at.is_(None),
            ).all():
                pm.deleted_at = now
            # обнулить pm_id в активных подписках
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

    def subscriptions_due(self, *, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Возвращает подписки, у которых наступил срок списания И
        за последние 24 часа было < 2 попыток списания.
        Now — ожидается как aware-UTC; при необходимости приводим.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        short_cooldown = timedelta(minutes=30)   # защита от частых ретраев
        long_window    = timedelta(hours=24)     # суточный лимит на попытки

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
                .limit(limit * 3)  # небольшой "запас" перед фильтрацией по попыткам
            )
            subs = list(q)

            # 1) "Перегретые" подписки с >=2 попытками за 24ч
            since_long = now - long_window
            blocked_ids_long = {
                sub_id for (sub_id,) in
                s.query(ChargeAttempt.subscription_id)
                 .filter(ChargeAttempt.attempted_at >= since_long)
                 .group_by(ChargeAttempt.subscription_id)
                 .having(text("COUNT(*) >= 2"))
                 .all()
            }

            # 2) Короткий cooldown: была ЛЮБАЯ попытка за последние 30 минут
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

    # --- charge attempts (лимит ретраев) ---
    def record_charge_attempt(self, *, subscription_id: int, user_id: int, payment_id: Optional[str], status: str) -> int:
        with self._session() as s, s.begin():
            rec = ChargeAttempt(
                subscription_id=subscription_id,
                user_id=user_id,
                payment_id=payment_id,
                status=status,
            )
            s.add(rec)
            s.flush()
            return rec.id

    def mark_charge_attempt_status(self, *, payment_id: str, status: str) -> None:
        with self._session() as s, s.begin():
            rec = s.query(ChargeAttempt).filter(ChargeAttempt.payment_id == payment_id).one_or_none()
            if rec:
                rec.status = status

    # --- payment methods (cards) ---
    def card_upsert_from_provider(
        self,
        *,
        user_id: int,
        provider: str,
        pm_id: str,
        brand: Optional[str],
        first6: Optional[str],
        last4: Optional[str],
        exp_month: Optional[int],
        exp_year: Optional[int],
    ) -> int:
        """
        Создаёт/обновляет запись карты по token (pm_id).
        Снимает 'deleted_at', если карта была помечена удалённой.
        Возвращает id карты.
        """
        with self._session() as s, s.begin():
            rec = s.query(PaymentMethod).filter(PaymentMethod.pm_id == pm_id).one_or_none()
            if rec is None:
                rec = PaymentMethod(
                    user_id=user_id, provider=provider, pm_id=pm_id,
                    brand=brand, first6=first6, last4=last4,
                    exp_month=exp_month, exp_year=exp_year,
                )
                s.add(rec)
                s.flush()
                return rec.id
            # обновим метаданные и снимем "deleted"
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

    # -------- Mailing recipients (from variables) --------
    def list_active_subscriber_ids(self, *, include_grace_days: int = 0) -> List[int]:
        """
        Возвращает user_id тех, у кого есть платная подписка:
          - have_sub = '1'
          - sub_until >= today(MSK) - grace_days
        Триал НЕ учитываем.
        """
        today = datetime.now(MSK).date()
        if include_grace_days and include_grace_days > 0:
            today = today - timedelta(days=include_grace_days)
        today_str = today.strftime("%Y-%m-%d")

        sql = text("""
            SELECT DISTINCT v1.user_id
            FROM variables v1
            JOIN variables v2
              ON v2.user_id = v1.user_id
             AND v2.variable_name = 'sub_until'
             AND v2.variable_value >= :today
            WHERE v1.variable_name = 'have_sub'
              AND v1.variable_value = '1'
        """)
        with self._session() as s:
            rows = s.execute(sql, {"today": today_str}).fetchall()
            return [int(r[0]) for r in rows]

    def subscription_cancel(self, user_id: int, plan_code: str) -> None:
        with self._session() as s, s.begin():
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.plan_code == plan_code)
                .one_or_none()
            )
            if rec and rec.status != "canceled":
                rec.status = "canceled"
                rec.cancel_at = now_utc()
                rec.updated_at = now_utc()

    # --- trial helpers ---
    def set_trial(self, user_id: int, hours: int = 72) -> str:
        until = now_utc() + timedelta(hours=int(hours))
        iso = iso_utc_z(until)
        self.set_var(user_id, "trial_until", iso)
        return iso

    def get_trial_until(self, user_id: int) -> Optional[str]:
        return self.get_var(user_id, "trial_until")

    def is_trial_active(self, user_id: int) -> bool:
        raw = self.get_trial_until(user_id)
        if not raw:
            return False
        # допускаем формат ISO с 'Z' в конце
        try:
            ts = raw[:-1] if raw.endswith("Z") else raw
            # если без таймзоны — считаем это UTC
            parsed = datetime.fromisoformat(ts)
            until = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except Exception:
            return False
        return now_utc() < until

    def trial_remaining_hours(self, user_id: int) -> int:
        raw = self.get_trial_until(user_id)
        if not raw:
            return 0
        try:
            ts = raw[:-1] if raw.endswith("Z") else raw
            parsed = datetime.fromisoformat(ts)
            until = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            return max(0, int((until - now_utc()).total_seconds() // 3600))
        except Exception:
            return 0

    # --- history ---
    def history_add(self, user_id: int, payload: dict, final_text: str) -> ReviewHistory:
        """
        Сохраняет запись истории и возвращает ORM-объект.
        payload — словарь с ключами полей payload (client_name/agent_name/.../style).
        """
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = ReviewHistory(
                user_id=user_id,
                client_name=payload.get("client_name"),
                agent_name=payload.get("agent_name"),
                company=payload.get("company"),
                city=payload.get("city"),
                address=payload.get("address"),
                deal_type=payload.get("deal_type"),
                deal_custom=payload.get("deal_custom"),
                situation=payload.get("situation"),
                style=payload.get("style"),
                final_text=final_text,
            )
            s.add(rec)
            s.flush()  # получить rec.id
            s.refresh(rec)
            return rec

    def history_list(self, user_id: int, limit: int = 10) -> List[ReviewHistory]:
        with self._session() as s:
            q = (
                s.query(ReviewHistory)
                .filter(ReviewHistory.user_id == user_id)
                .order_by(ReviewHistory.id.desc())
                .limit(limit)
            )
            return list(q)

    def history_get(self, user_id: int, item_id: int) -> Optional[ReviewHistory]:
        with self._session() as s:
            rec = s.get(ReviewHistory, item_id)
            if rec is None or rec.user_id != user_id:
                return None
            return rec

    # --- SUMMARY: CRUD-методы в удобном формате dict ---
    def summary_add_entry(self, user_id: int, *, source_type: str, options: dict, payload: dict, result: Optional[dict]) -> int:
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = SummaryHistory(
                user_id=user_id,
                source_type=source_type or "unknown",
                options_json=json.dumps(options or {}, ensure_ascii=False),
                payload_json=json.dumps(payload or {}, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False) if result is not None else None,
            )
            s.add(rec)
            s.flush()
            return rec.id

    def summary_list_entries(self, user_id: int, limit: int = 10) -> list[dict]:
        with self._session() as s:
            q = (
                s.query(SummaryHistory)
                .filter(SummaryHistory.user_id == user_id)
                .order_by(SummaryHistory.id.desc())
                .limit(limit)
            )
            items: list[dict] = []
            for rec in q:
                items.append({
                    "id": rec.id,
                    "created_at": rec.created_at.isoformat(timespec="seconds"),
                    "source_type": rec.source_type,
                    "options": json.loads(rec.options_json or "{}"),
                })
            return items

    def summary_get_entry(self, user_id: int, entry_id: int) -> Optional[dict]:
        with self._session() as s:
            rec = s.get(SummaryHistory, entry_id)
            if rec is None or rec.user_id != user_id:
                return None
            return {
                "id": rec.id,
                "created_at": rec.created_at.isoformat(timespec="seconds"),
                "source_type": rec.source_type,
                "options": json.loads(rec.options_json or "{}"),
                "payload": json.loads(rec.payload_json or "{}"),
                "result": json.loads(rec.result_json or "null") if rec.result_json else None,
            }

    # --- Description (описания объектов): CRUD ---
    def description_add(self, user_id: int, *, fields: dict, result_text: str) -> int:
        """
        Добавляет запись истории «Описание объекта».
        """
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = DescriptionHistory(
                user_id=user_id,
                fields_json=json.dumps(fields or {}, ensure_ascii=False),
                result_text=result_text or "",
            )
            s.add(rec)
            s.flush()
            return rec.id

    def description_list(self, user_id: int, limit: int = 10) -> list[dict]:
        with self._session() as s:
            q = (
                s.query(DescriptionHistory)
                .filter(DescriptionHistory.user_id == user_id)
                .order_by(DescriptionHistory.id.desc())
                .limit(limit)
            )
            items: list[dict] = []
            for rec in q:
                items.append({
                    "id": rec.id,
                    "created_at": rec.created_at.isoformat(timespec="seconds"),
                    # лёгкий превью по результату (первые 60 символов без переводов)
                    "preview": (rec.result_text or "").replace("\n", " ")[:60],
                })
            return items

    def description_get(self, user_id: int, entry_id: int) -> Optional[dict]:
        with self._session() as s:
            rec = s.get(DescriptionHistory, entry_id)
            if rec is None or rec.user_id != user_id:
                return None
            return {
                "id": rec.id,
                "created_at": rec.created_at.isoformat(timespec="seconds"),
                "fields": json.loads(rec.fields_json or "{}"),
                "result_text": rec.result_text or "",
            }

    def description_delete(self, user_id: int, entry_id: int) -> bool:
        with self._session() as s, s.begin():
            rec = s.get(DescriptionHistory, entry_id)
            if rec is None or rec.user_id != user_id:
                return False
            s.delete(rec)
            return True


# Глобальный «репозиторий» для обратной совместимости
_repo = UserRepository(SessionLocal)
init_schema()


# =========================
#   Совместимые функции
# =========================
def init_db() -> None:
    init_schema()


def set_variable(user_id: int, variable_name: str, variable_value: Any) -> Any:
    return _repo.set_var(user_id, variable_name, variable_value)


def get_variable(user_id: int, variable_name: str) -> Optional[str]:
    return _repo.get_var(user_id, variable_name)


def check_and_add_user(user_id: int) -> bool:
    return _repo.ensure_user(user_id)


# --- Trial wrappers ---
def set_trial(user_id: int, hours: int = 72) -> str:
    return _repo.set_trial(user_id, hours)
def is_trial_active(user_id: int) -> bool:
    return _repo.is_trial_active(user_id)
def trial_remaining_hours(user_id: int) -> int:
    return _repo.trial_remaining_hours(user_id)


# -------- History compat wrappers --------
def history_add(user_id: int, payload: dict, final_text: str) -> ReviewHistory:
    return _repo.history_add(user_id, payload, final_text)


def history_list(user_id: int, limit: int = 10) -> list[ReviewHistory]:
    return _repo.history_list(user_id, limit)


def history_get(user_id: int, item_id: int) -> Optional[ReviewHistory]:
    return _repo.history_get(user_id, item_id)

# -------- Summary (новые обёртки под прежний контракт) --------
def summary_add_entry(*, user_id: int, source_type: str, options: dict, payload: dict, result: Optional[dict]) -> int:
    return _repo.summary_add_entry(user_id, source_type=source_type, options=options, payload=payload, result=result)

def summary_list_entries(user_id: int, limit: int = 10) -> list[dict]:
    return _repo.summary_list_entries(user_id, limit=limit)

def summary_get_entry(user_id: int, entry_id: int) -> Optional[dict]:
    return _repo.summary_get_entry(user_id, entry_id)

# -------- Descriptions (история описаний) --------
def description_add(*, user_id: int, fields: dict, result_text: str) -> int:
    return _repo.description_add(user_id, fields=fields, result_text=result_text)

def description_list(user_id: int, limit: int = 10) -> list[dict]:
    return _repo.description_list(user_id, limit=limit)

def description_get(user_id: int, entry_id: int) -> Optional[dict]:
    return _repo.description_get(user_id, entry_id)

def description_delete(user_id: int, entry_id: int) -> bool:
    return _repo.description_delete(user_id, entry_id)

# -------- Payments (лог/идемпотентность) --------
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

# -------- Subscriptions --------
def subscription_upsert(*, user_id: int, plan_code: str, interval_months: int, amount_value: str,
                        amount_currency: str, payment_method_id: Optional[str],
                        next_charge_at: Optional[datetime], status: str = "active") -> int:
    return _repo.subscription_upsert(
        user_id=user_id, plan_code=plan_code, interval_months=interval_months,
        amount_value=amount_value, amount_currency=amount_currency,
        payment_method_id=payment_method_id, next_charge_at=next_charge_at, status=status
    )

def subscriptions_due(now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
    return _repo.subscriptions_due(now=now, limit=limit)

def subscription_mark_charged(sub_id: int, *, next_charge_at: datetime) -> None:
    _repo.subscription_mark_charged(sub_id, next_charge_at=next_charge_at)

def subscription_cancel(user_id: int, plan_code: str) -> None:
    _repo.subscription_cancel(user_id, plan_code)

def subscription_mark_charged_for_user(user_id: int, *, next_charge_at: datetime) -> Optional[int]:
    """Совместимая обёртка для обновления подписки по user_id."""
    return _repo.subscription_mark_charged_for_user(user_id, next_charge_at=next_charge_at)

def subscription_cancel_for_user(*, user_id: int) -> int:
    """Отменить все активные подписки пользователя (очистит pm_id/next_charge_at)."""
    return _repo.subscription_cancel_for_user(user_id=user_id)

# -------- Card presence / debug --------
def has_saved_card(user_id: int) -> bool:
    return _repo.has_saved_card(user_id)

def debug_payment_state(user_id: int) -> dict:
    return _repo.debug_payment_state(user_id)

# -------- Mailing recipients (compat wrapper) --------
def list_active_subscriber_ids(include_grace_days: int = 0) -> List[int]:
    return _repo.list_active_subscriber_ids(include_grace_days=include_grace_days)

# -------- Charge attempts (обёртки) --------
def record_charge_attempt(*, subscription_id: int, user_id: int, payment_id: Optional[str], status: str) -> int:
    return _repo.record_charge_attempt(subscription_id=subscription_id, user_id=user_id, payment_id=payment_id, status=status)

def mark_charge_attempt_status(*, payment_id: str, status: str) -> None:
    _repo.mark_charge_attempt_status(payment_id=payment_id, status=status)

# -------- Cards (facade) --------
def card_upsert_from_provider(
    *, user_id: int, provider: str, pm_id: str,
    brand: Optional[str], first6: Optional[str], last4: Optional[str],
    exp_month: Optional[int], exp_year: Optional[int],
) -> int:
    return _repo.card_upsert_from_provider(
        user_id=user_id, provider=provider, pm_id=pm_id,
        brand=brand, first6=first6, last4=last4,
        exp_month=exp_month, exp_year=exp_year,
    )

def get_user_card(user_id: int) -> Optional[dict]:
    return _repo.get_user_card(user_id)

def delete_user_card_and_detach_subscriptions(*, user_id: int) -> int:
    return _repo.delete_user_card_and_detach_subscriptions(user_id=user_id)
