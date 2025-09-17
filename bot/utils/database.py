# smart_agent/bot/utils/database.py
from __future__ import annotations

from typing import Optional, Any, Iterable, List
from datetime import datetime

from sqlalchemy import (
    create_engine, event,
    String, Integer, ForeignKey, DateTime, Text
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    sessionmaker, Session
)

from bot.config import DB_PATH


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        future=True,
        echo=False,           # при необходимости включай лог SQL
    )

    # Включаем каскады FK в SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# =========================
#          Models
# =========================
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
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


class Variable(Base):
    __tablename__ = "variables"

    # Композитный PK: (user_id, variable_name)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True
    )
    variable_name: Mapped[str] = mapped_column(String, primary_key=True)
    variable_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    user: Mapped[User] = relationship(back_populates="variables")


class ReviewHistory(Base):
    """
    История сгенерированных черновиков.
    Храним «плоские» поля payload, чтобы не городить JSON в SQLite.
    """
    __tablename__ = "review_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # payload
    client_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_name:  Mapped[Optional[str]] = mapped_column(String, nullable=True)
    company:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
    city:        Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
    deal_type:   Mapped[Optional[str]] = mapped_column(String, nullable=True)  # CSV кодов (sale,buy,...) + 'custom'
    deal_custom: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    situation:   Mapped[Optional[str]] = mapped_column(Text,   nullable=True)
    style:       Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # результат
    final_text:  Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="history")


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
        Одновременно проставляет дефолты (tokens=2, have_sub=0).
        """
        with self._session() as s, s.begin():
            existed = s.get(User, user_id) is not None
            if not existed:
                u = User(user_id=user_id)
                s.add(u)
                s.add_all([
                    Variable(user_id=user_id, variable_name="tokens",   variable_value="2"),
                    Variable(user_id=user_id, variable_name="have_sub", variable_value="0"),
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

    # --- tokens helpers (на случай прямого использования репозитория) ---
    def get_tokens(self, user_id: int) -> int:
        raw = self.get_var(user_id, "tokens")
        try:
            return int(raw) if raw is not None else 0
        except Exception:
            return 0

    def set_tokens(self, user_id: int, value: int) -> int:
        self.set_var(user_id, "tokens", int(value))
        return int(value)

    def add_tokens(self, user_id: int, delta: int) -> int:
        new_val = self.get_tokens(user_id) + int(delta)
        self.set_tokens(user_id, new_val)
        return new_val

    def remove_tokens(self, user_id: int, delta: int = 1) -> int:
        new_val = max(0, self.get_tokens(user_id) - int(delta))
        self.set_tokens(user_id, new_val)
        return new_val

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


# Необязательно, но удобно (если где-то импортируешь напрямую токены из db)
def get_tokens(user_id: int) -> int:
    return _repo.get_tokens(user_id)


def add_tokens(user_id: int, n: int) -> int:
    return _repo.add_tokens(user_id, n)


def remove_tokens(user_id: int, n: int = 1) -> int:
    return _repo.remove_tokens(user_id, n)


# -------- History compat wrappers --------
def history_add(user_id: int, payload: dict, final_text: str) -> ReviewHistory:
    return _repo.history_add(user_id, payload, final_text)


def history_list(user_id: int, limit: int = 10) -> list[ReviewHistory]:
    return _repo.history_list(user_id, limit)


def history_get(user_id: int, item_id: int) -> Optional[ReviewHistory]:
    return _repo.history_get(user_id, item_id)
