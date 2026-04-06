import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # 回复
    await message.reply_text(f"你说的是：{message.text}")

    # 6秒后删除用户消息
    await context.application.create_task(delete_message_later(message, 6))


async def delete_message_later(message, delay):
    import asyncio
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("机器人已启动...")

app.run_polling()
