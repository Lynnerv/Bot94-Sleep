import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DATABASE_FILE, TIMEZONE


def now_iso():
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


@contextmanager
def db():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db():
    with db() as connection:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(schedules)").fetchall()}
        if existing and "date" not in existing:
            connection.execute("DROP TABLE IF EXISTS schedule_executions")
            connection.execute("DROP TABLE IF EXISTS schedules")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                channel_id INTEGER,
                message_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)

        schedule_columns = {row[1] for row in connection.execute("PRAGMA table_info(schedules)").fetchall()}
        if "channel_id" not in schedule_columns:
            connection.execute("ALTER TABLE schedules ADD COLUMN channel_id INTEGER")
        if "message_id" not in schedule_columns:
            connection.execute("ALTER TABLE schedules ADD COLUMN message_id INTEGER")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS schedule_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                execution_date TEXT NOT NULL,
                original_datetime TEXT NOT NULL,
                override_datetime TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                reminder_channel_id INTEGER,
                reminder_message_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(schedule_id, execution_date)
            )
        """)


init_db()


def create_schedule(user_id, guild_id, date, time, channel_id=None, message_id=None):
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO schedules (user_id, guild_id, date, time, enabled, channel_id, message_id, created_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (int(user_id), int(guild_id), date, time, channel_id, message_id, now_iso()),
        )
        return cursor.lastrowid


def get_schedules(user_id=None, guild_id=None, enabled_only=False):
    query = "SELECT * FROM schedules WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(int(user_id))
    if guild_id is not None:
        query += " AND guild_id = ?"
        params.append(int(guild_id))
    if enabled_only:
        query += " AND enabled = 1"
    query += " ORDER BY date, time, id"
    with db() as connection:
        return list(connection.execute(query, params).fetchall())


def get_schedule(schedule_id):
    with db() as connection:
        return connection.execute("SELECT * FROM schedules WHERE id = ?", (int(schedule_id),)).fetchone()


def update_schedule(schedule_id, **fields):
    allowed = {"date", "time", "enabled", "channel_id", "message_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    with db() as connection:
        cursor = connection.execute(
            "UPDATE schedules SET " + ",".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
            list(fields.values()) + [int(schedule_id)],
        )
        return cursor.rowcount > 0


def delete_schedule(schedule_id, user_id=None):
    with db() as connection:
        connection.execute("DELETE FROM schedule_executions WHERE schedule_id = ?", (int(schedule_id),))
        if user_id is None:
            cursor = connection.execute("DELETE FROM schedules WHERE id = ?", (int(schedule_id),))
        else:
            cursor = connection.execute(
                "DELETE FROM schedules WHERE id = ? AND user_id = ?",
                (int(schedule_id), int(user_id)),
            )
        return cursor.rowcount > 0


def get_execution(schedule_id, execution_date):
    with db() as connection:
        return connection.execute(
            "SELECT * FROM schedule_executions WHERE schedule_id = ? AND execution_date = ?",
            (int(schedule_id), execution_date),
        ).fetchone()


def create_execution(schedule_id, execution_date, original_datetime):
    current = now_iso()
    with db() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO schedule_executions
               (schedule_id, execution_date, original_datetime, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (int(schedule_id), execution_date, original_datetime, current, current),
        )
    return get_execution(schedule_id, execution_date)


def update_execution(execution_id, **fields):
    allowed = {
        "override_datetime", "status", "reminder_sent", "confirmed",
        "reminder_channel_id", "reminder_message_id"
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    fields["updated_at"] = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "UPDATE schedule_executions SET " + ",".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
            list(fields.values()) + [int(execution_id)],
        )
        return cursor.rowcount > 0
