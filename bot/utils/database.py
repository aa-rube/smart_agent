# smart_agent/bot/utils/database.py
from __future__ import annotations

from typing import Optional, Any, List
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine,
    String, Integer, BigInteger, ForeignKey, DateTime, Text
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    sessionmaker, Session
)

from bot.config import DB_URL
import json


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    engine = create_engine(
        DB_URL,  # Используем DB_URL вместо DB_PATH
        future=True,
        echo=False,
        pool_pre_ping=True,
    )

    return engine


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # метаданные запроса
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "text" | "audio" | "unknown"
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # данные
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)   # вход/контекст
    result_json:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # результат анализа

    user: Mapped[User] = relationship(back_populates="summaries")


# =========================
#       Repository
# =========================
class UserRepository:
    """
    Аналог spring-repository: короткоживущие сессии, чистые методы,
    никакого сырого SQL.
    """

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    # --- schema ---
    def init_schema(self) -> None:
        Base.metadata.create_all(bind=engine)

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
                s.add_all([
                    Variable(user_id=user_id, variable_name="have_sub", variable_value="0"),
                    Variable(
                        user_id=user_id, variable_name="trial_until",
                        variable_value=(datetime.utcnow() + timedelta(hours=72)).isoformat(timespec="seconds") + "Z"
                    ),
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



    # --- trial helpers ---
    def set_trial(self, user_id: int, hours: int = 72) -> str:
        until = datetime.utcnow() + timedelta(hours=int(hours))
        iso = until.isoformat(timespec="seconds") + "Z"
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
            until = datetime.fromisoformat(ts)
        except Exception:
            return False
        return datetime.utcnow() < until

    def trial_remaining_hours(self, user_id: int) -> int:
        raw = self.get_trial_until(user_id)
        if not raw:
            return 0
        try:
            ts = raw[:-1] if raw.endswith("Z") else raw
            until = datetime.fromisoformat(ts)
            return max(0, int((until - datetime.utcnow()).total_seconds() // 3600))
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


# Глобальный «репозиторий» для обратной совместимости
_repo = UserRepository(SessionLocal)
_repo.init_schema()


# =========================
#   Совместимые функции
# =========================
def init_db() -> None:
    """Оставлено для совместимости."""
    _repo.init_schema()


def set_variable(user_id: int, variable_name: str, variable_value: Any) -> Any:
    """Оставлено для совместимости."""
    return _repo.set_var(user_id, variable_name, variable_value)


def get_variable(user_id: int, variable_name: str) -> Optional[str]:
    """Оставлено для совместимости."""
    return _repo.get_var(user_id, variable_name)


def check_and_add_user(user_id: int) -> bool:
    """
    Совместимость с прежним контрактом:
    True  — пользователь уже был,
    False — только что добавлен (проставлены дефолтные переменные).
    """
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
