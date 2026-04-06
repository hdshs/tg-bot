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
PENDING_FILE = DATA_DIR / "pending_actions.json"

DEFAULT_DELETE_DELAY = 6


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
pending_actions = load_json(PENDING_FILE, {})


def persist_all():
    save_json(WHITELIST_FILE, list(whitelist_users))
    save_json(GROUPS_FILE, groups_data)
    save_json(PENDING_FILE, pending_actions)


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


def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_whitelist_user(user_id: int) -> bool:
    return user_id in whitelist_users


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


def admin_contact_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def super_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看白名单", callback_data="sa_view_whitelist")],
        [InlineKeyboardButton("添加白名单", callback_data="sa_add_whitelist_help")],
        [InlineKeyboardButton("删除白名单", callback_data="sa_remove_whitelist_help")],
        [InlineKeyboardButton("查看我的群", callback_data="my_groups")],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def user_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看我的群", callback_data="my_groups")],
        [InlineKeyboardButton("联系管理员", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


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
        [InlineKeyboardButton("返回群列表", callback_data="my_groups")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def build_groups_list(user_id: int):
    rows = []
    for cid, cfg in groups_data.items():
        if cfg.get("owner_id") == user_id:
            title = cfg.get("title") or f"群 {cid}"
            rows.append([InlineKeyboardButton(title, callback_data=f"group_open|{cid}")])

    if not rows:
        rows = [[InlineKeyboardButton("暂无已授权群", callback_data="noop")]]

    rows.append([InlineKeyboardButton("返回主菜单", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def group_info_text(chat_id: str):
    cfg = groups_data.get(str(chat_id), {})
    title = cfg.get("title") or f"群 {chat_id}"
    enabled = "开启" if cfg.get("enabled", True) else "关闭"
    delay = cfg.get("delay", DEFAULT_DELETE_DELAY)
    return f"群名称：{title}\n状态：{enabled}\n删除延迟：{delay} 秒"


async def bind_group_if_allowed(update: Update):
    if not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        return

    if not is_whitelist_user(user.id):
        return

    cfg = get_group_config(chat.id)
    cfg["owner_id"] = user.id
    cfg["title"] = chat.title or ""
    cfg["enabled"] = True
    if not cfg.get("delay"):
        cfg["delay"] = DEFAULT_DELETE_DELAY
    persist_all()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if is_super_admin(user_id):
        await update.message.reply_text("超级管理员后台", reply_markup=super_admin_panel())
        return

    if is_whitelist_user(user_id):
        await update.message.reply_text("授权用户后台", reply_markup=user_panel())
        return

    await update.message.reply_text("你还未获得授权使用该机器人。", reply_markup=admin_contact_keyboard())


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

    for _, cfg in groups_data.items():
        if cfg.get("owner_id") == uid:
            cfg["enabled"] = False

    persist_all()
    await update.message.reply_text(f"已移出白名单：{uid}")


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

    if not cfg.get("enabled", True):
        return

    if user.is_bot:
        return

    if await is_group_admin(chat.id, user.id, context):
        return

    delay = int(cfg.get("delay", DEFAULT_DELETE_DELAY))
    context.application.create_task(delete_message_later(message, delay))


async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if not update.message.new_chat_members:
        return

    bot_id = context.bot.id
    added = any(member.id == bot_id for member in update.message.new_chat_members)
    if not added:
        return

    await bind_group_if_allowed(update)

    cfg = get_group_config(update.effective_chat.id)
    if cfg.get("owner_id"):
        await update.message.reply_text("授权成功，本群已启用删除功能。")
    else:
        await update.message.reply_text("当前拉群用户未授权，本群暂未启用功能。")


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

    if data == "sa_view_whitelist":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        text = "白名单用户：\n" + "\n".join(str(x) for x in sorted(whitelist_users))
        await query.edit_message_text(text, reply_markup=super_admin_panel())
        return

    if data == "sa_add_whitelist_help":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text(
            "请在私聊中发送：\n/addwl 用户ID",
            reply_markup=super_admin_panel()
        )
        return

    if data == "sa_remove_whitelist_help":
        if not is_super_admin(user_id):
            await query.edit_message_text("无权限。")
            return
        await query.edit_message_text(
            "请在私聊中发送：\n/delwl 用户ID",
            reply_markup=super_admin_panel()
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

    if action == "set_custom_delay":
        chat_id = action_info.get("chat_id")
        cfg = groups_data.get(chat_id)
        if not cfg or cfg.get("owner_id") != update.effective_user.id:
            pending_actions.pop(user_id, None)
            persist_all()
            await update.message.reply_text("无权操作该群。")
            return

        try:
            sec = int(update.message.text.strip())
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

    print("授权控制版 V2 已启动...")
    app.run_polling()


if __name__ == "__main__":
    main()
