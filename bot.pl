import re
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import os
TOKEN = os.getenv("BOT_TOKEN")

new_users = {}

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        new_users[member.id] = datetime.utcnow()
        msg = await update.message.reply_text(
            f"Привет, {member.full_name}! В течение 24 часов нельзя отправлять фото, видео и ссылки."
        )
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except:
            pass

async def restrict_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    now = datetime.utcnow()

    if user_id in new_users and (now - new_users[user_id]) < timedelta(hours=24):
        has_media = bool(update.message.photo) or bool(update.message.video) or bool(update.message.document)
        text = update.message.text or ''
        has_link = bool(re.search(r'https?://|t\.me/|www\.', text))
        if has_media or has_link:
            try:
                await update.message.delete()
                await update.message.reply_text(
                    f"{update.message.from_user.first_name}, первые 24 часа нельзя отправлять фото, видео и ссылки."
                )
            except:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает.")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), restrict_media))

if __name__ == '__main__':
    print("Бот запущен...")
    app.run_polling()