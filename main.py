import os
import asyncio
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

# 👉 这里填你的TG用户ID
ADMIN_ID = @vyfjii

# 默认设置
chat_settings = {}
whitelist = set([ADMIN_ID])


def get_config(chat_id):
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "enabled": True,
            "delay": 6
        }
    return chat_settings[chat_id]


# ===== 删除逻辑 =====
async def delete_message_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    cfg = get_config(chat_id)

    # 只在开启状态才删除
    if not cfg["enabled"]:
        return

    # 不删管理员
    member = await context.bot.get_chat_member(chat_id, user.id)
    if member.status in ("administrator", "creator"):
        return

    # 不删机器人
    if user.is_bot:
        return

    context.application.create_task(delete_message_later(msg, cfg["delay"]))


# ===== 新成员提示 =====
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.new_chat_members:
        text = "本群已开启清理模式，发送的消息将在6秒后自动删除。"
        await update.message.reply_text(text)


# ===== 私聊后台 =====
def get_panel():
    keyboard = [
        [InlineKeyboardButton("开启删除", callback_data="on"),
         InlineKeyboardButton("关闭删除", callback_data="off")],
        [InlineKeyboardButton("设置 3 秒", callback_data="set_3"),
         InlineKeyboardButton("设置 6 秒", callback_data="set_6"),
         InlineKeyboardButton("设置 10 秒", callback_data="set_10")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in whitelist:
        await update.message.reply_text("未授权使用")
        return

    await update.message.reply_text("控制面板：", reply_markup=get_panel())


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in whitelist:
        await query.edit_message_text("未授权")
        return

    # 默认控制第一个群（后面可以升级多群）
    if not chat_settings:
        await query.edit_message_text("暂无群数据")
        return

    chat_id = list(chat_settings.keys())[0]
    cfg = get_config(chat_id)

    if query.data == "on":
        cfg["enabled"] = True
        text = "已开启删除"
    elif query.data == "off":
        cfg["enabled"] = False
        text = "已关闭删除"
    elif query.data.startswith("set_"):
        sec = int(query.data.split("_")[1])
        cfg["delay"] = sec
        text = f"已设置 {sec} 秒删除"

    await query.edit_message_text(text, reply_markup=get_panel())


# ===== 主程序 =====
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_group_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))

    print("后台删除机器人已启动")

    app.run_polling()


if __name__ == "__main__":
    main()
