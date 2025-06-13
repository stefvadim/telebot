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

# === Конфигурация ===
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

# === Telegram Application ===
telegram_app = ApplicationBuilder().token(TOKEN).build()

# === FastAPI app ===
app = FastAPI()

# === Данные ===
join_times = defaultdict(dict)  # chat_id -> user_id -> join_time (datetime)
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> score
last_week_winners = defaultdict(list)  # chat_id -> [(user_id, score)]
message_timestamps = defaultdict(lambda: defaultdict(deque))  # chat_id -> user_id -> deque of message timestamps
muted_users = defaultdict(set)  # chat_id -> set of muted user_ids


# === Проверка, является ли пользователь админом или владельцем ===
async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except:
        return False


# === Приветствие новых пользователей ===
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for member in update.message.new_chat_members:
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "🚫 В первые 24 часа запрещено публиковать фото, видео и ссылки.",
            parse_mode="HTML",
        )
        # Сообщение приветствия удалим через 10 секунд
        await asyncio.sleep(10)
        await msg.delete()


# === Ограничение на медиа/ссылки и антиспам + подсчет сообщений ===
async def media_restrict_and_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg = update.message

    if msg is None:
        return

    # Админы и владельцы не ограничены
    if await is_admin(chat_id, user_id, context):
        return

    now = datetime.utcnow()

    # Проверяем, есть ли мут на пользователя
    if user_id in muted_users[chat_id]:
        try:
            await msg.delete()
        except:
            pass
        return  # Игнорируем сообщения от замученных

    # Запрет медиа и ссылок первые 24 часа
    if user_id in join_times[chat_id]:
        if now - join_times[chat_id][user_id] < timedelta(hours=24):
            if (
                msg.photo or msg.video or msg.animation or msg.document
                or any(e.type in ["url", "text_link"] for e in msg.entities or [])
            ):
                try:
                    await msg.delete()
                    warning = await update.effective_chat.send_message(
                        f"{update.effective_user.mention_html()}, в первые 24 часа запрещено публиковать фото, видео и ссылки!",
                        parse_mode="HTML",
                        reply_to_message_id=msg.message_id,
                    )
                    await asyncio.sleep(3)
                    await warning.delete()
                except:
                    pass
                return  # Не считаем такие сообщения

    # Антиспам: не более 3 сообщений в минуту
    timestamps = message_timestamps[chat_id][user_id]
    timestamps.append(now)
    # Удаляем старые таймстампы (> 60 сек)
    while timestamps and (now - timestamps[0]) > timedelta(seconds=60):
        timestamps.popleft()

    if len(timestamps) > 3:
        # Мут на 1 час
        try:
            until = now + timedelta(hours=1)
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            muted_users[chat_id].add(user_id)
            warn_msg = await update.effective_chat.send_message(
                f"{update.effective_user.mention_html()}, вы были замучены на 1 час за спам.",
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
            )
            await asyncio.sleep(3)
            await warn_msg.delete()
        except:
            pass
        try:
            await msg.delete()
        except:
            pass
        # Очистим историю сообщений пользователя после мута
        message_timestamps[chat_id][user_id].clear()
        return

    # Подсчет сообщений для рейтинга (только текст без команд)
    if msg.text and not msg.text.startswith("/"):
        rating[chat_id][user_id] += 1


# === Команда /id — только для админов ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("Команда доступна только администраторам.")
        return

    await update.message.reply_text(f"ID этого чата: {chat_id}")


# === Команда /top — топ 5 пользователей с автоудалением ===
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users = rating.get(chat_id, {})
    if not users:
        msg = await update.message.reply_text("Пока нет активных пользователей.")
        await asyncio.sleep(3)
        await msg.delete()
        await update.message.delete()
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
    await msg.delete()
    await update.message.delete()


# === Команда /myrank — позиция пользователя с автоудалением ===
async def cmd_myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    users = rating.get(chat_id, {})
    if user_id not in users:
        msg = await update.message.reply_text("Вы ещё не оставили ни одного сообщения.")
        await asyncio.sleep(3)
        await msg.delete()
        await update.message.delete()
        return
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), None)
    score = users[user_id]

    text = f"Ваш рейтинг в этом чате:\n🏅 Место: {position}\n✉️ Сообщений: {score}"
    msg = await update.message.reply_text(text)
    await asyncio.sleep(3)
    await msg.delete()
    await update.message.delete()


# === Команда /unmute <user_id> для админов и владельцев ===
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("Команда доступна только администраторам.")
        return

    if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /unmute <user_id>")
        return

    target_id = int(context.args[0])

    try:
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
            permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
        )
        muted_users[chat_id].discard(target_id)
        await update.message.reply_text(f"Пользователь {target_id} был размучен.")
    except Exception as e:
        await update.message.reply_text(f"Не удалось размутить пользователя: {e}")


# === Еженедельное объявление победителей с закреплением ===
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
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, media_restrict_and_count))


# === Webhook endpoint ===
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


# === При старте FastAPI ===
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[telegram_app])
    scheduler.start()
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")


# === Запуск локального сервера ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
