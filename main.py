{\rtf1\ansi\ansicpg936\cocoartf2709
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 from telegram import Update\
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes\
import os\
\
TOKEN = os.getenv("BOT_TOKEN")\
\
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):\
    msg = update.message\
\
    await msg.reply_text(f"\uc0\u25910 \u21040 \u65306 \{msg.text\}")\
\
    context.job_queue.run_once(delete_msg, 6, data=msg)\
\
async def delete_msg(context: ContextTypes.DEFAULT_TYPE):\
    try:\
        await context.job.data.delete()\
    except:\
        pass\
\
app = ApplicationBuilder().token(TOKEN).build()\
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))\
\
app.run_polling()}