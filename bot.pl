import os
import json
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
SCORE_FILE = "scores.json"
NEW_USER_TIMEOUT_HOURS = 24
GROUP_ID = -1001234567890  # Замени на свой ID

scores = {}
if os.path.exists(SCORE_FILE):
    with open(SCORE_FILE, "r", encoding="utf-8") as f:
        scores = json.load(f)

def save_scores():
    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f)

# Для хранения времени входа новых пользователей (user_id: datetime)
new_users = {}

pinned_message_id = None

async def welcome_and_restrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и ограничение новых участников"""
    for member in update.message.new_chat_members:
        user_id = member.id
        now = datetime.utcnow()
        new_users[user_id] = now

        # Ограничиваем пользователя: запрет на отправку медиа и ссылок первые 24 часа
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        try:
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=user_id,
                permissions=permissions,
                until_date=now + timedelta(hours=NEW_USER_TIMEOUT_HOURS)
            )
        except Exception as e:
            print(f"Ошибка ограничения пользователя {user_id}: {e}")

        # Приветственное сообщение
        welcome_msg = await update.message.reply_text(
            f"Добро пожаловать, {member.full_name}! "
            f"Вам запрещено публиковать фото, видео и ссылки в течение 24 часов."
        )

        # Удаляем приветствие через 10 секунд
        await asyncio.sleep(10)
        try:
            await welcome_msg.delete()
        except Exception:
            pass  # если удалить не удалось — не страшно

async def check_and_restrict_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрет на медиа/ссылки для новых пользователей в первые 24 часа"""
    user = update.message.from_user
    user_id = user.id
    chat_id = update.effective_chat.id

    if user_id in new_users:
        joined_at = new_users[user_id]
        now = datetime.utcnow()
        if now - joined_at < timedelta(hours=NEW_USER_TIMEOUT_HOURS):
            # Проверяем, есть ли медиа, ссылки или документы в сообщении
            if (
                update.message.photo or
                update.message.video or
                update.message.document or
                update.message.audio or
                update.message.voice or
                update.message.video_note or
                update.message.animation or
                (update.message.entities and any(e.type in ["url", "text_link"] for e in update.message.entities))
            ):
                try:
                    await update.message.delete()
                    warning = await update.message.reply_text(
                        f"{user.full_name}, вы не можете публиковать медиа и ссылки в первые 24 часа после вступления."
                    )
                    await asyncio.sleep(5)
                    await warning.delete()
                except Exception:
                    pass
                return  # сообщение удалено, дальше не считаем баллы

    # Если прошло 24 часа или пользователь не новый — считаем баллы
    await handle_message(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = str(user.id)
    if user_id not in scores:
        scores[user_id] = {"name": user.full_name, "score": 0}
    scores[user_id]["name"] = user.full_name
    scores[user_id]["score"] += 1
    save_scores()

async def top_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not scores:
        await update.message.reply_text("Рейтинг пуст.")
        return
    sorted_users = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    text = "🏆 Топ участников:\n\n"
    for i, (user_id, data) in enumerate(sorted_users[:10], 1):
        text += f"{i}. {data['name']} — {data['score']} баллов\n"
    await update.message.reply_text(text)

async def reset_weekly_scores(app):
    global scores, pinned_message_id
    if not scores:
        return
    sorted_users = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    text = "🏅 Итоги недели:\n\n"
    titles = ["🥇 Звезда недели", "🥈 Активист", "🥉 Участник недели"]
    for i, (user_id, data) in enumerate(sorted_users[:3], 0):
        text += f"{titles[i]} — {data['name']} ({data['score']} баллов)\n"
    text += "\nРейтинг сброшен. Начинаем новую неделю!"

    try:
        msg = await app.bot.send_message(chat_id=GROUP_ID, text=text)
        if pinned_message_id:
            try:
                await app.bot.unpin_chat_message(chat_id=GROUP_ID, message_id=pinned_message_id)
            except Exception:
                pass
        await app.bot.pin_chat_message(chat_id=GROUP_ID, message_id=msg.message_id)
        pinned_message_id = msg.message_id
    except Exception as e:
        print(f"Ошибка при отправке или закреплении сообщения: {e}")

    scores = {}
    save_scores()
    # Очистим список новых пользователей (т.к. рейтинг сброшен)
    new_users.clear()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот рейтинга. Пиши сообщения и набирай баллы!")

async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"ID этого чата: {chat.id}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top_scores))
    app.add_handler(CommandHandler("id", chat_id))
    # Обработка новых участников
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_and_restrict))
    # Обработка сообщений с проверкой медиа у новых участников
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), check_and_restrict_media))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: app.create_task(reset_weekly_scores(app)),
                      trigger="cron", day_of_week="sat", hour=23, minute=59)
    scheduler.start()

    print("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
