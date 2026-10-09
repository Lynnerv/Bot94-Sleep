import sqlite3
from contextlib import contextmanager
from datetime import datetime
from config import DATABASE_FILE, TIMEZONE

@contextmanager
def db():
    connection = sqlite3.connect(DATABASE_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def now_iso():
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")

def init_db():
    with db() as con:
        con.execute("""
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
        )""")
        columns = {r["name"] for r in con.execute("PRAGMA table_info(schedules)")}
        for name, declaration in (("channel_id", "INTEGER"), ("message_id", "INTEGER")):
            if name not in columns:
                con.execute(f"ALTER TABLE schedules ADD COLUMN {name} {declaration}")
        con.execute("""
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
            UNIQUE(schedule_id, execution_date),
            FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
        )""")

def create_schedule(user_id, guild_id, date, time, channel_id=None, message_id=None):
    with db() as con:
        cur = con.execute("""INSERT INTO schedules
        (user_id,guild_id,date,time,enabled,channel_id,message_id,created_at)
        VALUES (?,?,?,?,1,?,?,?)""",
        (int(user_id), int(guild_id), date, time, channel_id, message_id, now_iso()))
        return cur.lastrowid

def get_schedules(user_id=None, guild_id=None, enabled_only=False):
    sql = "SELECT * FROM schedules WHERE 1=1"
    args = []
    if user_id is not None:
        sql += " AND user_id=?"; args.append(int(user_id))
    if guild_id is not None:
        sql += " AND guild_id=?"; args.append(int(guild_id))
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY date,time,id"
    with db() as con:
        return list(con.execute(sql, args).fetchall())

def get_schedule(schedule_id):
    with db() as con:
        return con.execute("SELECT * FROM schedules WHERE id=?", (int(schedule_id),)).fetchone()

def update_schedule(schedule_id, **fields):
    allowed = {"date", "time", "enabled", "channel_id", "message_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    sql = "UPDATE schedules SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?"
    with db() as con:
        cur = con.execute(sql, [*fields.values(), int(schedule_id)])
        return cur.rowcount > 0

def delete_schedule(schedule_id, user_id=None, guild_id=None):
    sql = "DELETE FROM schedules WHERE id=?"
    args = [int(schedule_id)]
    if user_id is not None:
        sql += " AND user_id=?"; args.append(int(user_id))
    if guild_id is not None:
        sql += " AND guild_id=?"; args.append(int(guild_id))
    with db() as con:
        cur = con.execute(sql, args)
        if cur.rowcount:
            con.execute("DELETE FROM schedule_executions WHERE schedule_id=?", (int(schedule_id),))
        return cur.rowcount > 0

def get_execution(schedule_id, execution_date):
    with db() as con:
        return con.execute("SELECT * FROM schedule_executions WHERE schedule_id=? AND execution_date=?",
                           (int(schedule_id), execution_date)).fetchone()

def create_execution(schedule_id, execution_date, original_datetime):
    stamp = now_iso()
    with db() as con:
        con.execute("""INSERT OR IGNORE INTO schedule_executions
        (schedule_id,execution_date,original_datetime,created_at,updated_at)
        VALUES (?,?,?,?,?)""", (int(schedule_id), execution_date, original_datetime, stamp, stamp))
    return get_execution(schedule_id, execution_date)

def update_execution(execution_id, **fields):
    allowed = {"override_datetime","status","reminder_sent","confirmed",
               "reminder_channel_id","reminder_message_id"}
    fields = {k:v for k,v in fields.items() if k in allowed}
    if not fields:
        return False
    fields["updated_at"] = now_iso()
    sql = "UPDATE schedule_executions SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?"
    with db() as con:
        cur = con.execute(sql, [*fields.values(), int(execution_id)])
        return cur.rowcount > 0

init_db()
