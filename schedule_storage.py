import psycopg2
import psycopg2.extras
from datetime import timedelta

from config import DATABASE_URL


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def parse_time(s: str):
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def init_schedule_tables():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ad_fixed_times (
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        time_text VARCHAR(5) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        last_sent_date VARCHAR(10) NOT NULL DEFAULT ''
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


def reduce_expire_in_memory(rec: dict, days: int):
    expires_at = rec.get("expires_at", "")
    if not expires_at:
        return rec

    try:
        base = parse_time(expires_at)
    except Exception:
        return rec

    new_dt = base - timedelta(days=int(days))
    rec["expires_at"] = new_dt.strftime("%Y-%m-%d %H:%M:%S")
    return rec


def add_fixed_time(chat_id: int, time_text: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ad_fixed_times (chat_id, time_text, enabled, last_sent_date)
        VALUES (%s, %s, TRUE, '')
    """, (int(chat_id), time_text))
    conn.commit()
    cur.close()
    conn.close()


def get_fixed_times(chat_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM ad_fixed_times
        WHERE chat_id = %s
        ORDER BY time_text ASC
    """, (int(chat_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def delete_fixed_time(item_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ad_fixed_times WHERE id = %s", (int(item_id),))
    conn.commit()
    cur.close()
    conn.close()


def set_fixed_time_enabled(item_id: int, enabled: bool):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE ad_fixed_times SET enabled = %s WHERE id = %s", (enabled, int(item_id)))
    conn.commit()
    cur.close()
    conn.close()


def mark_fixed_time_sent(item_id: int, date_text: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE ad_fixed_times SET last_sent_date = %s WHERE id = %s", (date_text, int(item_id)))
    conn.commit()
    cur.close()
    conn.close()
