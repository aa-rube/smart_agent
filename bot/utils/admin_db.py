# smart_agent/bot/utils/admin_db.py
import sqlite3
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import Optional, List, Tuple, Any, Dict

import bot.config as cfg

DB_PATH = cfg.ADMIN_DB_PATH  # отдельная БД для админ-фич (как в KWORK)

def _conn():
    return sqlite3.connect(DB_PATH)

def create_tables():
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS Users(
            user_id TEXT PRIMARY KEY,
            UserTag TEXT,
            HaveSub INTEGER DEFAULT 0,
            StartSub TEXT,
            EndSub TEXT,
            Rate INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS Posts(
            MessageID INTEGER PRIMARY KEY,
            Date TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS NotificationMessages(
            days_before INTEGER PRIMARY KEY,
            message TEXT NOT NULL
        )""")
        bd.commit()

def init_notification_table():
    create_tables()

def inicialize_users(user_id: int, user_tag: str):
    create_tables()
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("INSERT OR IGNORE INTO Users (user_id, UserTag) VALUES (?, ?)", (str(user_id), user_tag))
        bd.commit()

def save_new_post(date_str: str, message_id: int):
    create_tables()
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("INSERT OR REPLACE INTO Posts (MessageID, Date) VALUES (?, ?)", (message_id, date_str))
        bd.commit()

def get_posts_from_start_of_month() -> list[Dict[str, Any]]:
    with _conn() as bd:
        cur = bd.cursor()
        now = datetime.now()
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        cur.execute("""
            SELECT MessageID, Date FROM Posts
            WHERE Date BETWEEN ? AND ?
        """, (start_month.strftime("%Y-%m-%d %H:%M:%S"), end_today.strftime("%Y-%m-%d %H:%M:%S")))
        rows = cur.fetchall()
    return [
        {
            "message_id": row[0],
            "date": datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S"),
            "channel_id": cfg.CONTENT_CHANNEL_ID,
        }
        for row in rows
    ]

def get_all_users() -> list[Tuple]:
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT user_id, HaveSub, StartSub, EndSub, UserTag FROM Users")
        return cur.fetchall()

def get_my_info(user_id: int) -> Optional[Tuple]:
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT HaveSub, StartSub, EndSub FROM Users WHERE user_id = ?", (str(user_id),))
        return cur.fetchone()

def add_sub_user(userid: int, months: int):
    with _conn() as bd:
        cur = bd.cursor()
        now = datetime.now()
        cur.execute("SELECT HaveSub, EndSub FROM Users WHERE user_id = ?", (str(userid),))
        row = cur.fetchone()
        if row and row[0]:
            try:
                current_end = datetime.strptime(row[1], '%Y-%m-%d')
                base_date = current_end if current_end >= now else now
            except Exception:
                base_date = now
        else:
            base_date = now
        end_date = base_date + relativedelta(months=months)
        cur.execute("SELECT user_id FROM Users WHERE user_id = ?", (str(userid),))
        if cur.fetchone():
            cur.execute("""UPDATE Users SET 
                HaveSub=1, StartSub=?, EndSub=?, Rate=?
                WHERE user_id=?""",
                (now.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), months, str(userid)))
        else:
            cur.execute("""INSERT INTO Users (user_id, HaveSub, StartSub, EndSub, Rate, UserTag)
                VALUES (?, 1, ?, ?, ?, ?)""",
                (str(userid), now.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), months, ""))
        bd.commit()

def give_sub_manual(userid: int, start_date: str, end_date: str, rate: Optional[int] = None):
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT user_id FROM Users WHERE user_id=?", (str(userid),))
        if cur.fetchone():
            cur.execute("""UPDATE Users SET HaveSub=1, StartSub=?, EndSub=?, Rate=? WHERE user_id=?""",
                        (start_date, end_date, rate, str(userid)))
        else:
            cur.execute("""INSERT INTO Users (user_id, HaveSub, StartSub, EndSub, Rate)
                           VALUES (?, 1, ?, ?, ?)""", (str(userid), start_date, end_date, rate))
        bd.commit()

def remove_expired_subscriptions() -> list[str]:
    removed: list[str] = []
    today = datetime.now().date()
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT user_id, EndSub FROM Users WHERE HaveSub=1")
        for user_id, end_sub in cur.fetchall():
            try:
                end_date = datetime.strptime(end_sub, "%Y-%m-%d").date()
                if today > end_date:
                    cur.execute("""UPDATE Users
                                   SET HaveSub=0, Rate=NULL, StartSub=NULL, EndSub=NULL
                                   WHERE user_id=?""", (user_id,))
                    removed.append(user_id)
            except Exception:
                continue
        bd.commit()
    return removed

def check_user(user_id: int):
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT Rate, user_id, UserTag FROM Users WHERE user_id = ?", (str(user_id),))
        return cur.fetchone()

def check_sub_user(userid: int) -> bool:
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT HaveSub FROM Users WHERE user_id = ?", (str(userid),))
        row = cur.fetchone()
        return bool(row and row[0])

# ---- Notifications table helpers ----
def get_notification_message(days_before: int) -> Optional[str]:
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT message FROM NotificationMessages WHERE days_before=?", (days_before,))
        row = cur.fetchone()
        return row[0] if row else None

def get_all_notification_messages():
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT days_before, message FROM NotificationMessages ORDER BY days_before DESC")
        return cur.fetchall()

def set_notification_message(days_before: int, message: str) -> bool:
    try:
        with _conn() as bd:
            cur = bd.cursor()
            cur.execute("""
                INSERT INTO NotificationMessages(days_before, message) VALUES(?, ?)
                ON CONFLICT(days_before) DO UPDATE SET message=excluded.message
            """, (days_before, message))
            bd.commit()
        return True
    except Exception as e:
        print(f"set_notification_message error: {e}")
        return False

def delete_notification_message(days_before: int) -> bool:
    with _conn() as bd:
        cur = bd.cursor()
        cur.execute("SELECT 1 FROM NotificationMessages WHERE days_before=?", (days_before,))
        if cur.fetchone() is None:
            return False
        cur.execute("DELETE FROM NotificationMessages WHERE days_before=?", (days_before,))
        bd.commit()
        return True
