import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)

# Конфигурация
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

# Telegram Application
telegram_app = ApplicationBuilder().token(TOKEN).build()

# FastAPI
app = FastAPI()

# Хранилища
join_times = defaultdict(dict)               # {chat_id: {user_id: join_time}}
rating = defaultdict(lambda: defaultdict(int))  # {chat_id: {user_id: message_count}}
message_times = defaultdict(lambda: defaultdict(list))  # {chat_id: {user_id: [timestamps]}}
muted_users = defaultdict(dict)              # {chat_id: {user_id: mute_end_time}}
last_week_winners = defaultdict(list)

# Приветствие новых пользователей
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()

        msg = await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {member.mention_html()}!\n"
            "⛔ В течение 24 часов запрещены фото, видео и ссылки.",
            parse_mode="HTML"
        )
        await asyncio.sleep(10)
        await msg.delete()

# Ограничение медиа
async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id in join_times[chat_id]:
        if datetime.utcnow() - join_times[chat_id][user_id] < timedelta(hours=24):
            msg = update.message
            if msg.photo or msg.video or any(e.type in ["url", "text_link"] for e in msg.entities or []):
                try:
                    await msg.delete()
                    await update.effective_chat.send_message(
                        f"{update.effective_user.mention_html()}, медиа запрещены первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=msg.message_id
                    )
                except:
                    pass

# Антиспам
async def check_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    if user_id in join_times[chat_id]:
        # Получение админов
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [a.user.id for a in admins]

        if user_id not in admin_ids:
            now = datetime.utcnow()
            timestamps = message_times[chat_id][user_id]
            timestamps = [t for t in timestamps if (now - t) < timedelta(minutes=1)]
            timestamps.append(now)
            message_times[chat_id][user_id] = timestamps

            if len(timestamps) > 3 and user_id not in muted_users[chat_id]:
                until = now + timedelta(hours=1)
                try:
                    await context.bot.restrict_chat_member(
                        chat_id, user_id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=until
                    )
                    muted_users[chat_id][user_id] = until
                    await update.effective_chat.send_message(
                        f"🔇 {user.mention_html()} замучен на 1 час за спам.",
                        parse_mode="HTML"
                    )
                except:
                    pass

# Подсчёт сообщений
async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text and not update.message.text.startswith("/"):
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        rating[chat_id][user_id] += 1

# Команды
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if user_id in [a.user.id for a in admins]:
        await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")
    else:
        await update.message.reply_text("⛔ Только админы могут использовать эту команду.")

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        await update.message.reply_text("Нет активных участников.")
        return
    top = sorted(users.items(), key=lambda x: x[1], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
    text = "<b>📊 Топ-5 участников недели:</b>\n\n"
    for i, (uid, score) in enumerate(top):
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            name = member.user.full_name
        except:
            name = "Пользователь"
        text += f"{medals[i]} {name} — {score} сообщений\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        await update.message.reply_text("Вы ещё не оставили ни одного сообщения.")
        return
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
    score = users[user_id]
    await update.message.reply_text(
        f"Ваш рейтинг:\n🏅 Место: {position}\n✉️ Сообщений: {score}"
    )

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admins = await context.bot.get_chat_administrators(chat_id)
    admin_ids = [a.user.id for a in admins]

    if update.effective_user.id not in admin_ids:
        await update.message.reply_text("⛔ Только админы могут размутить участников.")
        return

    if not context.args:
        await update.message.reply_text("Используйте: /unmute <user_id>")
        return

    try:
        user_id = int(context.args[0])
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        muted_users[chat_id].pop(user_id, None)
        await update.message.reply_text(f"✅ Пользователь {user_id} размучен.")
    except Exception as e:
        await update.message.reply_text("Ошибка при размуте.")

# Еженедельный рейтинг
async def weekly_awards(app):
    bot = app.bot
    for chat_id, scores in rating.items():
        top_users = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        last_week_winners[chat_id] = top_users

        text = "<b>🏆 Победители недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (user_id, score) in enumerate(top_users):
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                name = member.user.full_name
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        try:
            msg = await bot.send_message(chat_id, text, parse_mode="HTML")
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except:
            pass

        rating[chat_id].clear()

# Регистрируем хендлеры
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_media_restriction))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_spam))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, count_message))

# Webhook endpoint
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

# Запуск
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[telegram_app])
    scheduler.start()
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

# Локальный запуск
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
