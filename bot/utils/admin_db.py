# smart_agent/bot/utils/admin_db.py
from __future__ import annotations

from typing import Optional, List, Tuple, Any, Dict
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import (
    create_engine, event, String, Integer, Boolean, UniqueConstraint
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
)

import bot.config as cfg


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    engine = create_engine(
        f"sqlite:///{cfg.ADMIN_DB_PATH}",
        future=True,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# =========================
#          Models
# =========================
class AdminUser(Base):
    __tablename__ = "Users"

    # Оставляем тип TEXT, как было в сырой БД
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    UserTag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    HaveSub: Mapped[int] = mapped_column(Integer, default=0)  # 0/1
    StartSub: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # 'YYYY-MM-DD'
    EndSub: Mapped[Optional[str]] = mapped_column(String, nullable=True)    # 'YYYY-MM-DD'
    Rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)     # месяцы


class Post(Base):
    __tablename__ = "Posts"

    MessageID: Mapped[int] = mapped_column(Integer, primary_key=True)
    Date: Mapped[str] = mapped_column(String)  # 'YYYY-MM-DD HH:MM:SS'


class NotificationMessage(Base):
    __tablename__ = "NotificationMessages"

    days_before: Mapped[int] = mapped_column(Integer, primary_key=True)
    message: Mapped[str] = mapped_column(String)


# =========================
#        Repository
# =========================
class AdminRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    # --- schema ---
    def init_schema(self) -> None:
        Base.metadata.create_all(bind=engine)

    def _s(self) -> Session:
        return self._sf()

    # --- users ---
    def inicialize_users(self, user_id: int, user_tag: str) -> None:
        uid = str(user_id)
        with self._s() as s, s.begin():
            user = s.get(AdminUser, uid)
            if user is None:
                s.add(AdminUser(user_id=uid, UserTag=user_tag))

    def get_all_users(self) -> List[Tuple]:
        with self._s() as s:
            rows = s.query(AdminUser).all()
            # совместимость: (user_id, HaveSub, StartSub, EndSub, UserTag)
            return [(u.user_id, u.HaveSub, u.StartSub, u.EndSub, u.UserTag) for u in rows]

    def get_my_info(self, user_id: int) -> Optional[Tuple]:
        with self._s() as s:
            u = s.get(AdminUser, str(user_id))
            if not u:
                return None
            # совместимость: (HaveSub, StartSub, EndSub)
            return (u.HaveSub, u.StartSub, u.EndSub)

    def add_sub_user(self, userid: int, months: int) -> None:
        uid = str(userid)
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        with self._s() as s, s.begin():
            u = s.get(AdminUser, uid)
            if u is None:
                u = AdminUser(user_id=uid, UserTag="", HaveSub=0)
                s.add(u)
                s.flush()

            # база расчёта — конец текущей подписки, если активна и в будущем
            base_date = now
            if u.EndSub:
                try:
                    current_end = datetime.strptime(u.EndSub, '%Y-%m-%d')
                    if current_end >= now:
                        base_date = current_end
                except Exception:
                    pass

            end_date = (base_date + relativedelta(months=months)).strftime('%Y-%m-%d')

            u.HaveSub = 1
            u.StartSub = today_str
            u.EndSub = end_date
            u.Rate = months

    def give_sub_manual(self, userid: int, start_date: str, end_date: str, rate: Optional[int] = None) -> None:
        uid = str(userid)
        with self._s() as s, s.begin():
            u = s.get(AdminUser, uid)
            if u is None:
                u = AdminUser(user_id=uid)
                s.add(u)
            u.HaveSub = 1
            u.StartSub = start_date
            u.EndSub = end_date
            u.Rate = rate

    def remove_expired_subscriptions(self) -> List[str]:
        removed: List[str] = []
        today = datetime.now().date()
        with self._s() as s, s.begin():
            users = s.query(AdminUser).filter(AdminUser.HaveSub == 1).all()
            for u in users:
                try:
                    if u.EndSub and datetime.strptime(u.EndSub, "%Y-%m-%d").date() < today:
                        u.HaveSub = 0
                        u.Rate = None
                        u.StartSub = None
                        u.EndSub = None
                        removed.append(u.user_id)
                except Exception:
                    continue
        return removed

    def check_user(self, user_id: int) -> Optional[Tuple]:
        with self._s() as s:
            u = s.get(AdminUser, str(user_id))
            if not u:
                return None
            # совместимость: (Rate, user_id, UserTag)
            return (u.Rate, u.user_id, u.UserTag)

    def check_sub_user(self, userid: int) -> bool:
        with self._s() as s:
            u = s.get(AdminUser, str(userid))
            return bool(u and u.HaveSub)

    # --- posts ---
    def save_new_post(self, date_str: str, message_id: int) -> None:
        with self._s() as s, s.begin():
            # REPLACE semantics: primary key on MessageID
            existing = s.get(Post, message_id)
            if existing:
                existing.Date = date_str
            else:
                s.add(Post(MessageID=message_id, Date=date_str))

    def get_posts_from_start_of_month(self) -> List[Dict[str, Any]]:
        with self._s() as s:
            now = datetime.now()
            start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

            posts = (
                s.query(Post)
                .all()
            )

        result: List[Dict[str, Any]] = []
        for p in posts:
            try:
                dt = datetime.strptime(p.Date, "%Y-%m-%d %H:%M:%S")
            except Exception:
                # пропускаем некорректную дату
                continue
            if start_month <= dt <= end_today:
                result.append({
                    "message_id": p.MessageID,
                    "date": dt,
                    "channel_id": cfg.CONTENT_CHANNEL_ID,
                })
        return result

    # --- notifications ---
    def get_notification_message(self, days_before: int) -> Optional[str]:
        with self._s() as s:
            nm = s.get(NotificationMessage, days_before)
            return nm.message if nm else None

    def get_all_notification_messages(self):
        with self._s() as s:
            rows = s.query(NotificationMessage).order_by(NotificationMessage.days_before.desc()).all()
            return [(r.days_before, r.message) for r in rows]

    def set_notification_message(self, days_before: int, message: str) -> bool:
        try:
            with self._s() as s, s.begin():
                nm = s.get(NotificationMessage, days_before)
                if nm:
                    nm.message = message
                else:
                    s.add(NotificationMessage(days_before=days_before, message=message))
            return True
        except Exception as e:
            print(f"set_notification_message error: {e}")
            return False

    def delete_notification_message(self, days_before: int) -> bool:
        with self._s() as s, s.begin():
            nm = s.get(NotificationMessage, days_before)
            if not nm:
                return False
            s.delete(nm)
            return True


# Глобальный репозиторий + совместимые функции
_repo = AdminRepository(SessionLocal)
_repo.init_schema()


def create_tables():
    _repo.init_schema()


def init_notification_table():
    _repo.init_schema()


def inicialize_users(user_id: int, user_tag: str):
    _repo.inicialize_users(user_id, user_tag)


def save_new_post(date_str: str, message_id: int):
    _repo.save_new_post(date_str, message_id)


def get_posts_from_start_of_month() -> List[Dict[str, Any]]:
    return _repo.get_posts_from_start_of_month()


def get_all_users() -> List[Tuple]:
    return _repo.get_all_users()


def get_my_info(user_id: int) -> Optional[Tuple]:
    return _repo.get_my_info(user_id)


def add_sub_user(userid: int, months: int):
    _repo.add_sub_user(userid, months)


def give_sub_manual(userid: int, start_date: str, end_date: str, rate: Optional[int] = None):
    _repo.give_sub_manual(userid, start_date, end_date, rate)


def remove_expired_subscriptions() -> List[str]:
    return _repo.remove_expired_subscriptions()


def check_user(user_id: int):
    return _repo.check_user(user_id)


def check_sub_user(userid: int) -> bool:
    return _repo.check_sub_user(userid)


def get_notification_message(days_before: int) -> Optional[str]:
    return _repo.get_notification_message(days_before)


def get_all_notification_messages():
    return _repo.get_all_notification_messages()


def set_notification_message(days_before: int, message: str) -> bool:
    return _repo.set_notification_message(days_before, message)


def delete_notification_message(days_before: int) -> bool:
    return _repo.delete_notification_message(days_before)
