from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import os

TOKEN = os.getenv("BOT_TOKEN")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    await msg.reply_text(f"收到：{msg.text}")
    context.job_queue.run_once(delete_msg, 6, data=msg)

async def delete_msg(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.job.data.delete()
    except:
        pass

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

app.run_polling()
