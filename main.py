import os
import json
import asyncio
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
def get_group_config(chat_id: int):
    cid = str(chat_id)
    if cid not in groups_data:
        groups_data[cid] = {
            "owner_id": None,
            "enabled": True,
            "delay": DEFAULT_DELETE_DELAY,
            "title": "",
            "bound_at": now_str(),
        }
        persist_all()
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
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def build_groups_list(user_id: int):
    rows = []
    for cid, cfg in groups_data.items():
        if cfg.get("owner_id") == user_id:
            title = cfg.get("title") or f"群 {cid}"
            delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
            status = "开启" if cfg.get("enabled", True) else "关闭"
            rows.append([
                InlineKeyboardButton(f"{title}｜{status}｜{delay}秒", callback_data=f"group_open|{cid}")
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
        [InlineKeyboardButton("刷新群名", callback_data=f"group_refresh|{chat_id}")],
        [InlineKeyboardButton("返回群列表", callback_data="my_groups")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def group_info_text(chat_id: str):
    cfg = groups_data.get(str(chat_id), {})
    title = cfg.get("title") or f"群 {chat_id}"
    enabled = "开启" if cfg.get("enabled", True) else "关闭"
    delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
    bound_at = cfg.get("bound_at", "")
    return (
        f"群名称：{title}\n"
        f"群ID：{chat_id}\n"
        f"状态：{enabled}\n"
        f"删除延迟：{delay} 秒\n"
        f"绑定时间：{format_time(bound_at)}"
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
        pending_actions.pop(user_id, None)
        persist_all()
        await update.message.reply_text(f"已删除白名单用户：{target_uid}", reply_markup=whitelist_menu_panel())
        return


def main():
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

    print("授权控制版 V3 已启动...")
    app.run_polling()


if __name__ == "__main__":
    main()
