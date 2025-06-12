import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 🔐 Ваш токен
TOKEN = "7854667217:AAEpFQNVBPR_E-eFVy_I6dVXXmVOzs7bitg"

# Хранение данных
join_times = defaultdict(dict)  # chat_id -> {user_id: join_time}
rating = defaultdict(lambda: defaultdict(int))  # chat_id -> {user_id: message_count}
last_week_winners = defaultdict(list)  # chat_id -> [(user_id, score), ...]

# 📥 Приветствие новых участников
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
        await msg.delete()

# 🚫 Ограничение на медиа первые 24 часа
async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    if user_id in join_times[chat_id]:
        join_time = join_times[chat_id][user_id]
        if datetime.utcnow() - join_time < timedelta(hours=24):
            if (
                update.message.photo
                or update.message.video
                or (update.message.entities and any(
                    e.type in ["url", "text_link"] for e in update.message.entities))
            ):
                try:
                    await update.message.delete()
                    await update.effective_chat.send_message(
                        f"{user.mention_html()}, публикация медиа и ссылок запрещена первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                    )
                except Exception:
                    pass

# 📝 Подсчёт сообщений
async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    rating[chat_id][user_id] += 1

# 🏆 Еженедельные награды
async def weekly_awards(app):
    bot = app.bot
    for chat_id, user_scores in rating.items():
        sorted_users = sorted(user_scores.items(), key=lambda x: x[1], reverse=True)
        last_week_winners[chat_id] = sorted_users[:5]

        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        text = "<b>🏆 Победители прошлой недели:</b>\n\n"
        for i, (user_id, score) in enumerate(last_week_winners[chat_id]):
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                name = member.user.full_name
            except Exception:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        try:
            msg = await bot.send_message(chat_id, text, parse_mode="HTML")
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass

        rating[chat_id].clear()

# 📎 Команда /id
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"ID этого чата: {chat_id}")

# 🚀 Главная функция
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Обработчики
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, check_media_restriction))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, count_message))
    app.add_handler(CommandHandler("id", cmd_id))

    # Планировщик запуска еженедельных наград
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[app])
    scheduler.start()

    # Запуск бота
    await app.run_polling()

# 🔁 Запуск
if __name__ == "__main__":
    asyncio.run(main())
