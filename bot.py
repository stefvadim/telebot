import os
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions
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

app = FastAPI()

telegram_app = ApplicationBuilder().token(TOKEN).build()

# Данные
join_times = defaultdict(dict)  # chat_id -> user_id -> datetime вступления
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> count сообщений
last_week_winners = defaultdict(list)  # chat_id -> list топ пользователей
message_times = defaultdict(lambda: defaultdict(deque))  # chat_id -> user_id -> deque с временем сообщений
muted_users = defaultdict(set)  # chat_id -> set user_id (замучены)

# Константы
SPAM_LIMIT = 3  # сообщений
SPAM_INTERVAL = 60  # секунд
MUTE_TIME = 60 * 60  # 1 час в секундах


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for member in update.message.new_chat_members:
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        # Отправляем предупреждение
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "В первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML",
        )
        # Удаляем через 10 секунд
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except Exception:
            pass


async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админы не ограничены
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        pass

    if user_id in join_times[chat_id]:
        if datetime.utcnow() - join_times[chat_id][user_id] < timedelta(hours=24):
            # Проверяем медиа и ссылки
            if msg.photo or msg.video or any(
                e.type in ["url", "text_link"] for e in (msg.entities or [])
            ):
                try:
                    await msg.delete()
                    warn = await update.effective_chat.send_message(
                        f"{user.mention_html()}, медиа и ссылки запрещены первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=msg.message_id,
                    )
                    await asyncio.sleep(10)
                    await warn.delete()
                except Exception:
                    pass


async def check_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админы без ограничений
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        pass

    now = datetime.utcnow()
    times = message_times[chat_id][user_id]

    # Удаляем сообщения старше SPAM_INTERVAL секунд
    while times and (now - times[0]).total_seconds() > SPAM_INTERVAL:
        times.popleft()
    times.append(now)

    if len(times) > SPAM_LIMIT:
        # Мутим пользователя на час
        if user_id not in muted_users[chat_id]:
            try:
                await context.bot.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=now + timedelta(seconds=MUTE_TIME),
                )
                muted_users[chat_id].add(user_id)
                warn = await update.effective_chat.send_message(
                    f"{user.mention_html()}, вы отправляете сообщения слишком часто. Вы замучены на 1 час.",
                    parse_mode="HTML",
                )
                await asyncio.sleep(10)
                await warn.delete()
            except Exception:
                pass


async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.text is None:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админы не считаются
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        pass

    # Если пользователь замучен, не считаем сообщения
    if user_id in muted_users[chat_id]:
        return

    rating[chat_id][user_id] += 1


async def weekly_awards(app):
    bot = app.bot
    for chat_id, scores in rating.items():
        if not scores:
            continue
        top_users = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        last_week_winners[chat_id] = top_users

        text = "<b>🏆 Победители недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]

        for i, (user_id, score) in enumerate(top_users):
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                name = member.user.full_name
            except Exception:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
        rating[chat_id].clear()


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status in ("administrator", "creator"):
            await update.message.reply_text(f"ID этого чата: {chat_id}")
        else:
            await update.message.reply_text("Команда доступна только администраторам.")
    except Exception:
        await update.message.reply_text("Ошибка при проверке прав.")


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
        except Exception:
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
        f"Ваш рейтинг в этом чате:\n🏅 Место: {position}\n✉️ Сообщений: {score}"
    )


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("Команда доступна только администраторам.")
            return
    except Exception:
        await update.message.reply_text("Ошибка при проверке прав.")
        return

    # Размутим указанного пользователя, или себя (если нет аргументов)
    args = context.args
    if args:
        try:
            target_user_id = int(args[0])
        except Exception:
            await update.message.reply_text("Пожалуйста, укажите корректный ID пользователя.")
            return
    else:
        await update.message.reply_text("Пожалуйста, укажите ID пользователя для размуты.")
        return

    if target_user_id in muted_users[chat_id]:
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                target_user_id,
                permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                            can_send_polls=True, can_send_other_messages=True,
                                            can_add_web_page_previews=True, can_change_info=True,
                                            can_invite_users=True, can_pin_messages=True),
                until_date=0,
            )
            muted_users[chat_id].remove(target_user_id)
            await update.message.reply_text(f"Пользователь {target_user_id} размучен.")
        except Exception:
            await update.message.reply_text("Не удалось размутить пользователя.")
    else:
        await update.message.reply_text("Этот пользователь не находится в муте.")


# Обработчики команд
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))

# Обработчики сообщений
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

    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
