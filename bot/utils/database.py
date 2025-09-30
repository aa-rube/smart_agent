# smart_agent/bot/utils/database.py
from __future__ import annotations

from typing import Optional, Any, List, Dict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    create_engine, String, Integer, BigInteger, ForeignKey, DateTime, Text
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    sessionmaker, Session
)

from bot.config import DB_URL
import json

MSK = ZoneInfo("Europe/Moscow")


# ──────────────────────────────────────────────────────────────────────────────
# UTC helpers
# ──────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_text: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="descriptions")


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
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    user: Mapped[User] = relationship(back_populates="events")


# =========================
#       Repository
# =========================
def init_schema() -> None:
    Base.metadata.create_all(bind=engine)


class AppRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # --- users ---
    def ensure_user(self, user_id: int) -> bool:
        with self._session() as s, s.begin():
            existed = s.get(User, user_id) is not None
            if not existed:
                s.add(User(user_id=user_id))
            return existed

    # --- consents ---
    def add_consent(self, user_id: int, kind: str, when: Optional[datetime] = None) -> int:
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = UserConsent(user_id=user_id, kind=kind, accepted_at=when or now_utc())
            s.add(rec)
            s.flush()
            return rec.id

    # --- trials ---
    def set_trial(self, user_id: int, hours: int = 72) -> datetime:
        until = now_utc() + timedelta(hours=int(hours))
        with self._session() as s, s.begin():
            if s.get(User, user_id) is None:
                s.add(User(user_id=user_id))
            rec = s.get(Trial, user_id)
            if rec is None:
                s.add(Trial(user_id=user_id, until_at=until))
            else:
                rec.until_at = until
                rec.updated_at = now_utc()
        return until

    def get_trial_until(self, user_id: int) -> Optional[datetime]:
        with self._session() as s:
            rec = s.get(Trial, user_id)
            return rec.until_at if rec else None

    def is_trial_active(self, user_id: int) -> bool:
        until = self.get_trial_until(user_id)
        return bool(until and now_utc() < until)

    def trial_remaining_hours(self, user_id: int) -> int:
        until = self.get_trial_until(user_id)
        if not until:
            return 0
        return max(0, int((until - now_utc()).total_seconds() // 3600))

    # --- history ---
    def history_add(self, user_id: int, payload: dict, final_text: str) -> ReviewHistory:
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
            s.flush()
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

    # --- events ---
    def event_add(self, user_id: int, text: str) -> None:
        """Сохраняет событие (user_id, сообщение и точный timestamp)."""
        ts = now_utc()
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


def check_and_add_user(user_id: int) -> bool:
    return _repo.ensure_user(user_id)


# Trial (возвращаем datetime, чтобы вызывать .date() в хендлере без плясок)
def set_trial(user_id: int, hours: int = 72) -> datetime:
    return _repo.set_trial(user_id, hours)


def get_trial_until(user_id: int) -> Optional[datetime]:
    return _repo.get_trial_until(user_id)


def is_trial_active(user_id: int) -> bool:
    return _repo.is_trial_active(user_id)


def trial_remaining_hours(user_id: int) -> int:
    return _repo.trial_remaining_hours(user_id)


# History
def history_add(user_id: int, payload: dict, final_text: str) -> ReviewHistory:
    return _repo.history_add(user_id, payload, final_text)


def history_list(user_id: int, limit: int = 10) -> list[ReviewHistory]:
    return _repo.history_list(user_id, limit)


def history_get(user_id: int, item_id: int) -> Optional[ReviewHistory]:
    return _repo.history_get(user_id, item_id)


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


def description_list(user_id: int, limit: int = 10) -> list[dict]:
    return _repo.description_list(user_id, limit=limit)


def description_get(user_id: int, entry_id: int) -> Optional[dict]:
    return _repo.description_get(user_id, entry_id)


def description_delete(user_id: int, entry_id: int) -> bool:
    return _repo.description_delete(user_id, entry_id)


# Consents
def add_consent(user_id: int, kind: str, when: Optional[datetime] = None) -> int:
    return _repo.add_consent(user_id, kind, when)


# Event log (простой интерфейс)
def event_add(user_id: int, text: str) -> None:
    return _repo.event_add(user_id, text)
