import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# 🔐 Вставь свой токен
TOKEN = "7854667217:AAEpFQNVBPR_E-eFVy_I6dVXXmVOzs7bitg"

# 🧠 Хранилища данных
join_times = defaultdict(dict)  # {chat_id: {user_id: join_time}}
rating = defaultdict(lambda: defaultdict(int))  # {chat_id: {user_id: message_count}}
last_week_winners = defaultdict(list)  # {chat_id: [(user_id, score), ...]}


# 👋 Приветствие новых пользователей
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


# 🚫 Ограничение медиа и ссылок
async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    if user_id in join_times[chat_id]:
        join_time = join_times[chat_id][user_id]
        if datetime.utcnow() - join_time < timedelta(hours=24):
            message = update.message
            has_media = message.photo or message.video
            has_links = message.entities and any(e.type in ["url", "text_link"] for e in message.entities)
            if has_media or has_links:
                try:
                    await message.delete()
                    await update.effective_chat.send_message(
                        f"{user.mention_html()}, публикация медиа и ссылок запрещена первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=message.message_id,
                    )
                except Exception:
                    pass


# 📈 Подсчёт сообщений
async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    rating[chat_id][user_id] += 1


# 🏆 Еженедельные награды
async def weekly_awards(app):
    bot = app.bot
    for chat_id, users_scores in rating.items():
        sorted_scores = sorted(users_scores.items(), key=lambda x: x[1], reverse=True)
        last_week_winners[chat_id] = sorted_scores[:5]

        text = "<b>🏆 Победители прошлой недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (user_id, score) in enumerate(last_week_winners[chat_id]):
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                name = member.user.full_name
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)

        rating[chat_id].clear()


# 🔧 Команда /id
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}")


# 🚀 Главная асинхронная функция
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Обработчики
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, check_media_restriction))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, count_message))
    app.add_handler(CommandHandler("id", cmd_id))

    # Планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[app])
    scheduler.start()

    # Запуск приложения
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("Бот запущен.")
    await asyncio.Event().wait()  # Бесконечное ожидание


# 🎯 Точка входа
if __name__ == "__main__":
    asyncio.run(main())
