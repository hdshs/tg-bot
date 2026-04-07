import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta

from config import (
    DATABASE_URL,
    ADMIN_ID,
    DEFAULT_EXPIRE_DAYS,
    DEFAULT_GROUP_LIMIT,
    DEFAULT_DELETE_DELAY,
    DEFAULT_AD_INTERVAL_MINUTES,
    DEFAULT_AD_MAX_COUNT,
)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_time(s: str):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def format_time(s: str):
    if not s:
        return "永久"
    try:
        return parse_time(s).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(s)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 白名单
    cur.execute("""
    CREATE TABLE IF NOT EXISTS whitelist_users (
        user_id BIGINT PRIMARY KEY,
        added_at TEXT NOT NULL,
        expires_at TEXT NOT NULL DEFAULT '',
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        max_groups INTEGER NOT NULL DEFAULT 1,
        note TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'user',
        last_reminded_at TEXT NOT NULL DEFAULT ''
    )
    """)

    # 群配置
    cur.execute("""
    CREATE TABLE IF NOT EXISTS groups_data (
        chat_id BIGINT PRIMARY KEY,
        owner_id BIGINT,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        delay INTEGER NOT NULL DEFAULT 6,
        title TEXT NOT NULL DEFAULT '',
        bound_at TEXT NOT NULL DEFAULT '',
        ad_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        ad_interval_minutes INTEGER NOT NULL DEFAULT 60,
        ad_last_sent_at TEXT NOT NULL DEFAULT '',
        ad_max_count INTEGER NOT NULL DEFAULT 3,
        ad_rotate_index INTEGER NOT NULL DEFAULT 0
    )
    """)

    # pending actions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_actions (
        user_id BIGINT PRIMARY KEY,
        action_data TEXT NOT NULL DEFAULT '{}'
    )
    """)

    # 广告表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS group_ads (
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        content_type VARCHAR(20) NOT NULL DEFAULT 'text',
        text_content TEXT NOT NULL DEFAULT '',
        media_file_id TEXT NOT NULL DEFAULT '',
        media_url TEXT NOT NULL DEFAULT '',
        buttons_json TEXT NOT NULL DEFAULT '[]',
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)

    conn.commit()
    cur.close()
    conn.close()

    ensure_admin()


# =========================
# 超级管理员
# =========================
def ensure_admin():
    rec = get_whitelist_user(ADMIN_ID)
    if rec:
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO whitelist_users (
            user_id, added_at, expires_at, enabled, max_groups, note, role, last_reminded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
    """, (
        ADMIN_ID, now_str(), "", True, 999, "super_admin", "super_admin", ""
    ))
    conn.commit()
    cur.close()
    conn.close()


# =========================
# 白名单
# =========================
def get_whitelist_user(user_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM whitelist_users WHERE user_id = %s", (int(user_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_all_whitelist_users():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM whitelist_users ORDER BY user_id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def create_whitelist_user(user_id: int, expire_days: int = DEFAULT_EXPIRE_DAYS, max_groups: int = DEFAULT_GROUP_LIMIT):
    expires_at = (datetime.now() + timedelta(days=expire_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO whitelist_users (
            user_id, added_at, expires_at, enabled, max_groups, note, role, last_reminded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            expires_at = EXCLUDED.expires_at,
            enabled = EXCLUDED.enabled,
            max_groups = EXCLUDED.max_groups
    """, (
        int(user_id), now_str(), expires_at, True, int(max_groups), "", "user", ""
    ))
    conn.commit()
    cur.close()
    conn.close()


def delete_whitelist_user(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM whitelist_users WHERE user_id = %s", (int(user_id),))
    conn.commit()
    cur.close()
    conn.close()


def update_whitelist_enabled(user_id: int, enabled: bool):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE whitelist_users SET enabled = %s WHERE user_id = %s", (enabled, int(user_id)))
    conn.commit()
    cur.close()
    conn.close()


def update_whitelist_max_groups(user_id: int, max_groups: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE whitelist_users SET max_groups = %s WHERE user_id = %s", (int(max_groups), int(user_id)))
    conn.commit()
    cur.close()
    conn.close()


def extend_whitelist_days(user_id: int, days: int):
    rec = get_whitelist_user(user_id)
    if not rec:
        return

    current_expires = rec.get("expires_at", "")
    if current_expires:
        try:
            base = parse_time(current_expires)
            if base < datetime.now():
                base = datetime.now()
        except Exception:
            base = datetime.now()
    else:
        base = datetime.now()

    new_expires = (base + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE whitelist_users SET expires_at = %s WHERE user_id = %s", (new_expires, int(user_id)))
    conn.commit()
    cur.close()
    conn.close()


def is_super_admin(user_id: int) -> bool:
    return int(user_id) == ADMIN_ID


def is_whitelist_user(user_id: int) -> bool:
    rec = get_whitelist_user(user_id)
    if not rec:
        return False
    if not rec.get("enabled", False):
        return False

    expires_at = rec.get("expires_at", "")
    if expires_at:
        try:
            if parse_time(expires_at) < datetime.now():
                return False
        except Exception:
            return False

    return True


def user_status_text(rec: dict):
    if not rec.get("enabled", False):
        return "已关闭"

    expires_at = rec.get("expires_at", "")
    if expires_at:
        try:
            if parse_time(expires_at) < datetime.now():
                return "已到期"
        except Exception:
            return "时间异常"
    return "已启用"


# =========================
# 群配置
# =========================
def get_group(chat_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM groups_data WHERE chat_id = %s", (int(chat_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_groups_by_owner(user_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM groups_data WHERE owner_id = %s ORDER BY chat_id ASC", (int(user_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def count_owned_groups(user_id: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM groups_data WHERE owner_id = %s", (int(user_id),))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def can_add_more_groups(user_id: int) -> bool:
    rec = get_whitelist_user(user_id)
    if not rec:
        return False
    max_groups = int(rec.get("max_groups", 0))
    current = count_owned_groups(user_id)
    return current < max_groups


def ensure_group(chat_id: int):
    rec = get_group(chat_id)
    if rec:
        return rec

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO groups_data (
            chat_id, owner_id, enabled, delay, title, bound_at,
            ad_enabled, ad_interval_minutes, ad_last_sent_at, ad_max_count, ad_rotate_index
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chat_id) DO NOTHING
    """, (
        int(chat_id), None, True, DEFAULT_DELETE_DELAY, "", now_str(),
        False, DEFAULT_AD_INTERVAL_MINUTES, "", DEFAULT_AD_MAX_COUNT, 0
    ))
    conn.commit()
    cur.close()
    conn.close()
    return get_group(chat_id)


def bind_group(chat_id: int, owner_id: int, title: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO groups_data (
            chat_id, owner_id, enabled, delay, title, bound_at,
            ad_enabled, ad_interval_minutes, ad_last_sent_at, ad_max_count, ad_rotate_index
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET
            owner_id = EXCLUDED.owner_id,
            title = EXCLUDED.title,
            enabled = TRUE
    """, (
        int(chat_id), int(owner_id), True, DEFAULT_DELETE_DELAY, title or "", now_str(),
        False, DEFAULT_AD_INTERVAL_MINUTES, "", DEFAULT_AD_MAX_COUNT, 0
    ))
    conn.commit()
    cur.close()
    conn.close()


def update_group_field(chat_id: int, field: str, value):
    allowed = {
        "enabled",
        "delay",
        "title",
        "ad_enabled",
        "ad_interval_minutes",
        "ad_last_sent_at",
        "ad_max_count",
        "ad_rotate_index",
    }
    if field not in allowed:
        raise ValueError(f"不允许更新字段: {field}")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE groups_data SET {field} = %s WHERE chat_id = %s", (value, int(chat_id)))
    conn.commit()
    cur.close()
    conn.close()


# =========================
# pending actions
# =========================
def get_pending_action(user_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT action_data FROM pending_actions WHERE user_id = %s", (int(user_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None

    try:
        return json.loads(row["action_data"])
    except Exception:
        return None


def set_pending_action(user_id: int, action_data: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pending_actions (user_id, action_data)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            action_data = EXCLUDED.action_data
    """, (int(user_id), json.dumps(action_data, ensure_ascii=False)))
    conn.commit()
    cur.close()
    conn.close()


def clear_pending_action(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_actions WHERE user_id = %s", (int(user_id),))
    conn.commit()
    cur.close()
    conn.close()


# =========================
# 广告
# =========================
def add_ad(chat_id: int, content_type: str, text_content: str = "", media_file_id: str = "", media_url: str = "", buttons=None):
    if buttons is None:
        buttons = []

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO group_ads (
            chat_id, content_type, text_content, media_file_id, media_url, buttons_json, enabled
        ) VALUES (%s, %s, %s, %s, %s, %s, TRUE)
    """, (
        int(chat_id),
        content_type,
        text_content or "",
        media_file_id or "",
        media_url or "",
        json.dumps(buttons, ensure_ascii=False)
    ))
    conn.commit()
    cur.close()
    conn.close()


def get_ads(chat_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM group_ads WHERE chat_id = %s ORDER BY id ASC", (int(chat_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_enabled_ads(chat_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM group_ads WHERE chat_id = %s AND enabled = TRUE ORDER BY id ASC", (int(chat_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_ad(ad_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM group_ads WHERE id = %s", (int(ad_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def set_ad_enabled(ad_id: int, enabled: bool):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE group_ads SET enabled = %s WHERE id = %s", (enabled, int(ad_id)))
    conn.commit()
    cur.close()
    conn.close()


def delete_ad(ad_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM group_ads WHERE id = %s", (int(ad_id),))
    conn.commit()
    cur.close()
    conn.close()


def ads_count(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM group_ads WHERE chat_id = %s", (int(chat_id),))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count
