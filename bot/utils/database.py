#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\database.py

import sqlite3
from bot.config import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS variables (
            user_id INTEGER NOT NULL,
            variable_name TEXT NOT NULL,
            variable_value TEXT,
            PRIMARY KEY (user_id, variable_name),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

def set_variable(user_id, variable_name, variable_value):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO variables (user_id, variable_name, variable_value)
        VALUES (?, ?, ?)
    """, (user_id, variable_name, str(variable_value)))
    conn.commit()
    conn.close()
    return variable_value

def get_variable(user_id, variable_name):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT variable_value FROM variables WHERE user_id = ? AND variable_name = ?
    """, (user_id, variable_name))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def check_and_add_user(user_id):
    """Возвращает True, если уже был, False — если новый. Для нового ставим дефолты."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        # дефолты: 2 токена, подписки нет
        cur.execute("""
            INSERT OR IGNORE INTO variables (user_id, variable_name, variable_value)
            VALUES (?, 'tokens', '2')
        """, (user_id,))
        cur.execute("""
            INSERT OR IGNORE INTO variables (user_id, variable_name, variable_value)
            VALUES (?, 'have_sub', '0')
        """, (user_id,))
        conn.commit()
    conn.close()
    return exists