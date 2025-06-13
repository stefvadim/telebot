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

# Для новых пользователей: время присоединения
join_times = defaultdict(dict)

# Рейтинг: chat_id -> user_id -> count_messages
rating = defaultdict(lambda: defaultdict(int))

# Топ-пользователи прошлой недели
last_week_winners = defaultdict(list)

# Антиспам: chat_id -> user_id -> deque[timestamps]
message_times = defaultdict(lambda: defaultdict(lambda: deque(maxlen=3)))

# Муты: chat_id -> user_id -> mute_end_time (datetime)
muted_users = defaultdict(dict)

# Проверка, админ ли пользователь
async def is_admin(chat_id, user_id):
    try:
        member = await telegram_app.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()
        await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "В первые 24 часа запрещено публиковать фото, видео и ссылки.",
            parse_mode="HTML",
        )

async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    user = msg.from_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админам разрешено всё
    if await is_admin(chat_id, user_id):
        return

    # Проверяем, сколько времени с момента присоединения
    if user_id in join_times[chat_id]:
        if datetime.utcnow() - join_times[chat_id][user_id] < timedelta(hours=24):
            # Если фото или видео или ссылки (entities url/text_link)
            if msg.photo or msg.video or any(
                e.type in ("url", "text_link") for e in (msg.entities or [])
            ):
                try:
                    await msg.delete()
                    await update.effective_chat.send_message(
                        f"{user.mention_html()}, медиа и ссылки запрещены первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=msg.message_id,
                    )
                except:
                    pass

async def anti_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админам не ограничиваем
    if await is_admin(chat_id, user_id):
        return

    now = datetime.utcnow()
    times = message_times[chat_id][user_id]

    times.append(now)

    # Если за последнюю минуту более 3 сообщений
    # Проверяем первый из последних трёх сообщений
    if len(times) == 3 and (now - times[0]) < timedelta(seconds=60):
        # Если пользователь ещё не замучен
        if user_id not in muted_users[chat_id] or muted_users[chat_id][user_id] < now:
            try:
                # Мут на 1 час
                until = now + timedelta(hours=1)
                muted_users[chat_id][user_id] = until
                await telegram_app.bot.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until.timestamp(),
                )
                await update.effective_chat.send_message(
                    f"{user.mention_html()}, вы отправили слишком много сообщений! Мут на 1 час.",
                    parse_mode="HTML",
                )
            except Exception as e:
                print("Mute error:", e)

async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Админам не считаем рейтинг
    if await is_admin(chat_id, user_id):
        return

    # Если пользователь в муте - не считаем
    now = datetime.utcnow()
    if user_id in muted_users[chat_id] and muted_users[chat_id][user_id] > now:
        return

    # Не считаем команды
    if msg.text.startswith("/"):
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
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        try:
            msg = await bot.send_message(chat_id, text, parse_mode="HTML")
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception as e:
            print("Weekly award error:", e)

        # Очищаем рейтинг на новую неделю
        rating[chat_id].clear()

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    # Проверяем, админ ли
    if await is_admin(chat_id, user_id):
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
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_admin(chat_id, user_id):
        await update.message.reply_text("Команда доступна только администраторам.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Использование: /unmute <user_id>")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Неверный user_id.")
        return

    try:
        await telegram_app.bot.restrict_chat_member(
            chat_id,
            target_id,
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
        )
        if target_id in muted_users[chat_id]:
            del muted_users[chat_id][target_id]
        await update.message.reply_text(f"Пользователь {target_id} размучен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при размюте: {e}")

# Фильтрация команд: только /top и /myrank для обычных пользователей
async def command_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if await is_admin(chat_id, user_id):
        # Админы могут всё
        return

    allowed = ["/top", "/myrank"]
    text = msg.text.split()[0].lower()

    if text not in allowed:
        await msg.reply_text("Команда доступна только: /top и /myrank")
        try:
            await msg.delete()
        except:
            pass

# Регистрируем хендлеры
telegram_app.add_handler(CommandHandler("id", cmd_id))
telegram_app.add_handler(CommandHandler("top", cmd_top))
telegram_app.add_handler(CommandHandler("myrank", cmd_myrank))
telegram_app.add_handler(CommandHandler("unmute", cmd_unmute))

telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_media_restriction))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, anti_spam))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, count_message))

telegram_app.add_handler(MessageHandler(filters.COMMAND, command_filter))

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
