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

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}"

# --- Инициализация ---
app = FastAPI()
telegram_app = ApplicationBuilder().token(TOKEN).build()

# --- Данные ---
join_times = defaultdict(dict)        # chat_id -> user_id -> datetime
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> счетчик сообщений
message_times = defaultdict(lambda: defaultdict(deque))  # chat_id -> user_id -> deque timestamps (для антиспама)
muted_users = defaultdict(set)        # chat_id -> set user_id (замучены)

last_week_winners = defaultdict(list)  # chat_id -> список топа прошлой недели

# --- Параметры ---
SPAM_LIMIT = 3
SPAM_INTERVAL = 60
MUTE_TIME = 3600  # 1 час в секундах

# --- Хендлеры ---

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

async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    if user_id not in join_times[chat_id]:
        return

    if datetime.utcnow() - join_times[chat_id][user_id] < timedelta(hours=24):
        if msg.photo or msg.video or any(e.type in ["url", "text_link"] for e in msg.entities or []):
            try:
                await msg.delete()
                await update.effective_chat.send_message(
                    f"{user.mention_html()}, в первые 24 часа запрещено публиковать фото, видео и ссылки!",
                    parse_mode="HTML",
                    reply_to_message_id=msg.message_id,
                )
            except:
                pass

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Проверяем статус пользователя
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        is_admin = member.status in ("administrator", "creator")
    except Exception:
        is_admin = False

    # Админы не ограничены и не считаются
    if is_admin:
        return

    # Проверка и удаление по медиа в первые 24 часа
    await check_media_restriction(update, context)

    # Антиспам
    now = datetime.utcnow()
    times = message_times[chat_id][user_id]

    # Удаляем устаревшие метки времени
    while times and (now - times[0]).total_seconds() > SPAM_INTERVAL:
        times.popleft()
    times.append(now)

    if len(times) > SPAM_LIMIT:
        # Мутим пользователя
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=now + timedelta(seconds=MUTE_TIME),
            )
            muted_users[chat_id].add(user_id)
            warn = await update.effective_chat.send_message(
                f"{user.mention_html()}, вы отправляете сообщения слишком часто и замучены на 1 час.",
                parse_mode="HTML",
            )
            await asyncio.sleep(10)
            await warn.delete()
        except Exception:
            pass
        return

    # Если пользователь замучен вручную — не считаем
    if user_id in muted_users[chat_id]:
        return

    # Считаем сообщение для рейтинга
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
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        try:
            # Закрепляем сообщение
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except:
            pass

        rating[chat_id].clear()  # Сбрасываем рейтинг после наград

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Показываем ID только админам
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        is_admin = member.status in ("administrator", "creator")
    except Exception:
        is_admin = False

    if is_admin:
        await update.message.reply_text(f"ID этого чата: {chat_id}")
    else:
        await update.message.reply_text("Команда доступна только администраторам.")

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
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        is_admin = member.status in ("administrator", "creator")
    except Exception:
        is_admin = False

    if not is_admin:
        await update.message.reply_text("Команда доступна только администраторам.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /unmute <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверный ID пользователя.")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
            permissions=ChatPermissions(can_send_messages=True,
                                        can_send_media_messages=True,
                                        can_send_other_messages=True,
                                        can_add_web_page_previews=True),
        )
        if target_id in muted_users[chat_id]:
            muted_users[chat_id].remove(target_id)
        await update.message.reply_text(f"Пользователь {target_id} размучен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# --- Регистрация хендлеров ---

telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))

telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

# --- Вебхук ---

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

# --- Стартап ---

@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[telegram_app])
    scheduler.start()
    print(f"Webhook установлен: {WEBHOOK_URL}")

# --- Запуск ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
