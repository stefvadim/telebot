import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions, ChatMember
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# === Настройки окружения ===
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

telegram_app = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

# === Хранилища данных ===
join_times = defaultdict(dict)  # chat_id -> user_id -> datetime
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> score
message_log = defaultdict(lambda: defaultdict(list))  # chat_id -> user_id -> [datetime]
muted_users = defaultdict(set)  # chat_id -> set(user_id)
last_week_winners = defaultdict(list)

# === Приветствие новых участников ===
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {member.mention_html()}!\n"
            "🚫 Первые 24 часа нельзя отправлять медиа и ссылки.",
            parse_mode="HTML"
        )
        await asyncio.sleep(3)
        await msg.delete()

# === Проверка на администратора ===
async def is_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except:
        return False

# === Обработка всех сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    now = datetime.utcnow()
    msg = update.message

    if not msg:
        return

    # Пропуск админов
    if await is_admin(context.bot, chat_id, user_id):
        return

    # Если пользователь в муте — удаляем сообщение
    if user_id in muted_users[chat_id]:
        try:
            await msg.delete()
        except:
            pass
        return

    # Проверка новичка на медиа и ссылки
    joined_at = join_times[chat_id].get(user_id)
    if joined_at and now - joined_at < timedelta(hours=24):
        if msg.photo or msg.video or msg.document or msg.audio or msg.video_note or msg.animation or \
           any(e.type in ["url", "text_link"] for e in msg.entities or []):
            try:
                await msg.delete()
                warn = await msg.reply_text(
                    f"{msg.from_user.mention_html()}, медиа и ссылки запрещены первые 24 часа!",
                    parse_mode="HTML"
                )
                await asyncio.sleep(3)
                await warn.delete()
            except:
                pass
            return

    # Считаем сообщение (флуд или нет)
    rating[chat_id][user_id] += 1

    # === Антиспам: 3 сообщения в минуту ===
    message_log[chat_id][user_id] = [
        t for t in message_log[chat_id][user_id] if (now - t).total_seconds() < 60
    ]
    message_log[chat_id][user_id].append(now)

    if len(message_log[chat_id][user_id]) > 3:
        muted_users[chat_id].add(user_id)
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int((now + timedelta(hours=1)).timestamp())
            )
            warn = await msg.reply_text(
                f"🔇 {msg.from_user.mention_html()} замьючен за флуд на 1 час.",
                parse_mode="HTML"
            )
            await asyncio.sleep(3)
            await warn.delete()
        except:
            pass

# === /top ===
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    top = sorted(rating[chat_id].items(), key=lambda x: x[1], reverse=True)[:5]

    if not top:
        await delete_reply(update, "Пока нет активных пользователей.")
        return

    medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
    text = "<b>📊 Топ-5 участников:</b>\n\n"
    for i, (uid, score) in enumerate(top):
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            name = member.user.full_name
        except:
            name = "Пользователь"
        text += f"{medals[i]} {name} — {score} сообщений\n"

    await delete_reply(update, text, parse_mode="HTML")

# === /myrank ===
async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating[chat_id]

    if user_id not in users:
        await delete_reply(update, "Вы ещё не оставили ни одного сообщения.")
        return

    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    rank = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
    score = users[user_id]
    await delete_reply(update, f"🏅 Ваше место: {rank}\n✉️ Сообщений: {score}")

# === /id (только админы) ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_admin(context.bot, chat_id, user_id):
        return

    await update.message.reply_text(f"🆔 Chat ID: {chat_id}")

# === /unmute <user_id> (только админы) ===
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sender_id = update.effective_user.id

    if not await is_admin(context.bot, chat_id, sender_id):
        await update.message.reply_text("❌ Команда доступна только администраторам.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Использование: /unmute <user_id>")
        return

    try:
        user_id = int(context.args[0])
        muted_users[chat_id].discard(user_id)
        await context.bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        await update.message.reply_text(f"✅ Пользователь {user_id} размьючен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# === Удалить ответ и запрос через 3 секунды ===
async def delete_reply(update: Update, text: str, parse_mode=None):
    try:
        msg = await update.message.reply_text(text, parse_mode=parse_mode)
        await asyncio.sleep(3)
        await update.message.delete()
        await msg.delete()
    except:
        pass

# === Награждение недели ===
async def weekly_awards(app):
    bot = app.bot
    for chat_id, scores in rating.items():
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        last_week_winners[chat_id] = top
        rating[chat_id].clear()

        text = "<b>🏆 Победители недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (uid, score) in enumerate(top):
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

# === Handlers ===
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))

# === Webhook endpoint ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

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
