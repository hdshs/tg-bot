import os
import json
import asyncio
import psycopg2
import psycopg2.extras
from pathlib import Path
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ===== 这里改成你自己的信息 =====
ADMIN_ID = 5593962796
ADMIN_USERNAME = "vyfjii"
# ===========================

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

WHITELIST_FILE = DATA_DIR / "whitelist_users.json"
GROUPS_FILE = DATA_DIR / "groups.json"
PENDING_FILE = DATA_DIR / "pending_actions.json"

DEFAULT_DELETE_DELAY = 6
DEFAULT_GROUP_LIMIT = 1
DEFAULT_EXPIRE_DAYS = 30

DEFAULT_AD_INTERVAL_MINUTES = 60
DEFAULT_AD_MAX_COUNT = 3

if not TOKEN:
    raise RuntimeError("未检测到 BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("未检测到 DATABASE_URL")


# =========================
# 工具函数
# =========================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_time(s: str):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def format_time(s: str):
    if not s:
        return "永久"
    try:
        dt = parse_time(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(s)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


whitelist_users = load_json(WHITELIST_FILE, {})
groups_data = load_json(GROUPS_FILE, {})
pending_actions = load_json(PENDING_FILE, {})


def persist_all():
    save_json(WHITELIST_FILE, whitelist_users)
    save_json(GROUPS_FILE, groups_data)
    save_json(PENDING_FILE, pending_actions)


# =========================
# PostgreSQL 广告数据库
# =========================
def get_db_conn():
    return psycopg2.connect(DATABASE_URL)


def init_ads_table():
    conn = get_db_conn()
    cur = conn.cursor()

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


def db_add_ad(chat_id: int, content_type: str, text_content: str = "", media_file_id: str = "", media_url: str = "", buttons=None):
    if buttons is None:
        buttons = []

    conn = get_db_conn()
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


def db_get_ads(chat_id: int):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM group_ads
        WHERE chat_id = %s
        ORDER BY id ASC
    """, (int(chat_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def db_get_enabled_ads(chat_id: int):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM group_ads
        WHERE chat_id = %s AND enabled = TRUE
        ORDER BY id ASC
    """, (int(chat_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def db_get_ad(ad_id: int):
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM group_ads
        WHERE id = %s
    """, (int(ad_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def db_delete_ad(ad_id: int):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM group_ads WHERE id = %s", (int(ad_id),))
    conn.commit()
    cur.close()
    conn.close()


def db_set_ad_enabled(ad_id: int, enabled: bool):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE group_ads
        SET enabled = %s
        WHERE id = %s
    """, (enabled, int(ad_id)))
    conn.commit()
    cur.close()
    conn.close()


def db_ads_count(chat_id: int):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM group_ads WHERE chat_id = %s", (int(chat_id),))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


# =========================
# 初始化超级管理员
# =========================
def ensure_admin():
    admin_key = str(ADMIN_ID)
    if admin_key not in whitelist_users:
        whitelist_users[admin_key] = {
            "user_id": ADMIN_ID,
            "added_at": now_str(),
            "expires_at": "",
            "enabled": True,
            "max_groups": 999,
            "note": "super_admin",
            "role": "super_admin",
            "last_reminded_at": ""
        }
        persist_all()


ensure_admin()


# =========================
# 白名单逻辑
# =========================
def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def get_user_record(user_id: int):
    return whitelist_users.get(str(user_id))


def is_whitelist_user(user_id: int) -> bool:
    rec = get_user_record(user_id)
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


def count_owned_groups(user_id: int) -> int:
    return sum(1 for _, cfg in groups_data.items() if cfg.get("owner_id") == user_id)


def can_add_more_groups(user_id: int) -> bool:
    rec = get_user_record(user_id)
    if not rec:
        return False
    max_groups = int(rec.get("max_groups", 0))
    current = count_owned_groups(user_id)
    return current < max_groups


def create_whitelist_user(user_id: int, expire_days: int = DEFAULT_EXPIRE_DAYS, max_groups: int = DEFAULT_GROUP_LIMIT):
    expires_at = (datetime.now() + timedelta(days=expire_days)).strftime("%Y-%m-%d %H:%M:%S")
    whitelist_users[str(user_id)] = {
        "user_id": user_id,
        "added_at": now_str(),
        "expires_at": expires_at,
        "enabled": True,
        "max_groups": max_groups,
        "note": "",
        "role": "user",
        "last_reminded_at": ""
    }
    persist_all()


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


def user_detail_text(user_id: int):
    rec = get_user_record(user_id)
    if not rec:
        return "未找到该用户。"

    owned = count_owned_groups(user_id)
    return (
        f"用户ID：{rec['user_id']}\n"
        f"状态：{user_status_text(rec)}\n"
        f"添加时间：{format_time(rec.get('added_at', ''))}\n"
        f"到期时间：{format_time(rec.get('expires_at', ''))}\n"
        f"群额度：{rec.get('max_groups', 0)}\n"
        f"已绑定群：{owned}\n"
        f"备注：{rec.get('note', '') or '无'}"
    )


# =========================
# 群配置
# =========================
def ensure_group_defaults(cfg: dict):
    if "owner_id" not in cfg:
        cfg["owner_id"] = None
    if "enabled" not in cfg:
        cfg["enabled"] = True
    if "delay" not in cfg:
        cfg["delay"] = DEFAULT_DELETE_DELAY
    if "title" not in cfg:
        cfg["title"] = ""
    if "bound_at" not in cfg:
        cfg["bound_at"] = now_str()
    if "ad_enabled" not in cfg:
        cfg["ad_enabled"] = False
    if "ad_interval_minutes" not in cfg:
        cfg["ad_interval_minutes"] = DEFAULT_AD_INTERVAL_MINUTES
    if "ad_last_sent_at" not in cfg:
        cfg["ad_last_sent_at"] = ""
    if "ad_max_count" not in cfg:
        cfg["ad_max_count"] = DEFAULT_AD_MAX_COUNT
    if "ad_rotate_index" not in cfg:
        cfg["ad_rotate_index"] = 0
    return cfg


def get_group_config(chat_id: int):
    cid = str(chat_id)
    if cid not in groups_data:
        groups_data[cid] = {
            "owner_id": None,
            "enabled": True,
            "delay": DEFAULT_DELETE_DELAY,
            "title": "",
            "bound_at": now_str(),
            "ad_enabled": False,
            "ad_interval_minutes": DEFAULT_AD_INTERVAL_MINUTES,
            "ad_last_sent_at": "",
            "ad_max_count": DEFAULT_AD_MAX_COUNT,
            "ad_rotate_index": 0,
        }
        persist_all()
    else:
        groups_data[cid] = ensure_group_defaults(groups_data[cid])
    return groups_data[cid]


async def is_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def delete_message_later(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


async def refresh_group_title(chat_id: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = await context.bot.get_chat(chat_id)
        groups_data[str(chat_id)]["title"] = chat.title or groups_data[str(chat_id)].get("title", "")
        persist_all()
    except Exception:
        pass


# =========================
# 广告逻辑
# =========================
def get_next_ad(chat_id: str):
    cfg = get_group_config(int(chat_id))
    ads = db_get_enabled_ads(int(chat_id))

    if not ads:
        return None

    idx = int(cfg.get("ad_rotate_index", 0))
    if idx >= len(ads):
        idx = 0

    ad = ads[idx]
    cfg["ad_rotate_index"] = (idx + 1) % len(ads)
    persist_all()
    return ad


def build_ad_reply_markup(buttons_json: str):
    try:
        buttons = json.loads(buttons_json or "[]")
    except Exception:
        buttons = []

    rows = []
    row = []
    for b in buttons:
        text = (b.get("text") or "").strip()
        url = (b.get("url") or "").strip()
        if text and url:
            row.append(InlineKeyboardButton(text, url=url))
            if len(row) == 2:
                rows.append(row)
                row = []

    if row:
        rows.append(row)

    if not rows:
        return None

    return InlineKeyboardMarkup(rows)


async def send_ad_by_format(chat_id: int, ad: dict, context: ContextTypes.DEFAULT_TYPE):
    content_type = (ad.get("content_type") or "text").strip()
    text_content = (ad.get("text_content") or "").strip()
    media_file_id = (ad.get("media_file_id") or "").strip()
    media_url = (ad.get("media_url") or "").strip()
    media_ref = media_file_id or media_url
    reply_markup = build_ad_reply_markup(ad.get("buttons_json", "[]"))

    if content_type == "text":
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_content or " ",
            reply_markup=reply_markup
        )
        return

    if content_type == "photo":
        if not media_ref:
            return
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=media_ref,
            caption=text_content or "",
            reply_markup=reply_markup
        )
        return

    if content_type == "video":
        if not media_ref:
            return
        await context.bot.send_video(
            chat_id=chat_id,
            video=media_ref,
            caption=text_content or "",
            reply_markup=reply_markup
        )
        return


# =========================
# 到期前3天提醒
# =========================
async def maybe_send_expire_reminder(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    rec = get_user_record(user_id)
    if not rec:
        return
    if not rec.get("enabled", False):
        return

    expires_at = rec.get("expires_at", "")
    if not expires_at:
        return

    try:
        expire_dt = parse_time(expires_at)
    except Exception:
        return

    now = datetime.now()
    delta = expire_dt - now

    if delta.total_seconds() < 0:
        return

    if delta <= timedelta(days=3):
        today_key = now.strftime("%Y-%m-%d")
        last_reminded = rec.get("last_reminded_at", "")
        if last_reminded == today_key:
            return

        group_names = []
        for cid, cfg in groups_data.items():
            if cfg.get("owner_id") == user_id:
                group_names.append(cfg.get("title") or f"群 {cid}")

        groups_text = "\n".join(f"• {g}" for g in group_names) if group_names else "暂无已绑定群"

        text = (
            "提醒：你的授权即将到期。\n\n"
            f"到期时间：{format_time(expires_at)}\n"
            f"剩余时间：约 {delta.days} 天\n\n"
            f"你的群：\n{groups_text}\n\n"
            "如需续期，请联系管理员。"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
                ])
            )
            rec["last_reminded_at"] = today_key
            persist_all()
        except Exception:
            pass


# =========================
# 教程文本
# =========================
def usage_tutorial_text():
    return (
        "机器人使用教程（新版）\n\n"
        "一、开始前先看这几条\n"
        "1. 你必须先被管理员加入白名单\n"
        "2. 机器人拉进群后，要给管理员权限\n"
        "3. 只有绑定到你名下的群，你才能管理\n"
        "4. 授权到期后，删除和广告功能都会停止\n\n"
        "二、怎么开始使用\n"
        "1. 私聊机器人，点击“查看我的群”\n"
        "2. 把机器人拉进你的群\n"
        "3. 当机器人被授权用户拉进群后，会自动绑定到后台\n"
        "4. 绑定成功后，你就能在后台看到这个群\n\n"
        "三、删除功能怎么设置\n"
        "1. 进入“查看我的群”\n"
        "2. 点开你要管理的群\n"
        "3. 可以选择：开启删除 / 关闭删除\n"
        "4. 可以设置：3秒 / 6秒 / 10秒 / 自定义秒数\n"
        "5. 普通成员消息会按你设置的秒数自动删除\n"
        "6. 管理员和机器人消息不会删\n\n"
        "四、广告功能怎么设置\n"
        "1. 进入某个群的管理页\n"
        "2. 点击“广告管理”\n"
        "3. 先设置广告数量上限\n"
        "4. 再设置广告频率（分钟）\n"
        "5. 最后添加广告内容并开启广告\n\n"
        "五、支持哪些广告格式\n"
        "1. 文字广告\n"
        "2. 图文广告（图片 + 文案）\n"
        "3. 视频广告（视频 + 文案）\n\n"
        "六、文字广告怎么发\n"
        "1. 进入广告管理\n"
        "2. 点击“添加文字广告”\n"
        "3. 直接发送文字内容即可\n"
        "4. 如果要加按钮，可以按这个格式发送：\n"
        "正文内容\n"
        "---buttons---\n"
        "官网|https://example.com\n"
        "联系我|https://t.me/你的用户名\n\n"
        "七、图文广告怎么发\n"
        "1. 点击“添加图文广告”\n"
        "2. 私聊直接发送一张图片\n"
        "3. 图片 caption 会作为广告文案\n\n"
        "八、视频广告怎么发\n"
        "1. 点击“添加视频广告”\n"
        "2. 私聊直接发送一个视频\n"
        "3. 视频 caption 会作为广告文案\n\n"
        "九、广告是怎么运行的\n"
        "1. 每个群都有自己的广告开关\n"
        "2. 每个群都有自己的广告频率\n"
        "3. 每个群都有自己的广告上限\n"
        "4. 每个群广告独立轮播，互不影响\n"
        "5. 你可以在广告列表里查看、启用、停用、删除单条广告\n\n"
        "十、常见问题\n"
        "1. 功能没生效：先检查机器人是不是群管理员\n"
        "2. 后台看不到群：检查是不是你自己拉进去的、是否已授权\n"
        "3. 广告不发：检查广告开关、频率、广告列表是否为空\n"
        "4. 到期后功能失效：联系管理员续期"
    )


# =========================
# 按钮UI
# =========================
def admin_contact_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def super_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("白名单管理", callback_data="wl_menu")],
        [InlineKeyboardButton("用户查询", callback_data="query_user_help")],
        [InlineKeyboardButton("查看我的群", callback_data="my_groups")],
        [InlineKeyboardButton("使用教程", callback_data="usage_tutorial")],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def whitelist_menu_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("白名单列表", callback_data="wl_list")],
        [InlineKeyboardButton("添加白名单", callback_data="sa_add_whitelist_help")],
        [InlineKeyboardButton("删除白名单", callback_data="sa_remove_whitelist_help")],
        [InlineKeyboardButton("查询用户", callback_data="query_user_help")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def user_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看我的群", callback_data="my_groups")],
        [InlineKeyboardButton("使用教程", callback_data="usage_tutorial")],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def build_groups_list(user_id: int):
    rows = []
    for cid, cfg in groups_data.items():
        cfg = ensure_group_defaults(cfg)
        if cfg.get("owner_id") == user_id:
            title = cfg.get("title") or f"群 {cid}"
            delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
            status = "开启" if cfg.get("enabled", True) else "关闭"
            ad_status = "广告开" if cfg.get("ad_enabled", False) else "广告关"
            rows.append([
                InlineKeyboardButton(
                    f"{title}｜删{status}｜{delay}秒｜{ad_status}",
                    callback_data=f"group_open|{cid}"
                )
            ])

    if not rows:
        rows = [[InlineKeyboardButton("暂无已授权群", callback_data="noop")]]

    rows.append([InlineKeyboardButton("返回主菜单", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def group_manage_panel(chat_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("开启删除", callback_data=f"group_enable|{chat_id}"),
            InlineKeyboardButton("关闭删除", callback_data=f"group_disable|{chat_id}")
        ],
        [
            InlineKeyboardButton("3秒", callback_data=f"group_set|{chat_id}|3"),
            InlineKeyboardButton("6秒", callback_data=f"group_set|{chat_id}|6"),
            InlineKeyboardButton("10秒", callback_data=f"group_set|{chat_id}|10"),
        ],
        [InlineKeyboardButton("自定义秒数", callback_data=f"group_custom|{chat_id}")],
        [InlineKeyboardButton("广告管理", callback_data=f"ad_menu|{chat_id}")],
        [InlineKeyboardButton("刷新群名", callback_data=f"group_refresh|{chat_id}")],
        [InlineKeyboardButton("返回群列表", callback_data="my_groups")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def ad_manage_panel(chat_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("开启广告", callback_data=f"ad_enable|{chat_id}"),
            InlineKeyboardButton("关闭广告", callback_data=f"ad_disable|{chat_id}")
        ],
        [
            InlineKeyboardButton("30分钟", callback_data=f"ad_freq|{chat_id}|30"),
            InlineKeyboardButton("60分钟", callback_data=f"ad_freq|{chat_id}|60"),
            InlineKeyboardButton("180分钟", callback_data=f"ad_freq|{chat_id}|180"),
        ],
        [InlineKeyboardButton("自定义广告频率", callback_data=f"ad_custom_freq|{chat_id}")],
        [InlineKeyboardButton("设置广告数量上限", callback_data=f"ad_set_max|{chat_id}")],
        [
            InlineKeyboardButton("添加文字广告", callback_data=f"ad_add_text|{chat_id}"),
            InlineKeyboardButton("添加图文广告", callback_data=f"ad_add_photo|{chat_id}")
        ],
        [
            InlineKeyboardButton("添加视频广告", callback_data=f"ad_add_video|{chat_id}")
        ],
        [InlineKeyboardButton("查看广告列表", callback_data=f"ad_list|{chat_id}")],
        [InlineKeyboardButton("返回群管理", callback_data=f"group_open|{chat_id}")],
    ])


def build_ads_list_panel(chat_id: str):
    ads = db_get_ads(int(chat_id))
    rows = []

    if ads:
        for ad in ads:
            ad_type = ad.get("content_type", "text")
            preview = (ad.get("text_content") or "").replace("\n", " ").strip()
            if not preview:
                preview = "无文案"
            if len(preview) > 12:
                preview = preview[:12] + "..."
            label = f"{ad_type}｜#{ad['id']}｜{'开' if ad.get('enabled') else '关'}｜{preview}"
            rows.append([
                InlineKeyboardButton(label, callback_data=f"ad_open|{chat_id}|{ad['id']}")
            ])
    else:
        rows = [[InlineKeyboardButton("当前没有广告", callback_data="noop")]]

    rows.append([InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")])
    return InlineKeyboardMarkup(rows)


def ad_detail_panel(chat_id: str, ad_id: int, enabled: bool):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("启用", callback_data=f"ad_enable_one|{chat_id}|{ad_id}"),
            InlineKeyboardButton("停用", callback_data=f"ad_disable_one|{chat_id}|{ad_id}")
        ],
        [InlineKeyboardButton("删除广告", callback_data=f"ad_delete|{chat_id}|{ad_id}")],
        [InlineKeyboardButton("返回广告列表", callback_data=f"ad_list|{chat_id}")],
    ])


def group_info_text(chat_id: str):
    cfg = get_group_config(int(chat_id))
    title = cfg.get("title") or f"群 {chat_id}"
    enabled = "开启" if cfg.get("enabled", True) else "关闭"
    delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
    bound_at = cfg.get("bound_at", "")
    ad_enabled = "开启" if cfg.get("ad_enabled", False) else "关闭"
    ad_interval = cfg.get("ad_interval_minutes", DEFAULT_AD_INTERVAL_MINUTES)
    ad_max = cfg.get("ad_max_count", DEFAULT_AD_MAX_COUNT)
    ad_count = db_ads_count(int(chat_id))

    return (
        f"群名称：{title}\n"
        f"群ID：{chat_id}\n"
        f"删除状态：{enabled}\n"
        f"删除延迟：{delay} 秒\n"
        f"绑定时间：{format_time(bound_at)}\n\n"
        f"广告状态：{ad_enabled}\n"
        f"广告频率：每 {ad_interval} 分钟\n"
        f"广告数量：{ad_count}/{ad_max}"
    )


def ad_info_text(chat_id: str):
    cfg = get_group_config(int(chat_id))
    title = cfg.get("title") or f"群 {chat_id}"
    return (
        f"广告管理\n\n"
        f"群名称：{title}\n"
        f"群ID：{chat_id}\n"
        f"广告状态：{'开启' if cfg.get('ad_enabled', False) else '关闭'}\n"
        f"广告频率：每 {cfg.get('ad_interval_minutes', DEFAULT_AD_INTERVAL_MINUTES)} 分钟\n"
        f"广告数量：{db_ads_count(int(chat_id))}/{cfg.get('ad_max_count', DEFAULT_AD_MAX_COUNT)}\n"
        f"上次发送：{format_time(cfg.get('ad_last_sent_at', ''))}"
    )


def ad_detail_text(ad: dict):
    return (
        f"广告ID：{ad['id']}\n"
        f"格式：{ad.get('content_type', 'text')}\n"
        f"状态：{'启用' if ad.get('enabled') else '停用'}\n"
        f"创建时间：{ad.get('created_at')}\n\n"
        f"文案：\n{ad.get('text_content') or '无'}\n\n"
        f"媒体：{ad.get('media_file_id') or ad.get('media_url') or '无'}"
    )


def whitelist_user_buttons(target_user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("开启权限", callback_data=f"wl_enable|{target_user_id}"),
            InlineKeyboardButton("关闭权限", callback_data=f"wl_disable|{target_user_id}")
        ],
        [
            InlineKeyboardButton("延长7天", callback_data=f"wl_extend|{target_user_id}|7"),
            InlineKeyboardButton("延长30天", callback_data=f"wl_extend|{target_user_id}|30")
        ],
        [
            InlineKeyboardButton("群额度+1", callback_data=f"wl_limit_add|{target_user_id}"),
            InlineKeyboardButton("群额度-1", callback_data=f"wl_limit_sub|{target_user_id}")
        ],
        [InlineKeyboardButton("删除白名单", callback_data=f"wl_delete|{target_user_id}")],
        [InlineKeyboardButton("返回白名单列表", callback_data="wl_list")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def build_whitelist_list():
    rows = []
    for uid_str, rec in sorted(whitelist_users.items(), key=lambda x: int(x[0])):
        uid = int(uid_str)
        role = rec.get("role", "user")
        if role == "super_admin":
            label = f"{uid}｜超级管理员"
        else:
            label = f"{uid}｜{user_status_text(rec)}｜额度{rec.get('max_groups', 0)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"wl_open|{uid}")])

    if not rows:
        rows = [[InlineKeyboardButton("暂无白名单", callback_data="noop")]]

    rows.append([InlineKeyboardButton("返回白名单菜单", callback_data="wl_menu")])
    return InlineKeyboardMarkup(rows)


# =========================
# /start
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if is_super_admin(user_id):
        await update.message.reply_text("超级管理员后台", reply_markup=super_admin_panel())
        await maybe_send_expire_reminder(user_id, context)
        return

    if is_whitelist_user(user_id):
        await update.message.reply_text("授权用户后台", reply_markup=user_panel())
        await maybe_send_expire_reminder(user_id, context)
        return

    await update.message.reply_text("你还未获得授权使用该机器人。", reply_markup=admin_contact_keyboard())


# =========================
# 命令：加白名单 / 删白名单
# =========================
async def addwl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("只有超级管理员可以使用这个命令。")
        return
    if not context.args:
        await update.message.reply_text("用法：/addwl 用户ID")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID必须是数字。")
        return

    create_whitelist_user(uid)
    await update.message.reply_text(f"已加入白名单：{uid}")


async def delwl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("只有超级管理员可以使用这个命令。")
        return
    if not context.args:
        await update.message.reply_text("用法：/delwl 用户ID")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户ID必须是数字。")
        return

    if uid == ADMIN_ID:
        await update.message.reply_text("不能删除超级管理员。")
        return

    whitelist_users.pop(str(uid), None)

    for _, cfg in groups_data.items():
        if cfg.get("owner_id") == uid:
            cfg["enabled"] = False
            cfg["ad_enabled"] = False

    persist_all()
    await update.message.reply_text(f"已移出白名单：{uid}")


# =========================
# 群事件
# =========================
async def bind_group_if_allowed(update: Update):
    if not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        return

    if not is_whitelist_user(user.id):
        return

    if not can_add_more_groups(user.id):
        return

    cfg = get_group_config(chat.id)
    cfg["owner_id"] = user.id
    cfg["title"] = chat.title or ""
    cfg["enabled"] = True
    cfg["bound_at"] = now_str()
    cfg["ad_enabled"] = cfg.get("ad_enabled", False)
    cfg["ad_interval_minutes"] = cfg.get("ad_interval_minutes", DEFAULT_AD_INTERVAL_MINUTES)
    cfg["ad_last_sent_at"] = cfg.get("ad_last_sent_at", "")
    cfg["ad_max_count"] = cfg.get("ad_max_count", DEFAULT_AD_MAX_COUNT)
    cfg["ad_rotate_index"] = cfg.get("ad_rotate_index", 0)

    if not cfg.get("delay"):
        cfg["delay"] = DEFAULT_DELETE_DELAY

    persist_all()


async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if not update.message.new_chat_members:
        return

    bot_id = context.bot.id
    added = any(member.id == bot_id for member in update.message.new_chat_members)
    if not added:
        return

    user_id = update.effective_user.id

    if not is_whitelist_user(user_id):
        await update.message.reply_text("当前拉群用户未授权，本群暂未启用功能。")
        return

    if not can_add_more_groups(user_id):
        await update.message.reply_text("你的授权群数量已达上限，请联系管理员。")
        return

    await bind_group_if_allowed(update)
    cfg = get_group_config(update.effective_chat.id)

    if cfg.get("owner_id"):
        await update.message.reply_text("授权成功，本群已启用删除功能。")
        await maybe_send_expire_reminder(user_id, context)
    else:
        await update.message.reply_text("授权失败，请联系管理员。")


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return
    if not update.message.new_chat_members:
        return

    cfg = get_group_config(update.effective_chat.id)
    if not cfg.get("owner_id") or not cfg.get("enabled", True):
        return

    delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
    text = f"本群已开启清理模式，发送的普通消息将在 {delay} 秒后自动删除。"
    sent = await update.message.reply_text(text)
    context.application.create_task(delete_message_later(sent, delay))


async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user
    message = update.message

    if chat.type not in ("group", "supergroup"):
        return

    cfg = get_group_config(chat.id)

    if not cfg.get("owner_id"):
        return

    owner_id = cfg.get("owner_id")
    if not is_whitelist_user(owner_id):
        return

    if not cfg.get("enabled", True):
        return

    if user.is_bot:
        return

    if await is_group_admin(chat.id, user.id, context):
        return

    delay = int(cfg.get("delay", DEFAULT_DELETE_DELAY))
    context.application.create_task(delete_message_later(message, delay))

    await maybe_send_expire_reminder(owner_id, context)


# =========================
# 自动广告调度
# =========================
async def auto_send_ads(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()

    for cid, cfg in groups_data.items():
        cfg = ensure_group_defaults(cfg)

        owner_id = cfg.get("owner_id")
        if not owner_id:
            continue

        if not is_whitelist_user(owner_id):
            cfg["ad_enabled"] = False
            persist_all()
            continue

        if not cfg.get("ad_enabled", False):
            continue

        if db_ads_count(int(cid)) == 0:
            continue

        interval_minutes = int(cfg.get("ad_interval_minutes", DEFAULT_AD_INTERVAL_MINUTES))
        last_sent_at = cfg.get("ad_last_sent_at", "")

        if last_sent_at:
            try:
                last_dt = parse_time(last_sent_at)
                if (now - last_dt).total_seconds() < interval_minutes * 60:
                    continue
            except Exception:
                pass

        ad = get_next_ad(cid)
        if not ad:
            continue

        try:
            await send_ad_by_format(int(cid), ad, context)
            cfg["ad_last_sent_at"] = now_str()
            persist_all()
        except Exception as e:
            print(f"广告发送失败 chat_id={cid}: {e}")


# =========================
# 按钮
# =========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "noop":
        return

    if data == "back_main":
        if is_super_admin(user_id):
            await query.edit_message_text("超级管理员后台", reply_markup=super_admin_panel())
            return
        if is_whitelist_user(user_id):
            await query.edit_message_text("授权用户后台", reply_markup=user_panel())
            return
        await query.edit_message_text("未授权。", reply_markup=admin_contact_keyboard())
        return

    if data == "usage_tutorial":
        if not (is_super_admin(user_id) or is_whitelist_user(user_id)):
            await query.edit_message_text("未授权。", reply_markup=admin_contact_keyboard())
            return
        await query.edit_message_text(
            usage_tutorial_text(),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
            ])
        )
        return

    if data == "wl_menu":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text("白名单管理", reply_markup=whitelist_menu_panel())
        return

    if data == "wl_list":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text("白名单列表：", reply_markup=build_whitelist_list())
        return

    if data == "query_user_help":
        pending_actions[str(user_id)] = {"action": "query_user"}
        persist_all()
        await query.edit_message_text(
            "请发送你要查询的用户ID",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
            ])
        )
        return

    if data.startswith("wl_open|"):
        _, uid = data.split("|", 1)
        uid_int = int(uid)
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return

        await query.edit_message_text(
            user_detail_text(uid_int),
            reply_markup=whitelist_user_buttons(uid_int)
        )
        return

    if data.startswith("wl_enable|"):
        _, uid = data.split("|", 1)
        rec = get_user_record(int(uid))
        if not rec:
            await query.edit_message_text("用户不存在。")
            return
        rec["enabled"] = True
        persist_all()
        await query.edit_message_text(user_detail_text(int(uid)), reply_markup=whitelist_user_buttons(int(uid)))
        return

    if data.startswith("wl_disable|"):
        _, uid = data.split("|", 1)
        rec = get_user_record(int(uid))
        if not rec:
            await query.edit_message_text("用户不存在。")
            return
        rec["enabled"] = False
        persist_all()
        await query.edit_message_text(user_detail_text(int(uid)), reply_markup=whitelist_user_buttons(int(uid)))
        return

    if data.startswith("wl_extend|"):
        _, uid, days = data.split("|")
        rec = get_user_record(int(uid))
        if not rec:
            await query.edit_message_text("用户不存在。")
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

        rec["expires_at"] = (base + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        persist_all()
        await query.edit_message_text(user_detail_text(int(uid)), reply_markup=whitelist_user_buttons(int(uid)))
        return

    if data.startswith("wl_limit_add|"):
        _, uid = data.split("|", 1)
        rec = get_user_record(int(uid))
        if not rec:
            await query.edit_message_text("用户不存在。")
            return
        rec["max_groups"] = int(rec.get("max_groups", 0)) + 1
        persist_all()
        await query.edit_message_text(user_detail_text(int(uid)), reply_markup=whitelist_user_buttons(int(uid)))
        return

    if data.startswith("wl_limit_sub|"):
        _, uid = data.split("|", 1)
        rec = get_user_record(int(uid))
        if not rec:
            await query.edit_message_text("用户不存在。")
            return
        current = int(rec.get("max_groups", 0))
        rec["max_groups"] = max(0, current - 1)
        persist_all()
        await query.edit_message_text(user_detail_text(int(uid)), reply_markup=whitelist_user_buttons(int(uid)))
        return

    if data.startswith("wl_delete|"):
        _, uid = data.split("|", 1)
        uid_int = int(uid)
        if uid_int == ADMIN_ID:
            await query.edit_message_text("不能删除超级管理员。")
            return
        whitelist_users.pop(str(uid_int), None)
        for _, cfg in groups_data.items():
            if cfg.get("owner_id") == uid_int:
                cfg["enabled"] = False
                cfg["ad_enabled"] = False
        persist_all()
        await query.edit_message_text("已删除该白名单用户。", reply_markup=build_whitelist_list())
        return

    if data == "sa_add_whitelist_help":
        pending_actions[str(user_id)] = {"action": "add_whitelist"}
        persist_all()
        await query.edit_message_text(
            "请发送要添加的用户ID",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回白名单菜单", callback_data="wl_menu")]
            ])
        )
        return

    if data == "sa_remove_whitelist_help":
        pending_actions[str(user_id)] = {"action": "remove_whitelist"}
        persist_all()
        await query.edit_message_text(
            "请发送要删除的用户ID",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回白名单菜单", callback_data="wl_menu")]
            ])
        )
        return

    if data == "my_groups":
        if not (is_super_admin(user_id) or is_whitelist_user(user_id)):
            await query.edit_message_text("未授权。", reply_markup=admin_contact_keyboard())
            return
        await query.edit_message_text("请选择你要管理的群：", reply_markup=build_groups_list(user_id))
        return

    if data.startswith("group_open|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。", reply_markup=user_panel())
            return

        await query.edit_message_text(group_info_text(chat_id), reply_markup=group_manage_panel(chat_id))
        return

    if data.startswith("group_enable|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["enabled"] = True
        persist_all()
        await query.edit_message_text("已开启删除。\n\n" + group_info_text(chat_id), reply_markup=group_manage_panel(chat_id))
        return

    if data.startswith("group_disable|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["enabled"] = False
        persist_all()
        await query.edit_message_text("已关闭删除。\n\n" + group_info_text(chat_id), reply_markup=group_manage_panel(chat_id))
        return

    if data.startswith("group_set|"):
        _, chat_id, sec = data.split("|")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["delay"] = int(sec)
        persist_all()
        await query.edit_message_text(f"已设置为 {sec} 秒删除。\n\n" + group_info_text(chat_id), reply_markup=group_manage_panel(chat_id))
        return

    if data.startswith("group_custom|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "set_custom_delay", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请直接发送你要设置的秒数（例如：15）",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回群管理", callback_data=f"group_open|{chat_id}")]
            ])
        )
        return

    if data.startswith("group_refresh|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        await refresh_group_title(chat_id, context)
        await query.edit_message_text("群名已刷新。\n\n" + group_info_text(chat_id), reply_markup=group_manage_panel(chat_id))
        return

    # ===== 广告管理 =====
    if data.startswith("ad_menu|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        await query.edit_message_text(ad_info_text(chat_id), reply_markup=ad_manage_panel(chat_id))
        return

    if data.startswith("ad_enable|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["ad_enabled"] = True
        persist_all()
        await query.edit_message_text("已开启广告。\n\n" + ad_info_text(chat_id), reply_markup=ad_manage_panel(chat_id))
        return

    if data.startswith("ad_disable|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["ad_enabled"] = False
        persist_all()
        await query.edit_message_text("已关闭广告。\n\n" + ad_info_text(chat_id), reply_markup=ad_manage_panel(chat_id))
        return

    if data.startswith("ad_freq|"):
        _, chat_id, minutes = data.split("|")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        cfg["ad_interval_minutes"] = int(minutes)
        persist_all()
        await query.edit_message_text(f"已设置广告频率为每 {minutes} 分钟。\n\n" + ad_info_text(chat_id), reply_markup=ad_manage_panel(chat_id))
        return

    if data.startswith("ad_custom_freq|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "set_custom_ad_freq", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请直接发送广告频率，单位是分钟。\n例如：45",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")]
            ])
        )
        return

    if data.startswith("ad_set_max|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "set_ad_max", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请直接发送广告数量上限。\n例如：5",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")]
            ])
        )
        return

    if data.startswith("ad_add_text|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "add_text_ad", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请直接发送文字广告内容。\n\n如果需要按钮，请按下面格式发送：\n"
            "正文内容\n"
            "---buttons---\n"
            "官网|https://example.com\n"
            "联系我|https://t.me/xxx",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")]
            ])
        )
        return

    if data.startswith("ad_add_photo|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "add_photo_ad", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请发送一张图片。\n可以直接带 caption 作为文案。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")]
            ])
        )
        return

    if data.startswith("ad_add_video|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        pending_actions[str(user_id)] = {"action": "add_video_ad", "chat_id": chat_id}
        persist_all()
        await query.edit_message_text(
            "请发送一个视频。\n可以直接带 caption 作为文案。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")]
            ])
        )
        return

    if data.startswith("ad_list|"):
        _, chat_id = data.split("|", 1)
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        await query.edit_message_text("当前广告列表：", reply_markup=build_ads_list_panel(chat_id))
        return

    if data.startswith("ad_open|"):
        _, chat_id, ad_id = data.split("|")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != user_id:
            await query.edit_message_text("无权管理这个群。")
            return

        ad = db_get_ad(int(ad_id))
        if not ad:
            await query.edit_message_text("广告不存在。")
            return

        await query.edit_message_text(
            ad_detail_text(ad),
            reply_markup=ad_detail_panel(chat_id, int(ad_id), ad.get("enabled", True))
        )
        return

    if data.startswith("ad_enable_one|"):
        _, chat_id, ad_id = data.split("|")
        db_set_ad_enabled(int(ad_id), True)
        ad = db_get_ad(int(ad_id))
        await query.edit_message_text(
            ad_detail_text(ad),
            reply_markup=ad_detail_panel(chat_id, int(ad_id), True)
        )
        return

    if data.startswith("ad_disable_one|"):
        _, chat_id, ad_id = data.split("|")
        db_set_ad_enabled(int(ad_id), False)
        ad = db_get_ad(int(ad_id))
        await query.edit_message_text(
            ad_detail_text(ad),
            reply_markup=ad_detail_panel(chat_id, int(ad_id), False)
        )
        return

    if data.startswith("ad_delete|"):
        _, chat_id, ad_id = data.split("|")
        db_delete_ad(int(ad_id))
        await query.edit_message_text("广告已删除。", reply_markup=build_ads_list_panel(chat_id))
        return


# =========================
# 私聊文字输入处理
# =========================
async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != "private":
        return

    user_id = str(update.effective_user.id)
    if user_id not in pending_actions:
        return

    action_info = pending_actions[user_id]
    action = action_info.get("action")
    text = update.message.text.strip()

    if action == "set_custom_delay":
        chat_id = action_info.get("chat_id")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != update.effective_user.id:
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权操作该群。")
            return

        try:
            sec = int(text)
            if sec < 1 or sec > 3600:
                raise ValueError
        except ValueError:
            await update.message.reply_text("请输入 1 到 3600 之间的整数。")
            return

        cfg["delay"] = sec
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(
            f"已设置为 {sec} 秒删除。\n\n{group_info_text(chat_id)}",
            reply_markup=group_manage_panel(chat_id)
        )
        return

    if action == "set_custom_ad_freq":
        chat_id = action_info.get("chat_id")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != update.effective_user.id:
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权操作该群。")
            return

        try:
            minutes = int(text)
            if minutes < 1 or minutes > 10080:
                raise ValueError
        except ValueError:
            await update.message.reply_text("请输入 1 到 10080 之间的整数分钟。")
            return

        cfg["ad_interval_minutes"] = minutes
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(
            f"已设置广告频率为每 {minutes} 分钟。\n\n{ad_info_text(chat_id)}",
            reply_markup=ad_manage_panel(chat_id)
        )
        return

    if action == "set_ad_max":
        chat_id = action_info.get("chat_id")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != update.effective_user.id:
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权操作该群。")
            return

        try:
            max_count = int(text)
            if max_count < 1 or max_count > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("请输入 1 到 100 之间的整数。")
            return

        current_count = db_ads_count(int(chat_id))
        if current_count > max_count:
            await update.message.reply_text(
                f"当前广告已有 {current_count} 条，不能直接改成 {max_count}。\n请先删除多余广告，再重新设置。"
            )
            return

        cfg["ad_max_count"] = max_count
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(
            f"已设置广告数量上限为 {max_count}。\n\n{ad_info_text(chat_id)}",
            reply_markup=ad_manage_panel(chat_id)
        )
        return

    if action == "add_text_ad":
        chat_id = action_info.get("chat_id")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != update.effective_user.id:
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权操作该群。")
            return

        current_count = db_ads_count(int(chat_id))
        max_count = int(cfg.get("ad_max_count", DEFAULT_AD_MAX_COUNT))
        if current_count >= max_count:
            await update.message.reply_text(f"该群广告数量已达到上限 {max_count}。")
            return

        main_text = text
        buttons = []

        if "---buttons---" in text:
            parts = text.split("---buttons---", 1)
            main_text = parts[0].strip()
            btn_lines = parts[1].strip().splitlines()
            for line in btn_lines:
                if "|" in line:
                    bt, url = line.split("|", 1)
                    bt = bt.strip()
                    url = url.strip()
                    if bt and url:
                        buttons.append({"text": bt, "url": url})

        db_add_ad(
            chat_id=int(chat_id),
            content_type="text",
            text_content=main_text,
            buttons=buttons
        )

        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(
            "文字广告添加成功。",
            reply_markup=ad_manage_panel(chat_id)
        )
        return

    if action == "query_user":
        if not is_super_admin(update.effective_user.id):
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权限。")
            return

        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("请输入正确的用户ID。")
            return

        pending_actions.pop(user_id, None)
        persist_all()

        rec = get_user_record(target_uid)
        if not rec:
            await update.message.reply_text("未找到该用户。", reply_markup=whitelist_menu_panel())
            return

        await update.message.reply_text(
            user_detail_text(target_uid),
            reply_markup=whitelist_user_buttons(target_uid)
        )
        return

    if action == "add_whitelist":
        if not is_super_admin(update.effective_user.id):
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权限。")
            return

        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("请输入正确的用户ID。")
            return

        create_whitelist_user(target_uid)
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(f"已添加白名单用户：{target_uid}", reply_markup=whitelist_menu_panel())
        return

    if action == "remove_whitelist":
        if not is_super_admin(update.effective_user.id):
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权限。")
            return

        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("请输入正确的用户ID。")
            return

        if target_uid == ADMIN_ID:
            await update.message.reply_text("不能删除超级管理员。")
            return

        whitelist_users.pop(str(target_uid), None)
        for _, cfg in groups_data.items():
            if cfg.get("owner_id") == target_uid:
                cfg["enabled"] = False
                cfg["ad_enabled"] = False
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(f"已删除白名单用户：{target_uid}", reply_markup=whitelist_menu_panel())
        return


# =========================
# 私聊媒体输入处理
# =========================
async def handle_private_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != "private":
        return

    user_id = str(update.effective_user.id)
    if user_id not in pending_actions:
        return

    action_info = pending_actions[user_id]
    action = action_info.get("action")
    chat_id = action_info.get("chat_id")

    cfg = groups_data.get(chat_id)
    if not cfg or cfg.get("owner_id") != update.effective_user.id:
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text("无权操作该群。")
        return

    current_count = db_ads_count(int(chat_id))
    max_count = int(cfg.get("ad_max_count", DEFAULT_AD_MAX_COUNT))
    if current_count >= max_count:
        await update.message.reply_text(f"该群广告数量已达到上限 {max_count}。")
        return

    if action == "add_photo_ad" and update.message.photo:
        file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        db_add_ad(
            chat_id=int(chat_id),
            content_type="photo",
            text_content=caption,
            media_file_id=file_id
        )
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text("图文广告添加成功。", reply_markup=ad_manage_panel(chat_id))
        return

    if action == "add_video_ad" and update.message.video:
        file_id = update.message.video.file_id
        caption = update.message.caption or ""
        db_add_ad(
            chat_id=int(chat_id),
            content_type="video",
            text_content=caption,
            media_file_id=file_id
        )
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text("视频广告添加成功。", reply_markup=ad_manage_panel(chat_id))
        return


def main():
    init_ads_table()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwl", addwl))
    app.add_handler(CommandHandler("delwl", delwl))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_added_to_group), group=0)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members), group=1)

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            handle_group_messages
        ),
        group=2
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_private_text),
        group=3
    )

    app.add_handler(
        MessageHandler(filters.PHOTO | filters.VIDEO, handle_private_media),
        group=4
    )

    app.job_queue.run_repeating(auto_send_ads, interval=30, first=20)

    print("商业级删除机器人 V5（Postgres广告版）已启动...")
    app.run_polling()


if __name__ == "__main__":
    main()
