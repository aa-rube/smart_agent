# smart_agent/bot/utils/admin_db.py
#Всегда пиши еодк без «поддержки старых версий». Если они есть в коде - удаляй.

from __future__ import annotations

import json
from typing import Optional, List, Tuple, Any, Dict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, String, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

MSK = ZoneInfo("Europe/Moscow")
import bot.config as cfg


# =========================
#     ORM Base & Engine
# =========================
class Base(DeclarativeBase):
    pass


def _make_engine():
    # Используем админскую базу данных MySQL
    engine = create_engine(
        cfg.ADMIN_DB_URL,
        future=True,
        echo=False,
        pool_pre_ping=True
    )

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# =========================
#          Models
# =========================
class AdminUser(Base):
    __tablename__ = "Users"

    # для PK в MySQL лучше задавать длину; 32 достаточно для TG id как строки
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # теги/псевдонимы — безопасно 191 (utf8mb4 индекс-френдли)
    UserTag: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    HaveSub: Mapped[int] = mapped_column(Integer, default=0)  # 0/1
    StartSub: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # 'YYYY-MM-DD'
    EndSub: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)    # 'YYYY-MM-DD'
    Rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)     # месяцы


class Mailing(Base):
    __tablename__ = "Mailings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(19))   # 'YYYY-MM-DD HH:MM:SS'
    publish_at: Mapped[str] = mapped_column(String(16))   # 'YYYY-MM-DD HH:MM'
    mailing_on: Mapped[int] = mapped_column(Integer, default=0)        # 0/1
    mailing_completed: Mapped[int] = mapped_column(Integer, default=0) # 0/1
    content_type: Mapped[str] = mapped_column(String(32)) # text/photo/video/audio/animation/media_group
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[str] = mapped_column(Text)            # JSON с file_ids/text


class NotificationMessage(Base):
    __tablename__ = "NotificationMessages"

    days_before: Mapped[int] = mapped_column(Integer, primary_key=True)
    message: Mapped[str] = mapped_column(Text)  # текст уведомления может быть длинным


# =========================
#      Helper functions
# =========================
def _norm_publish_at(s: str) -> str:
    """
    Нормализуем дату публикации к единому формату 'YYYY-MM-DD HH:MM'
    Поддерживаем входные форматы:
      - 'YYYY-MM-DD HH:MM'
      - 'YYYY-MM-DDTHH:MM'
      - 'YYYY-MM-DD HH:MM:SS'
      - 'YYYY-MM-DDTHH:MM:SS'
      - 'DD.MM.YYYY HH:MM'
    """
    s = (s or "").strip().replace("T", " ")
    tried = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M",
    ]
    for fmt in tried:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    # last resort: отрезать до минут
    if len(s) >= 16:
        return s[:16]
    return s


def _json_load(s: Optional[str]) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _json_dump(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False)


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
            return [(u.user_id, u.HaveSub, u.StartSub, u.EndSub, u.UserTag) for u in rows]

    def get_active_user_ids(self) -> List[str]:
        with self._s() as s:
            rows = s.query(AdminUser.user_id).filter(AdminUser.HaveSub == 1).all()
            return [r[0] for r in rows]

    def get_my_info(self, user_id: int) -> Optional[Tuple]:
        with self._s() as s:
            u = s.get(AdminUser, str(user_id))
            if not u:
                return None
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
        today = datetime.now(MSK).date()
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
            return (u.Rate, u.user_id, u.UserTag)

    def check_sub_user(self, userid: int) -> bool:
        with self._s() as s:
            u = s.get(AdminUser, str(userid))
            return bool(u and u.HaveSub)

    # --- mailings ---
    def create_scheduled_mailing(
        self,
        *,
        content_type: str,
        caption: Optional[str],
        payload: Dict[str, Any],
        publish_at: str,   # 'YYYY-MM-DD HH:MM' (или ISO, нормализуем)
        mailing_on: bool = True,
    ) -> int:
        # метка создания в МСК
        now_iso = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
        payload_json = _json_dump(payload)
        with self._s() as s, s.begin():
            m = Mailing(
                created_at=now_iso,
                publish_at=_norm_publish_at(publish_at),
                mailing_on=1 if mailing_on else 0,
                mailing_completed=0,
                content_type=content_type,
                caption=caption,
                payload=payload_json,
            )
            s.add(m)
            s.flush()
            return m.id

    def get_pending_mailings(self) -> List[Dict[str, Any]]:
        # сравниваем «пора слать» по МСК
        now_iso = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
        with self._s() as s:
            rows = (
                s.query(Mailing)
                .filter(Mailing.mailing_on == 1)
                .filter(Mailing.mailing_completed == 0)
                .filter(Mailing.publish_at <= now_iso)
                .order_by(Mailing.publish_at.asc())
                .all()
            )
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": r.id,
                "publish_at": r.publish_at,
                "content_type": r.content_type,
                "caption": r.caption,
                "payload": _json_load(r.payload),
            })
        return out

    def mark_mailing_completed(self, mailing_id: int) -> None:
        with self._s() as s, s.begin():
            m = s.get(Mailing, mailing_id)
            if m:
                m.mailing_completed = 1
                m.mailing_on = 0  # отключаем, чтобы не отправлялось повторно

    def get_last_publish_at(self) -> Optional[str]:
        """
        Максимальная дата publish_at из Mailings (строкой) или None, если записей нет.
        Формат в БД: 'YYYY-MM-DD HH:MM'
        """
        with self._s() as s:
            row = (
                s.query(Mailing.publish_at)
                .order_by(Mailing.publish_at.desc())
                .first()
            )
            return row[0] if row else None

    def get_scheduled_mailings(self, limit: int = 20, include_completed: bool = False) -> List[Dict[str, Any]]:
        with self._s() as s:
            q = s.query(Mailing)
            if not include_completed:
                q = q.filter(Mailing.mailing_completed == 0)
            rows = (
                q.order_by(Mailing.publish_at.asc())
                 .limit(limit)
                 .all()
            )
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": r.id,
                "created_at": r.created_at,
                "publish_at": r.publish_at,
                "mailing_on": r.mailing_on,
                "mailing_completed": r.mailing_completed,
                "content_type": r.content_type,
                "caption": r.caption,
                "payload": _json_load(r.payload),
            })
        return out

    def get_mailing_by_id(self, mailing_id: int) -> Optional[Dict[str, Any]]:
        with self._s() as s:
            r = s.get(Mailing, mailing_id)
            if not r:
                return None
            return {
                "id": r.id,
                "created_at": r.created_at,
                "publish_at": r.publish_at,
                "mailing_on": r.mailing_on,
                "mailing_completed": r.mailing_completed,
                "content_type": r.content_type,
                "caption": r.caption,
                "payload": _json_load(r.payload),
            }

    def update_mailing_publish_at(self, mailing_id: int, publish_at: str) -> bool:
        with self._s() as s, s.begin():
            m = s.get(Mailing, mailing_id)
            if not m:
                return False
            m.publish_at = _norm_publish_at(publish_at)
            return True

    def update_mailing_payload(
        self,
        *,
        mailing_id: int,
        content_type: str,
        payload: Dict[str, Any],
        caption: Optional[str] = None,
    ) -> bool:
        with self._s() as s, s.begin():
            m = s.get(Mailing, mailing_id)
            if not m:
                return False
            m.content_type = content_type
            m.payload = _json_dump(payload)
            m.caption = caption
            return True

    def update_mailing_text_or_caption(
        self,
        mailing_id: int,
        *,
        text: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> bool:
        """
        Если передан text — обновим payload['text'] (для content_type == 'text').
        Если передан caption — обновим caption (для медиа).
        Можно передавать по одному параметру.
        """
        if text is None and caption is None:
            return False
        with self._s() as s, s.begin():
            m = s.get(Mailing, mailing_id)
            if not m:
                return False
            if text is not None:
                payload = _json_load(m.payload)
                payload["text"] = text
                m.payload = _json_dump(payload)
            if caption is not None:
                m.caption = caption
            return True

    def delete_mailing(self, mailing_id: int) -> bool:
        with self._s() as s, s.begin():
            m = s.get(Mailing, mailing_id)
            if not m:
                return False
            s.delete(m)
            return True

    # --- calendar / counts ---
    def get_mailing_counts_map(
        self,
        start_iso: str,
        end_iso: str,
        only_pending: bool = True,
    ) -> Dict[str, int]:
        """
        Вернёт словарь {'YYYY-MM-DD': count} по рассылкам в диапазоне дат (включительно).
        Аргументы можно передавать как 'YYYY-MM-DD' или 'YYYY-MM-DD HH:MM'.
        По умолчанию считаем только невыполненные и включённые (mailing_on=1, mailing_completed=0).
        """
        def _as_start_bound(s: str) -> str:
            s = (s or "").strip().replace("T", " ")
            # если пришла только дата — берём начало суток
            if len(s) == 10:
                return f"{s} 00:00"
            return _norm_publish_at(s)

        def _as_end_bound(s: str) -> str:
            s = (s or "").strip().replace("T", " ")
            # если пришла только дата — берём конец суток
            if len(s) == 10:
                return f"{s} 23:59"
            return _norm_publish_at(s)

        start_b = _as_start_bound(start_iso)
        end_b = _as_end_bound(end_iso)

        day_expr = func.substr(Mailing.publish_at, 1, 10)  # 'YYYY-MM-DD'
        with self._s() as s:
            q = (
                s.query(day_expr.label("d"), func.count(Mailing.id).label("c"))
                 .filter(Mailing.publish_at >= start_b)
                 .filter(Mailing.publish_at <= end_b)
            )
            if only_pending:
                q = q.filter(Mailing.mailing_on == 1).filter(Mailing.mailing_completed == 0)
            rows = q.group_by(day_expr).all()
        return {r.d: int(r.c) for r in rows}

    # --- notifications ---
    def get_notification_message(self, days_before: int) -> Optional[str]:
        with self._s() as s:
            nm = s.get(NotificationMessage, days_before)
            return nm.message if nm else None

    def get_all_notification_messages(self) -> List[Tuple[int, str]]:
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

    def get_users_with_expiring_subscription(self, days_before: int) -> List[str]:
        if days_before < 0:
            return []
        target_str = (datetime.now(MSK).date() + timedelta(days=days_before)).strftime("%Y-%m-%d")
        with self._s() as s:
            rows = (
                s.query(AdminUser.user_id)
                .filter(AdminUser.HaveSub == 1)
                .filter(AdminUser.EndSub.isnot(None))
                .filter(AdminUser.EndSub == target_str)
                .all()
            )
        return [r[0] for r in rows]


# Глобальный репозиторий + совместимые функции
_repo = AdminRepository(SessionLocal)
_repo.init_schema()


def create_tables():
    _repo.init_schema()


def inicialize_users(user_id: int, user_tag: str) -> None:
    _repo.inicialize_users(user_id, user_tag)


def get_all_users() -> List[Tuple]:
    return _repo.get_all_users()


def get_active_user_ids() -> List[str]:
    return _repo.get_active_user_ids()


def get_my_info(user_id: int) -> Optional[Tuple]:
    return _repo.get_my_info(user_id)


def add_sub_user(userid: int, months: int) -> None:
    _repo.add_sub_user(userid, months)


def give_sub_manual(userid: int, start_date: str, end_date: str, rate: Optional[int] = None) -> None:
    _repo.give_sub_manual(userid, start_date, end_date, rate)


def remove_expired_subscriptions() -> List[str]:
    return _repo.remove_expired_subscriptions()


def check_user(user_id: int):
    return _repo.check_user(user_id)


def check_sub_user(userid: int) -> bool:
    return _repo.check_sub_user(userid)


def create_scheduled_mailing(
    *,
    content_type: str,
    caption: Optional[str],
    payload: Dict[str, Any],
    publish_at: str,
    mailing_on: bool = True,
) -> int:
    return _repo.create_scheduled_mailing(
        content_type=content_type,
        caption=caption,
        payload=payload,
        publish_at=publish_at,
        mailing_on=mailing_on,
    )


def get_pending_mailings() -> List[Dict[str, Any]]:
    return _repo.get_pending_mailings()


def mark_mailing_completed(mailing_id: int) -> None:
    _repo.mark_mailing_completed(mailing_id)


def get_last_publish_at() -> Optional[str]:
    return _repo.get_last_publish_at()


def get_scheduled_mailings(limit: int = 20, include_completed: bool = False) -> List[Dict[str, Any]]:
    return _repo.get_scheduled_mailings(limit=limit, include_completed=include_completed)


def get_mailing_by_id(mailing_id: int) -> Optional[Dict[str, Any]]:
    return _repo.get_mailing_by_id(mailing_id)


def update_mailing_publish_at(mailing_id: int, publish_at: str) -> bool:
    return _repo.update_mailing_publish_at(mailing_id, publish_at)


def update_mailing_payload(
    *,
    mailing_id: int,
    content_type: str,
    payload: Dict[str, Any],
    caption: Optional[str] = None,
) -> bool:
    return _repo.update_mailing_payload(
        mailing_id=mailing_id,
        content_type=content_type,
        payload=payload,
        caption=caption,
    )


def update_mailing_text_or_caption(
    mailing_id: int,
    *,
    text: Optional[str] = None,
    caption: Optional[str] = None,
) -> bool:
    return _repo.update_mailing_text_or_caption(
        mailing_id,
        text=text,
        caption=caption,
    )


def delete_mailing(mailing_id: int) -> bool:
    return _repo.delete_mailing(mailing_id)


def get_notification_message(days_before: int) -> Optional[str]:
    return _repo.get_notification_message(days_before)


def get_all_notification_messages() -> List[Tuple[int, str]]:
    return _repo.get_all_notification_messages()


def set_notification_message(days_before: int, message: str) -> bool:
    return _repo.set_notification_message(days_before, message)


def delete_notification_message(days_before: int) -> bool:
    return _repo.delete_notification_message(days_before)


def get_users_with_expiring_subscription(days_before: int) -> List[str]:
    return _repo.get_users_with_expiring_subscription(days_before)


def get_mailing_counts_map(start_iso: str, end_iso: str, only_pending: bool = True) -> Dict[str, int]:
    """
    Обёртка для репозитория. Возвращает {'YYYY-MM-DD': count}.
    """
    return _repo.get_mailing_counts_map(start_iso, end_iso, only_pending)
