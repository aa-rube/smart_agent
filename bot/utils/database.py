# smart_agent/bot/utils/database.py
#I'm using MYSQL8+ for this proj.
from __future__ import annotations

from typing import Optional
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine, String, Integer, BigInteger, ForeignKey, DateTime, Text, func
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    sessionmaker, Session
)

from bot.config import DB_URL
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
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Новые поля
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Логи событий
    events: Mapped[list["EventLog"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # История черновиков
    history: Mapped[list["ReviewHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # История саммари переговоров
    summaries: Mapped[list["SummaryHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # История «Описание объекта»
    descriptions: Mapped[list["DescriptionHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Согласия (TOS и т.п.)
    consents: Mapped[list["UserConsent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Триал
    trials: Mapped[list["Trial"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ReviewHistory(Base):
    __tablename__ = "review_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    # Идентификатор кейса (коллекции вариантов), общий для всех вариантов одного запуска
    case_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    client_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    deal_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    deal_custom: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    situation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    style: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    final_text: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="history")


class SummaryHistory(Base):
    __tablename__ = "summary_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "text" | "audio" | "unknown"
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="summaries")


class DescriptionHistory(Base):
    __tablename__ = "description_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    # Новое поле: уникальный идентификатор генерации (для связи с options и обновления по callback)
    msg_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_text: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="descriptions")


class DescriptionOption(Base):
    """
    EAV-таблица параметров генерации.
    Хранит все пары (variable, value) для конкретного msgId.
    """
    __tablename__ = "description_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    variable: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class UserConsent(Base):
    """
    Согласия пользователя (например, TOS).
    kind: 'tos'
    """
    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    user: Mapped[User] = relationship(back_populates="consents")


class Trial(Base):
    """
    Триал доступа. Одна актуальная запись на пользователя.
    """
    __tablename__ = "trials"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    until_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    user: Mapped[User] = relationship(back_populates="trials")


class EventLog(Base):
    """
    Простые события пользователя.
    Храним автоинкрементный id, user_id, строку сообщения и время с точностью до мс.
    """
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_msk, nullable=False)

    user: Mapped[User] = relationship(back_populates="events")


# =========================
#       Repository
# =========================
def init_schema() -> None:
    """
    Инициализация схемы БД.
    Опираемся на SQLAlchemy `Base.metadata.create_all`, без сырых ALTER/CREATE INDEX.
    Все необходимые таблицы/индексы должны быть созданы миграциями либо заранее.
    """
    Base.metadata.create_all(bind=engine)


class AppRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # --- users ---
    def ensure_user(self, user_id: int, *, chat_id: Optional[int] = None, username: Optional[str] = None) -> bool:
        with self._session() as s, s.begin():
            rec = s.get(User, user_id)
            existed = rec is not None
            if not existed:
                s.add(User(
                    user_id=user_id,
                    chat_id=chat_id if chat_id is not None else user_id,
                    username=username
                ))
                return False
            # Обновляем при изменениях
            if chat_id is not None and rec.chat_id != chat_id:
                rec.chat_id = chat_id
            if username is not None and rec.username != username:
                rec.username = username
            # Коммит произойдёт по exit из контекста begin()
            return True

    # --- consents ---
    def add_consent(self, user_id: int, kind: str, when: Optional[datetime] = None) -> int:
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            when_msk = to_aware_msk(when) if when else now_msk()
            rec = UserConsent(user_id=user_id, kind=kind, accepted_at=to_utc_for_db(when_msk))
            s.add(rec)
            s.flush()
            return rec.id

    # --- trials ---
    def set_trial(self, user_id: int, hours: int = 72) -> datetime:
        until_msk = now_msk() + timedelta(hours=int(hours))
        until_utc = to_utc_for_db(until_msk)  # Для БД храним в UTC
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = s.get(Trial, user_id)
            if rec is None:
                s.add(Trial(user_id=user_id, until_at=until_utc))
            else:
                rec.until_at = until_utc
                rec.updated_at = to_utc_for_db(now_msk())
        return until_msk  # Возвращаем в МСК

    def get_trial_until(self, user_id: int) -> Optional[datetime]:
        with self._session() as s:
            rec = s.get(Trial, user_id)
            if not rec:
                return None
            return from_db_naive(rec.until_at)

    def get_trial_created_at(self, user_id: int) -> Optional[datetime]:
        """Дата первого оформления триала (created_at записи Trial)."""
        with self._session() as s:
            rec = s.get(Trial, user_id)
            if not rec:
                return None
            return from_db_naive(rec.created_at)

    def trial_cooldown_days_left(self, user_id: int, *, cooldown_days: int = 60) -> int:
        """
        Сколько дней осталось до разрешения повторного триала.
        Если триала не было — 0.
        """
        created = self.get_trial_created_at(user_id)
        if not created:
            return 0
        now_msk_val = now_msk()
        delta = (now_msk_val - created).days
        left = cooldown_days - max(0, delta)
        return max(0, left)

    def get_last_purchase_action_date(self, user_id: int) -> Optional[datetime]:
        """
        Возвращает дату последней итерации покупки (успех/неуспех, автосписание, любое действие пользователя).
        Проверяет:
        - последний триал (updated_at из trials)
        - последняя подписка (updated_at из subscriptions)
        - последний успешный платеж (last_charge_at из subscriptions)
        - последняя попытка автосписания (attempted_at из charge_attempts)
        - последний платеж (created_at из payment_log где статус успешный/отменен/истек)
        Возвращает максимум из всех этих дат.
        """
        dates = []
        
        # 1. Последний триал (updated_at - последнее обновление триала)
        with self._session() as s:
            trial_rec = s.get(Trial, user_id)
            if trial_rec:
                dates.append(from_db_naive(trial_rec.updated_at))
        
        # 2. Последняя подписка и последний успешный платеж
        try:
            from bot.utils.billing_db import SessionLocal as BillingSessionLocal, Subscription, ChargeAttempt, PaymentLog
            with BillingSessionLocal() as s:
                # Последняя подписка (updated_at)
                sub_rec = (
                    s.query(Subscription)
                    .filter(Subscription.user_id == user_id)
                    .order_by(Subscription.updated_at.desc())
                    .first()
                )
                if sub_rec:
                    dates.append(from_db_naive(sub_rec.updated_at))
                    if sub_rec.last_charge_at:
                        dates.append(from_db_naive(sub_rec.last_charge_at))
                
                # Последняя попытка автосписания
                attempt_rec = (
                    s.query(ChargeAttempt)
                    .filter(ChargeAttempt.user_id == user_id)
                    .order_by(ChargeAttempt.attempted_at.desc())
                    .first()
                )
                if attempt_rec:
                    dates.append(from_db_naive(attempt_rec.attempted_at))
                
                # Последний платеж: проверяем все финальные статусы (успех, отмена, истечение)
                # ВАЖНО: PaymentLog.created_at используется как дополнительный источник истины для renewal платежей,
                # даже если last_charge_at в подписке не обновился из-за ошибки
                payment_rec = (
                    s.query(PaymentLog)
                    .filter(
                        PaymentLog.user_id == user_id,
                        PaymentLog.status.isnot(None),
                        PaymentLog.status.in_(('succeeded', 'canceled', 'expired'))
                    )
                    .order_by(PaymentLog.created_at.desc())
                    .first()
                )
                if payment_rec:
                    dates.append(from_db_naive(payment_rec.created_at))
                    
                # Дополнительно: проверяем только успешные платежи отдельно для более точного учёта кулдауна
                # Это особенно важно для renewal платежей, где last_charge_at может не обновиться
                succeeded_payment_rec = (
                    s.query(PaymentLog)
                    .filter(
                        PaymentLog.user_id == user_id,
                        PaymentLog.status == 'succeeded'
                    )
                    .order_by(PaymentLog.created_at.desc())
                    .first()
                )
                if succeeded_payment_rec:
                    # Добавляем дату успешного платежа (может быть уже в dates, но это не страшно)
                    dates.append(from_db_naive(succeeded_payment_rec.created_at))
        except Exception:
            pass  # billing_db может быть недоступен
        
        if not dates:
            return None
        
        # Возвращаем максимум из всех дат
        valid_dates = [d for d in dates if d is not None]
        return max(valid_dates) if valid_dates else None

    def is_trial_allowed(self, user_id: int, *, cooldown_days: int = 90) -> bool:
        """
        Разрешён ли повторный триал/покупка за 1 рубль с учётом кулдауна.
        Проверяет не только триал, но и любые покупки подписки.
        Кулдаун: 90 дней с последней итерации покупки (успех/неуспех, автосписание, любое действие).
        
        ИСПРАВЛЕНО: Использует timedelta для точного сравнения вместо .days,
        чтобы избежать проблем на границе 90 дней (89 дней 23 часа).
        """
        last_action = self.get_last_purchase_action_date(user_id)
        if last_action is None:
            return True  # Не было покупок - разрешено
        
        now_msk_val = now_msk()
        # ИСПРАВЛЕНО: Используем timedelta для точного сравнения вместо .days
        # .days возвращает только целую часть, что приводит к проблемам на границе
        delta = now_msk_val - last_action
        required_delta = timedelta(days=cooldown_days)
        return delta >= required_delta

    def is_trial_active(self, user_id: int) -> bool:
        until = self.get_trial_until(user_id)
        return bool(until and now_msk() < until)

    def trial_remaining_hours(self, user_id: int) -> int:
        until = self.get_trial_until(user_id)
        if until is None:
            return 0
        now_msk_val = now_msk()
        return max(0, int((until - now_msk_val).total_seconds() // 3600))

    def list_trial_active_user_ids(self, now: Optional[datetime] = None) -> list[int]:
        """Все пользователи, у кого активен триал на момент now (MySQL 8+)."""
        now_msk_val = to_aware_msk(now) if now else now_msk()
        now_utc = to_utc_for_db(now_msk_val)  # Для сравнения с БД (БД хранит в UTC)
        with self._session() as s:
            rows = (
                s.query(Trial.user_id)
                .filter(Trial.until_at > now_utc)
                .all()
            )
            return [uid for (uid,) in rows]

    # --- history ---
    def history_add(self, user_id: int, payload: dict, final_text: str, *,
                    case_id: Optional[str] = None) -> ReviewHistory:
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = ReviewHistory(
                user_id=user_id,
                case_id=case_id,
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
            s.flush()
            s.refresh(rec)
            return rec

    def history_list(self, user_id: int, limit: int = 10) -> list[ReviewHistory]:
        with self._session() as s:
            q = (
                s.query(ReviewHistory)
                .filter(ReviewHistory.user_id == user_id)
                .order_by(ReviewHistory.id.desc())
                .limit(limit)
            )
            return list(q)

    def history_list_cases(self, user_id: int, limit: int = 10) -> list[ReviewHistory]:
        """
        Возвращает по ОДНОЙ «представляющей» записи на кейс (case_id) + одиночные записи без case_id,
        упорядочено по времени (последние сверху).
        """
        with self._session() as s:
            # последние кейсы (есть case_id)
            sub = (
                s.query(
                    ReviewHistory.case_id.label("cid"),
                    func.max(ReviewHistory.id).label("max_id"),
                )
                .filter(ReviewHistory.user_id == user_id, ReviewHistory.case_id.isnot(None))
                .group_by(ReviewHistory.case_id)
                .order_by(func.max(ReviewHistory.id).desc())
                .limit(limit)
                .subquery()
            )
            case_rows = []
            if sub is not None:
                case_rows = (
                    s.query(ReviewHistory)
                    .join(sub, ReviewHistory.id == sub.c.max_id)
                    .order_by(ReviewHistory.id.desc())
                    .all()
                )
            # одиночные записи без case_id (добавим если не хватило лимита)
            need_more = max(0, limit - len(case_rows))
            single_rows: list[ReviewHistory] = []
            if need_more > 0:
                single_rows = (
                    s.query(ReviewHistory)
                    .filter(ReviewHistory.user_id == user_id, ReviewHistory.case_id.is_(None))
                    .order_by(ReviewHistory.id.desc())
                    .limit(need_more)
                    .all()
                )
            # совместный список: сначала кейсы по дате, затем одиночки
            return list(case_rows) + list(single_rows)

    def history_get(self, user_id: int, item_id: int) -> Optional[ReviewHistory]:
        with self._session() as s:
            rec = s.get(ReviewHistory, item_id)
            if rec is None or rec.user_id != user_id:
                return None
            return rec

    def history_get_case_variants(self, user_id: int, case_id: str) -> list[ReviewHistory]:
        """Все варианты конкретного кейса по case_id, в порядке возрастания id (1,2,3...)."""
        if not case_id:
            return []
        with self._session() as s:
            q = (
                s.query(ReviewHistory)
                .filter(ReviewHistory.user_id == user_id, ReviewHistory.case_id == case_id)
                .order_by(ReviewHistory.id.asc())
            )
            return list(q)

    # --- summary ---
    def summary_add_entry(self, user_id: int, *, source_type: str, options: dict, payload: dict,
                          result: Optional[dict]) -> int:
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

    # --- description ---
    def description_add(self, user_id: int, *, fields: dict, result_text: str) -> int:
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            # Не сохраняем пустые/пробельные результаты
            trimmed = (result_text or "").strip()
            if not trimmed:
                return 0
            rec = DescriptionHistory(
                user_id=user_id,
                msg_id=None,
                fields_json=json.dumps(fields or {}, ensure_ascii=False),
                result_text=trimmed,
            )
            s.add(rec)
            s.flush()
            return rec.id

    # --- description v2: start/finish by msgId + EAV options ---
    def description_start(self, user_id: int, *, msg_id: str, fields: dict) -> int:
        """
        Создаёт запись истории с msg_id и начальными данными (статус «processing» имплицитен —
        result_text пустой). Возвращает id записи.
        """
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = DescriptionHistory(
                user_id=user_id,
                msg_id=msg_id,
                fields_json=json.dumps(fields or {}, ensure_ascii=False),
                result_text="",  # обработка ещё идёт
            )
            s.add(rec)
            s.flush()
            return rec.id

    def description_finish_by_msgid(self, *, msg_id: str, result_text: str, fields: Optional[dict] = None) -> bool:
        """
        Обновляет запись по msgId финальным результатом (status «completed» имплицитен — result_text заполнен).
        Возвращает True, если запись найдена и обновлена.
        """
        if not msg_id:
            return False
        with self._session() as s, s.begin():
            rec = s.query(DescriptionHistory).filter(DescriptionHistory.msg_id == msg_id).one_or_none()
            if rec is None:
                return False
            if fields is not None:
                rec.fields_json = json.dumps(fields or {}, ensure_ascii=False)
            rec.result_text = result_text or ""
            s.flush()
            return True

    def description_options_save(self, *, msg_id: str, options: dict) -> int:
        """
        Сохраняет все параметры запроса в виде EAV (по одному ряду на параметр).
        Возвращает количество записанных опций.
        """
        if not msg_id:
            return 0
        rows = []
        for k, v in (options or {}).items():
            try:
                val = json.dumps(v, ensure_ascii=False)
            except Exception:
                val = str(v)
            rows.append(DescriptionOption(msg_id=msg_id, variable=str(k), value=val))
        if not rows:
            return 0
        with self._session() as s, s.begin():
            s.add_all(rows)
            return len(rows)

    def description_options_get(self, *, msg_id: str) -> dict:
        """Читает параметры генерации по msgId как dict variable->вalue (JSON-парс при возможности)."""
        if not msg_id:
            return {}
        with self._session() as s:
            items = (
                s.query(DescriptionOption.variable, DescriptionOption.value)
                .filter(DescriptionOption.msg_id == msg_id)
                .all()
            )
            out: dict = {}
            for var, val in items:
                try:
                    out[var] = json.loads(val)
                except Exception:
                    out[var] = val
            return out

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
                # Пропускаем пустые/пробельные результаты (не показываем «пустые» кнопки)
                preview_src = (rec.result_text or "")
                # Схлопываем все пробелы/переводы строк до одного пробела
                preview_clean = " ".join(preview_src.split())
                if not preview_clean:
                    continue
                items.append({
                    "id": rec.id,
                    "created_at": rec.created_at.isoformat(timespec="seconds"),
                    "preview": preview_clean[:60],
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
                "msg_id": rec.msg_id,
                "fields": json.loads(rec.fields_json or "{}"),
                "result_text": rec.result_text or "",
            }

    def description_get_by_msgid(self, msg_id: str) -> Optional[dict]:
        """Возвращает карточку описания по msgId (id, user_id, fields)."""
        if not msg_id:
            return None
        with self._session() as s:
            rec = (
                s.query(DescriptionHistory)
                .filter(DescriptionHistory.msg_id == msg_id)
                .one_or_none()
            )
            if rec is None:
                return None
            return {
                "id": rec.id,
                "user_id": rec.user_id,
                "fields": json.loads(rec.fields_json or "{}"),
            }

    def description_delete(self, user_id: int, entry_id: int) -> bool:
        with self._session() as s, s.begin():
            rec = s.get(DescriptionHistory, entry_id)
            if rec is None or rec.user_id != user_id:
                return False
            s.delete(rec)
            return True

    # --- events ---
    def event_add(self, user_id: int, text: str) -> None:
        """Сохраняет событие (user_id, сообщение и точный timestamp)."""
        ts = to_utc_for_db(now_msk())  # Для БД храним в UTC
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = EventLog(user_id=user_id, message=str(text), created_at=ts)
            s.add(rec)


# Глобальный репозиторий (app DB)
_repo = AppRepository(SessionLocal)
init_schema()


# ========= Facade (имена прежние, без «variables») =========
def init_db() -> None:
    init_schema()


def check_and_add_user(user_id: int, *, chat_id: Optional[int] = None, username: Optional[str] = None) -> bool:
    return _repo.ensure_user(user_id, chat_id=chat_id, username=username)


# Trial (возвращаем datetime, чтобы вызывать .date() в хендлере без плясок)
def set_trial(user_id: int, hours: int = 72) -> datetime:
    return _repo.set_trial(user_id, hours)


def get_trial_until(user_id: int) -> Optional[datetime]:
    return _repo.get_trial_until(user_id)


def get_trial_created_at(user_id: int) -> Optional[datetime]:
    return _repo.get_trial_created_at(user_id)


def is_trial_active(user_id: int) -> bool:
    return _repo.is_trial_active(user_id)


def trial_remaining_hours(user_id: int) -> int:
    return _repo.trial_remaining_hours(user_id)


def list_trial_active_user_ids(now: Optional[datetime] = None) -> list[int]:
    return _repo.list_trial_active_user_ids(now)


# Trial cooldown helpers
def trial_cooldown_days_left(user_id: int, *, cooldown_days: int = 60) -> int:
    return _repo.trial_cooldown_days_left(user_id, cooldown_days=cooldown_days)


def get_last_purchase_action_date(user_id: int) -> Optional[datetime]:
    """Возвращает дату последней итерации покупки (успех/неуспех, автосписание, любое действие пользователя)."""
    return _repo.get_last_purchase_action_date(user_id)

def is_trial_allowed(user_id: int, *, cooldown_days: int = 90) -> bool:
    """
    Разрешён ли повторный триал/покупка за 1 рубль с учётом кулдауна.
    Проверяет не только триал, но и любые покупки подписки.
    Кулдаун: 90 дней с последней итерации покупки.
    """
    return _repo.is_trial_allowed(user_id, cooldown_days=cooldown_days)


# History
def history_add(user_id: int, payload: dict, final_text: str, *, case_id: Optional[str] = None) -> ReviewHistory:
    return _repo.history_add(user_id, payload, final_text, case_id=case_id)


def history_list(user_id: int, limit: int = 10) -> list[ReviewHistory]:
    return _repo.history_list(user_id, limit)


def history_get(user_id: int, item_id: int) -> Optional[ReviewHistory]:
    return _repo.history_get(user_id, item_id)


def history_list_cases(user_id: int, limit: int = 10) -> list[ReviewHistory]:
    return _repo.history_list_cases(user_id, limit)


def history_get_case_variants(user_id: int, case_id: str) -> list[ReviewHistory]:
    return _repo.history_get_case_variants(user_id, case_id)


# Summary
def summary_add_entry(*, user_id: int, source_type: str, options: dict, payload: dict, result: Optional[dict]) -> int:
    return _repo.summary_add_entry(user_id, source_type=source_type, options=options, payload=payload, result=result)


def summary_list_entries(user_id: int, limit: int = 10) -> list[dict]:
    return _repo.summary_list_entries(user_id, limit=limit)


def summary_get_entry(user_id: int, entry_id: int) -> Optional[dict]:
    return _repo.summary_get_entry(user_id, entry_id)


# Descriptions
def description_add(*, user_id: int, fields: dict, result_text: str) -> int:
    return _repo.description_add(user_id, fields=fields, result_text=result_text)


# v2: msgId workflow
def description_start(*, user_id: int, msg_id: str, fields: dict) -> int:
    return _repo.description_start(user_id, msg_id=msg_id, fields=fields)


def description_finish_by_msgid(*, msg_id: str, result_text: str, fields: Optional[dict] = None) -> bool:
    return _repo.description_finish_by_msgid(msg_id=msg_id, result_text=result_text, fields=fields)


def description_options_save(*, msg_id: str, options: dict) -> int:
    return _repo.description_options_save(msg_id=msg_id, options=options)


def description_options_get(*, msg_id: str) -> dict:
    return _repo.description_options_get(msg_id=msg_id)


def description_list(user_id: int, limit: int = 10) -> list[dict]:
    return _repo.description_list(user_id, limit=limit)


def description_get(user_id: int, entry_id: int) -> Optional[dict]:
    return _repo.description_get(user_id, entry_id)


def description_get_by_msgid(msg_id: str) -> Optional[dict]:
    return _repo.description_get_by_msgid(msg_id)


def description_delete(user_id: int, entry_id: int) -> bool:
    return _repo.description_delete(user_id, entry_id)


# Consents
def add_consent(user_id: int, kind: str, when: Optional[datetime] = None) -> int:
    return _repo.add_consent(user_id, kind, when)


# Event log (простой интерфейс)
def event_add(user_id: int, text: str) -> None:
    return _repo.event_add(user_id, text)
#I'm using MYSQL8+ for this proj.