import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatMember, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

telegram_app = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

join_times = defaultdict(dict)  # chat_id -> user_id -> join_time
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> message count
muted_users = defaultdict(set)  # chat_id -> set of muted user_ids
user_msgs = defaultdict(lambda: defaultdict(deque))  # chat_id -> user_id -> deque[timestamps]


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()

        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "🚫 Первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML"
        )
        await asyncio.sleep(10)
        await msg.delete()


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    now = datetime.utcnow()

    # Если пользователь в муте
    if user_id in muted_users[chat_id]:
        await msg.delete()
        return

    # Медиа и ссылки — запрещены новым
    if user_id in join_times[chat_id] and now - join_times[chat_id][user_id] < timedelta(hours=24):
        if msg.photo or msg.video or msg.document or msg.sticker or \
           any(e.type in ["url", "text_link"] for e in msg.entities or []):
            try:
                await msg.delete()
                await msg.reply_text(
                    "🚫 Медиа и ссылки запрещены первые 24 часа!",
                    reply_to_message_id=msg.message_id
                )
            except:
                pass
            return

    # Антиспам: 3 сообщения в минуту
    timestamps = user_msgs[chat_id][user_id]
    timestamps.append(now)
    while timestamps and now - timestamps[0] > timedelta(minutes=1):
        timestamps.popleft()

    if len(timestamps) > 3:
        muted_users[chat_id].add(user_id)
        until = datetime.now() + timedelta(hours=1)
        await context.bot.restrict_chat_member(
            chat_id,
            user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until
        )
        try:
            await msg.reply_text("🔇 Вы были замучены за спам (на 1 час).")
        except:
            pass
        return

    # Подсчет сообщений (не команд)
    if msg.text and not msg.text.startswith("/"):
        rating[chat_id][user_id] += 1


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        return await update.message.reply_text("Пока нет активных пользователей.")

    top = sorted(users.items(), key=lambda x: x[1], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
    text = "<b>📊 Топ участников недели:</b>\n\n"
    for i, (uid, count) in enumerate(top):
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            name = member.user.full_name
        except:
            name = "Пользователь"
        text += f"{medals[i]} {name} — {count} сообщений\n"

    reply = await update.message.reply_text(text, parse_mode="HTML")
    await asyncio.sleep(3)
    await update.message.delete()
    await reply.delete()


async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        msg = await update.message.reply_text("Вы пока не в рейтинге.")
        await asyncio.sleep(3)
        await update.message.delete()
        await msg.delete()
        return

    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    position = next(i for i, (uid, _) in enumerate(sorted_users) if uid == user_id) + 1
    score = users[user_id]

    msg = await update.message.reply_text(
        f"🏅 Ваше место в рейтинге: {position}\n✉️ Сообщений: {score}"
    )
    await asyncio.sleep(3)
    await update.message.delete()
    await msg.delete()


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    sender = await context.bot.get_chat_member(chat_id, user_id)

    if sender.status != ChatMember.OWNER:
        return await update.message.reply_text("❌ Команда только для владельца чата.")

    if not context.args:
        return await update.message.reply_text("⚠️ Укажите ID пользователя: /unmute <user_id>")

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("⚠️ Неверный формат ID.")

    muted_users[chat_id].discard(target_id)
    await context.bot.restrict_chat_member(chat_id, target_id, ChatPermissions(can_send_messages=True))
    await update.message.reply_text("✅ Пользователь размучен.")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    member = await context.bot.get_chat_member(chat_id, user_id)

    if member.status != ChatMember.OWNER:
        return await update.message.reply_text("❌ Команда только для владельца чата.")

    await update.message.reply_text(f"ID чата: {chat_id}")


async def weekly_awards(app):
    bot = app.bot
    for chat_id, users in rating.items():
        top = sorted(users.items(), key=lambda x: x[1], reverse=True)[:5]
        text = "<b>🏆 Победители недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (uid, count) in enumerate(top):
            try:
                member = await bot.get_chat_member(chat_id, uid)
                name = member.user.full_name
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {count} сообщений\n"

        try:
            msg = await bot.send_message(chat_id, text, parse_mode="HTML")
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except:
            pass

        rating[chat_id].clear()


# === Handlers ===
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL, process_message))


# === Webhook Endpoint ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[telegram_app])
    scheduler.start()
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")


# === Local run ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
