import os
import json
import asyncio
from pathlib import Path
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

DEFAULT_DELETE_DELAY = 6


# ===== 数据读写 =====
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


whitelist_users = set(load_json(WHITELIST_FILE, [ADMIN_ID]))
groups_data = load_json(GROUPS_FILE, {})


def persist_all():
    save_json(WHITELIST_FILE, list(whitelist_users))
    save_json(GROUPS_FILE, groups_data)


def get_group_config(chat_id: int):
    chat_id = str(chat_id)
    if chat_id not in groups_data:
        groups_data[chat_id] = {
            "owner_id": None,
            "enabled": True,
            "delay": DEFAULT_DELETE_DELAY,
            "title": "",
        }
        persist_all()
    return groups_data[chat_id]


# ===== 工具函数 =====
async def delete_message_later(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


async def is_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_whitelist_user(user_id: int) -> bool:
    return user_id in whitelist_users


def admin_contact_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def build_super_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看白名单", callback_data="sa_view_whitelist")],
        [InlineKeyboardButton("添加白名单", callback_data="sa_add_whitelist_help")],
        [InlineKeyboardButton("删除白名单", callback_data="sa_remove_whitelist_help")],
        [InlineKeyboardButton("查看我的群", callback_data="my_groups")],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def build_user_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("我的群组", callback_data="my_groups")],
        [InlineKeyboardButton("开启删除", callback_data="enable_delete")],
        [InlineKeyboardButton("关闭删除", callback_data="disable_delete")],
        [
            InlineKeyboardButton("3秒", callback_data="set_3"),
            InlineKeyboardButton("6秒", callback_data="set_6"),
            InlineKeyboardButton("10秒", callback_data="set_10"),
        ],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def owned_groups_text(user_id: int):
    rows = []
    for chat_id, cfg in groups_data.items():
        if cfg.get("owner_id") == user_id:
            title = cfg.get("title") or f"群 {chat_id}"
            enabled = "开启" if cfg.get("enabled", True) else "关闭"
            delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
            rows.append(f"• {title}\n  状态：{enabled}｜秒数：{delay}")
    if not rows:
        return "你名下还没有已授权群。"
    return "\n\n".join(rows)


async def bind_group_if_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    当机器人被拉入群时，尝试把群绑定给邀请者。
    """
    if not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        return

    # 只有白名单用户拉入，群才生效
    if not is_whitelist_user(user.id):
        return

    cfg = get_group_config(chat.id)
    cfg["owner_id"] = user.id
    cfg["title"] = chat.title or ""
    cfg["enabled"] = True
    if not cfg.get("delay"):
        cfg["delay"] = DEFAULT_DELETE_DELAY
    persist_all()


# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if is_super_admin(user_id):
        await update.message.reply_text(
            "超级管理员后台",
            reply_markup=build_super_admin_panel()
        )
        return

    if is_whitelist_user(user_id):
        await update.message.reply_text(
            "授权用户后台",
            reply_markup=build_user_panel()
        )
        return

    await update.message.reply_text(
        "你还未获得授权使用该机器人。",
        reply_markup=admin_contact_keyboard()
    )


# ===== 超级管理员命令：加白名单 =====
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

    whitelist_users.add(uid)
    persist_all()
    await update.message.reply_text(f"已加入白名单：{uid}")


# ===== 超级管理员命令：删白名单 =====
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

    whitelist_users.discard(uid)

    # 把这个用户名下的群全部停用
    for _, cfg in groups_data.items():
        if cfg.get("owner_id") == uid:
            cfg["enabled"] = False

    persist_all()
    await update.message.reply_text(f"已移出白名单：{uid}")


# ===== 新成员欢迎 =====
async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    if not update.message.new_chat_members:
        return

    chat = update.effective_chat
    cfg = get_group_config(chat.id)

    # 只有授权群才欢迎
    if not cfg.get("owner_id") or not cfg.get("enabled", True):
        return

    delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
    text = f"本群已开启清理模式，发送的普通消息将在 {delay} 秒后自动删除。"
    sent = await update.message.reply_text(text)

    # 欢迎语也可延迟删除
    context.application.create_task(delete_message_later(sent, delay))


# ===== 群消息删除逻辑 =====
async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user
    message = update.message

    if chat.type not in ("group", "supergroup"):
        return

    cfg = get_group_config(chat.id)

    # 未授权群：不工作
    if not cfg.get("owner_id"):
        return

    # 关闭状态：不工作
    if not cfg.get("enabled", True):
        return

    # 不删机器人
    if user.is_bot:
        return

    # 不删管理员
    if await is_group_admin(chat.id, user.id, context):
        return

    delay = int(cfg.get("delay", DEFAULT_DELETE_DELAY))
    context.application.create_task(delete_message_later(message, delay))


# ===== 按钮后台 =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "sa_view_whitelist":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        text = "白名单用户：\n" + "\n".join(str(x) for x in sorted(whitelist_users))
        await query.edit_message_text(text, reply_markup=build_super_admin_panel())
        return

    if data == "sa_add_whitelist_help":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text(
            "请在私聊中发送：\n/addwl 用户ID",
            reply_markup=build_super_admin_panel()
        )
        return

    if data == "sa_remove_whitelist_help":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text(
            "请在私聊中发送：\n/delwl 用户ID",
            reply_markup=build_super_admin_panel()
        )
        return

    if data == "my_groups":
        if not (is_super_admin(user_id) or is_whitelist_user(user_id)):
            await query.edit_message_text("未授权。", reply_markup=admin_contact_keyboard())
            return

        text = owned_groups_text(user_id)
        kb = build_super_admin_panel() if is_super_admin(user_id) else build_user_panel()
        await query.edit_message_text(text, reply_markup=kb)
        return

    # 以下是普通授权用户面板控制
    if not is_whitelist_user(user_id):
        await query.edit_message_text("未授权。", reply_markup=admin_contact_keyboard())
        return

    # 取这个用户名下的第一个群来控制
    target_chat_id = None
    for cid, cfg in groups_data.items():
        if cfg.get("owner_id") == user_id:
            target_chat_id = cid
            break

    if not target_chat_id:
        await query.edit_message_text("你名下暂无已授权群。", reply_markup=build_user_panel())
        return

    cfg = groups_data[target_chat_id]

    if data == "enable_delete":
        cfg["enabled"] = True
        persist_all()
        await query.edit_message_text("已开启删除。", reply_markup=build_user_panel())
        return

    if data == "disable_delete":
        cfg["enabled"] = False
        persist_all()
        await query.edit_message_text("已关闭删除。", reply_markup=build_user_panel())
        return

    if data.startswith("set_"):
        seconds = int(data.split("_")[1])
        cfg["delay"] = seconds
        persist_all()
        await query.edit_message_text(f"已设置为 {seconds} 秒删除。", reply_markup=build_user_panel())
        return


# ===== 机器人加入群时绑定授权 =====
async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    if not update.message.new_chat_members:
        return

    bot_id = context.bot.id
    added = any(member.id == bot_id for member in update.message.new_chat_members)
    if not added:
        return

    await bind_group_if_allowed(update, context)

    cfg = get_group_config(update.effective_chat.id)
    if cfg.get("owner_id"):
        await update.message.reply_text("授权成功，本群已启用删除功能。")
    else:
        await update.message.reply_text("当前拉群用户未授权，本群暂未启用功能。")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # 私聊控制
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwl", addwl))
    app.add_handler(CommandHandler("delwl", delwl))
    app.add_handler(CallbackQueryHandler(button_handler))

    # 群事件
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_added_to_group), group=0)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members), group=1)

    # 群普通消息删除
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            handle_group_messages
        ),
        group=2
    )

    print("授权控制版删除机器人已启动...")
    app.run_polling()


if __name__ == "__main__":
    main()
