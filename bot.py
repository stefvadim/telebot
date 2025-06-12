from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio

TOKEN = "7854667217:AAEpFQNVBPR_E-eFVy_I6dVXXmVOzs7bitg"

# Хранилище данных
join_times = defaultdict(dict)
rating = defaultdict(lambda: defaultdict(int))
last_week_winners = defaultdict(list)


# 1. Приветствие и ограничение
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        user_id = member.id
        chat_id = update.effective_chat.id
        join_times[chat_id][user_id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "В первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML",
        )
        await asyncio.sleep(10)
        await msg.delete()


# 2. Проверка на медиа
async def check_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    if user_id in join_times[chat_id]:
        join_time = join_times[chat_id][user_id]
        if datetime.utcnow() - join_time < timedelta(hours=24):
            if (
                update.message.photo
                or update.message.video
                or (update.message.entities and any(e.type in ["url", "text_link"] for e in update.message.entities))
            ):
                try:
                    await update.message.delete()
                    await update.effective_chat.send_message(
                        f"{user.mention_html()}, нельзя отправлять медиа и ссылки первые 24 часа.",
                        parse_mode="HTML",
                    )
                except:
                    pass


# 3. Подсчёт сообщений
async def count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    rating[chat_id][user_id] += 1


# 4. Топ активных
async def weekly_awards(app):
    bot = app.bot
    for chat_id, users in rating.items():
        top_users = sorted(users.items(), key=lambda x: x[1], reverse=True)[:5]
        last_week_winners[chat_id] = top_users

        text = "<b>🏆 Победители недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (uid, count) in enumerate(top_users):
            try:
                member = await bot.get_chat_member(chat_id, uid)
                name = member.user.full_name
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {count} сообщений\n"

        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)

        rating[chat_id].clear()


# 5. Команда /id
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID чата: {update.effective_chat.id}")


# Главный запуск
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.ALL, check_media))
    app.add_handler(MessageHandler(filters.TEXT, count))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[app])
    scheduler.start()

    app.run_polling()


if __name__ == "__main__":
    main()
