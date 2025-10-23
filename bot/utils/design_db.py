from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import create_engine, String, Integer, BigInteger, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

from bot.config import DB_URL

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

def _make_engine():
    return create_engine(DB_URL, future=True, echo=False, pool_pre_ping=True)

engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

class DesignGeneration(Base):
    """
    Жёсткая схема хранения параметров генерации и привязки к message_id результата.
    """
    __tablename__ = "design_generation"
    __table_args__ = (UniqueConstraint("result_msg_id", name="uq_design_generation_result_msg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_msg_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)          # 'redesign' | 'zero'
    style: Mapped[str] = mapped_column(String(64), nullable=False)
    room_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    furniture: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # furniture_yes|furniture_no|None

    src_image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    result_image_path: Mapped[str] = mapped_column(String(512), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

def init_schema() -> None:
    Base.metadata.create_all(bind=engine)

class DesignRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    def add_generation(self, *, result_msg_id: int, user_id: int, chat_id: int,
                       mode: str, style: str, room_type: Optional[str], furniture: Optional[str],
                       src_image_path: str, result_image_path: str) -> int:
        with self._sf() as s, s.begin():
            rec = DesignGeneration(
                result_msg_id=int(result_msg_id),
                user_id=int(user_id),
                chat_id=int(chat_id),
                mode=str(mode),
                style=str(style),
                room_type=room_type,
                furniture=furniture,
                src_image_path=src_image_path,
                result_image_path=result_image_path,
            )
            s.add(rec)
            s.flush()
            return rec.id

    def get_by_result_msg_id(self, *, result_msg_id: int) -> Optional[DesignGeneration]:
        with self._sf() as s:
            return (
                s.query(DesignGeneration)
                .filter(DesignGeneration.result_msg_id == int(result_msg_id))
                .one_or_none()
            )

_repo = DesignRepository(SessionLocal)
init_schema()

def save_generation_record(*, result_msg_id: int, user_id: int, chat_id: int,
                           mode: str, style: str, room_type: Optional[str], furniture: Optional[str],
                           src_image_path: str, result_image_path: str) -> int:
    return _repo.add_generation(
        result_msg_id=result_msg_id,
        user_id=user_id,
        chat_id=chat_id,
        mode=mode,
        style=style,
        room_type=room_type,
        furniture=furniture,
        src_image_path=src_image_path,
        result_image_path=result_image_path,
    )

def get_generation_by_result_msg_id(result_msg_id: int) -> Optional[DesignGeneration]:
    return _repo.get_by_result_msg_id(result_msg_id=result_msg_id)