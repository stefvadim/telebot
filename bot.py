import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# === Конфигурация ===
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

telegram_app = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# === Хранилища ===
join_times = defaultdict(dict)  # chat_id -> user_id -> datetime
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> score
last_messages = defaultdict(lambda: defaultdict(list))  # chat_id -> user_id -> [timestamps]
muted_users = defaultdict(set)  # chat_id -> set(user_ids)

# === Утилиты ===
async def is_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status == "creator"

async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status in ["administrator", "creator"]

# === Приветствие новых ===
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        join_times[chat_id][member.id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {member.mention_html()}!\n"
            "В течение первых 24 часов запрещены фото, видео и ссылки.",
            parse_mode="HTML",
        )
        await asyncio.sleep(10)
        await msg.delete()

# === Обработка сообщений: антиспам, рейтинг, запрет медиа ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg = update.message

    # Админов не ограничиваем
    if await is_admin(context, chat_id, user_id):
        return

    now = datetime.utcnow()

    # === Антиспам: не более 3 сообщений в минуту ===
    timestamps = last_messages[chat_id][user_id]
    timestamps.append(now)
    last_messages[chat_id][user_id] = [t for t in timestamps if now - t < timedelta(minutes=1)]

    if len(last_messages[chat_id][user_id]) > 3:
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=now + timedelta(hours=1)
            )
            muted_users[chat_id].add(user_id)
            await msg.reply_text("🔇 Вы были замучены за спам на 1 час.")
        except:
            pass
        return

    # === Запрет медиа и ссылок для новых ===
    if user_id in join_times[chat_id]:
        if now - join_times[chat_id][user_id] < timedelta(hours=24):
            if msg.photo or msg.video or any(e.type in ["url", "text_link"] for e in msg.entities or []):
                try:
                    await msg.delete()
                    await msg.reply_text(
                        "🚫 Медиа и ссылки запрещены в первые 24 часа.",
                        quote=True
                    )
                except:
                    pass
                return

    # === Подсчёт сообщений для рейтинга ===
    if msg.text and not msg.text.startswith("/"):
        rating[chat_id][user_id] += 1

# === /top команда ===
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        await auto_delete_reply(update, "Пока нет активных пользователей.")
        return

    top = sorted(users.items(), key=lambda x: x[1], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
    text = "<b>📊 Топ-5 участников:</b>\n\n"
    for i, (uid, score) in enumerate(top):
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            name = member.user.full_name
        except:
            name = "Пользователь"
        text += f"{medals[i]} {name} — {score} сообщений\n"

    await auto_delete_reply(update, text, parse_mode="HTML")

# === /myrank команда ===
async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        await auto_delete_reply(update, "Вы ещё не оставили ни одного сообщения.")
        return
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
    score = users[user_id]
    await auto_delete_reply(update, f"🏅 Ваше место: {position}\n✉️ Сообщений: {score}")

# === /id — только для админов ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_admin(context, update.effective_chat.id, update.effective_user.id):
        await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}")
    else:
        await update.message.reply_text("🚫 Только для админов.")

# === /unmute — только для владельца ===
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    from_user = update.effective_user

    if not await is_owner(context, chat_id, from_user.id):
        await update.message.reply_text("🚫 Команда доступна только владельцу чата.")
        return

    if not context.args:
        await update.message.reply_text("❗ Используйте: /unmute <user_id>")
        return

    try:
        target_id = int(context.args[0])
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        muted_users[chat_id].discard(target_id)
        await update.message.reply_text(f"✅ Пользователь {target_id} размучен.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

# === Автоудаление ответов (включая команды) ===
async def auto_delete_reply(update: Update, text: str, parse_mode=None):
    msg = await update.message.reply_text(text, parse_mode=parse_mode)
    await asyncio.sleep(3)
    await update.message.delete()
    await msg.delete()

# === Еженедельное награждение ===
async def weekly_awards(app):
    bot = app.bot
    for chat_id, scores in rating.items():
        top_users = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        text = "<b>🏆 Победители недели:</b>\n\n"
        for i, (uid, score) in enumerate(top_users):
            try:
                member = await bot.get_chat_member(chat_id, uid)
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

# === Обработка Webhook ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

# === Запуск при старте ===
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[telegram_app])
    scheduler.start()
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

# === Хендлеры ===
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# === Локальный запуск ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
