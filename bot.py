import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions, ChatMember, ChatMemberOwner
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# === Конфигурация ===
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

# === Telegram App ===
telegram_app = ApplicationBuilder().token(TOKEN).build()

# === FastAPI App ===
app = FastAPI()

# === Данные ===
join_times = defaultdict(dict)  # chat_id -> user_id -> join_time
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> score
message_times = defaultdict(lambda: defaultdict(list))  # chat_id -> user_id -> [timestamps]
muted_users = defaultdict(set)  # chat_id -> set of muted user_ids
last_week_winners = defaultdict(list)  # chat_id -> [(user_id, score)]

# === Приветствие ===
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {member.mention_html()}!\n"
            "🚫 В первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML"
        )
        await asyncio.sleep(10)
        await msg.delete()

# === Проверка на владельца ===
async def is_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return isinstance(member, ChatMemberOwner)

# === Ограничение медиа + антиспам + рейтинг ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg = update.message

    member = await context.bot.get_chat_member(chat_id, user_id)
    is_admin = member.status in ["administrator", "creator"]

    now = datetime.utcnow()

    # 🔇 Антиспам (кроме админов и владельца)
    if not is_admin:
        timestamps = message_times[chat_id][user_id]
        timestamps = [t for t in timestamps if now - t < timedelta(minutes=1)]
        timestamps.append(now)
        message_times[chat_id][user_id] = timestamps

        if len(timestamps) > 3 and user_id not in muted_users[chat_id]:
            muted_users[chat_id].add(user_id)
            await context.bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=now + timedelta(hours=1)
            )
            await msg.reply_text("🚫 Вы слишком часто отправляете сообщения. Мут на 1 час.")
            return

    # 🚫 Запрет медиа/ссылок новым юзерам
    if user_id in join_times[chat_id]:
        if now - join_times[chat_id][user_id] < timedelta(hours=24):
            if msg.photo or msg.video or any(e.type in ["url", "text_link"] for e in msg.entities or []):
                try:
                    await msg.delete()
                    warn = await update.effective_chat.send_message(
                        f"{update.effective_user.mention_html()}, медиа и ссылки запрещены первые 24 часа!",
                        parse_mode="HTML"
                    )
                    await asyncio.sleep(5)
                    await warn.delete()
                except:
                    pass
                return

    # 📈 Подсчёт рейтинга
    if msg.text and not msg.text.startswith("/"):
        rating[chat_id][user_id] += 1

# === Команды /top и /myrank ===
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        reply = await update.message.reply_text("Пока нет активных пользователей.")
        await asyncio.sleep(3)
        await update.message.delete()
        await reply.delete()
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

    reply = await update.message.reply_text(text, parse_mode="HTML")
    await asyncio.sleep(3)
    await update.message.delete()
    await reply.delete()

async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        reply = await update.message.reply_text("Вы ещё не оставили ни одного сообщения.")
        await asyncio.sleep(3)
        await update.message.delete()
        await reply.delete()
        return

    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
    score = users[user_id]
    reply = await update.message.reply_text(
        f"🏅 Ваш рейтинг:\nМесто: {position}\nСообщений: {score}"
    )
    await asyncio.sleep(3)
    await update.message.delete()
    await reply.delete()

# === Команда /unmute — только владелец ===
async def is_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status == "creator"

    if not await is_owner(context, chat_id, user_id):
        await update.message.reply_text("🔒 Только владелец может использовать эту команду.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Использование: /unmute <user_id>")
        return

    target_id = int(context.args[0])
    muted_users[chat_id].discard(target_id)
    try:
        await context.bot.restrict_chat_member(
            chat_id, target_id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        await update.message.reply_text(f"✅ Пользователь {target_id} размучен.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

# === /id — только для админов ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    member = await context.bot.get_chat_member(chat_id, user_id)

    if member.status not in ["administrator", "creator"]:
        await update.message.reply_text("Команда доступна только администраторам.")
        return

    await update.message.reply_text(f"🆔 ID этого чата: {chat_id}")

# === Еженедельные награды ===
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

# === Регистрируем хендлеры ===
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

# === Webhook endpoint ===
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

# === Запуск локально ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
