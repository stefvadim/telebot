import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatMember, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

telegram_app = ApplicationBuilder().token(TOKEN).build()
app = FastAPI()

join_times = defaultdict(dict)
rating = defaultdict(lambda: defaultdict(int))
last_week_winners = defaultdict(list)
muted_users = defaultdict(set)
user_message_times = defaultdict(lambda: defaultdict(deque))

MAX_MSG_PER_MINUTE = 3
MUTE_DURATION = timedelta(hours=1)

async def is_admin(bot, chat_id, user_id) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.CREATOR]
    except:
        return False

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "В первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML",
        )
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except:
            pass

async def anti_spam_and_media_restrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg = update.message

    if await is_admin(context.bot, chat_id, user_id):
        if msg.text and not msg.text.startswith("/"):
            rating[chat_id][user_id] += 1
        return

    if user_id in muted_users[chat_id]:
        try:
            await msg.delete()
        except:
            pass
        return

    now = datetime.utcnow()

    join_time = join_times[chat_id].get(user_id)
    if join_time and (now - join_time < timedelta(hours=24)):
        has_media = msg.photo or msg.video or msg.animation or msg.document or msg.sticker
        has_link = any(e.type in ["url", "text_link"] for e in (msg.entities or []))
        if has_media or has_link:
            try:
                await msg.delete()
                warn = await update.effective_chat.send_message(
                    f"{update.effective_user.mention_html()}, медиа и ссылки запрещены первые 24 часа!",
                    parse_mode="HTML",
                    reply_to_message_id=msg.message_id,
                )
                await asyncio.sleep(3)
                await warn.delete()
            except:
                pass
            return

    times = user_message_times[chat_id][user_id]
    times.append(now)
    while times and now - times[0] > timedelta(minutes=1):
        times.popleft()

    if len(times) > MAX_MSG_PER_MINUTE:
        muted_users[chat_id].add(user_id)
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int((now + MUTE_DURATION).timestamp()),
            )
            warn = await update.effective_chat.send_message(
                f"{update.effective_user.mention_html()} был замьючен за флуд на 1 час.",
                parse_mode="HTML",
            )
            await asyncio.sleep(5)
            await warn.delete()
        except:
            pass
        return

    # В рейтинг — только некомандные текстовые сообщения
    if msg.text and not msg.text.startswith("/"):
        rating[chat_id][user_id] += 1

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not await is_admin(context.bot, chat_id, user_id):
        await update.message.reply_text("❌ Команда доступна только администраторам и владельцу.")
        return
    await update.message.reply_text(f"🆔 ID этого чата: {chat_id}")

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sender_id = update.effective_user.id

    if not await is_admin(context.bot, chat_id, sender_id):
        await update.message.reply_text("❌ Команда доступна только администраторам и владельцу.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Использование: /unmute <user_id>")
        return

    try:
        user_id = int(context.args[0])
        if user_id in muted_users[chat_id]:
            muted_users[chat_id].remove(user_id)
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False,
                ),
                until_date=0,
            )
            await update.message.reply_text(f"✅ Пользователь {user_id} размьючен.")
        else:
            await update.message.reply_text("Этот пользователь не находится в муте.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        await update.message.reply_text("Пока нет активных пользователей.")
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
    msg = await update.message.reply_text(text, parse_mode="HTML")

    await asyncio.sleep(3)
    try:
        await update.message.delete()
        await msg.delete()
    except:
        pass

async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        msg = await update.message.reply_text("Вы ещё не оставили ни одного сообщения.")
    else:
        sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
        position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
        score = users[user_id]
        msg = await update.message.reply_text(
            f"Ваш рейтинг в этом чате:\n🏅 Место: {position}\n✉️ Сообщений: {score}"
        )
    await asyncio.sleep(3)
    try:
        await update.message.delete()
        await msg.delete()
    except:
        pass

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

telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL, anti_spam_and_media_restrict))

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8000)
