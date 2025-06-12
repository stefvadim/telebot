import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)


TOKEN = "7854667217:AAEpFQNVBPR_E-eFVy_I6dVXXmVOzs7bitg"

# Словарь для хранения времени вступления пользователей {chat_id: {user_id: join_time}}
join_times = defaultdict(dict)

# Рейтинг сообщений {chat_id: {user_id: count}}
rating = defaultdict(lambda: defaultdict(int))

# Победители прошлой недели {chat_id: [(user_id, score), ...]}
last_week_winners = defaultdict(list)


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Приветствие нового участника + запрет на медиа первые 24 часа
    for member in update.message.new_chat_members:
        chat_id = update.effective_chat.id
        user_id = member.id
        join_times[chat_id][user_id] = datetime.utcnow()

        # Отправляем приветствие
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {member.mention_html()}!\n"
            "В первые 24 часа нельзя отправлять фото, видео и ссылки.",
            parse_mode="HTML"
        )
        # Удаляем приветствие через 10 секунд
        await asyncio.sleep(10)
        await msg.delete()


async def check_media_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Проверяем, сколько пользователь в чате
    if user_id in join_times[chat_id]:
        join_time = join_times[chat_id][user_id]
        now = datetime.utcnow()

        if now - join_time < timedelta(hours=24):
            # Если отправлено фото, видео или ссылка — удаляем сообщение
            if (
                update.message.photo
                or update.message.video
                or (update.message.entities and any(e.type in ["url", "text_link"] for e in update.message.entities))
            ):
                try:
                    await update.message.delete()
                    await update.effective_chat.send_message(
                        f"{user.mention_html()}, публикация медиа и ссылок запрещена первые 24 часа!",
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                    )
                except:
                    pass
                return


async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Увеличиваем счетчик сообщений
    rating[chat_id][user_id] += 1


async def weekly_awards(context: ContextTypes.DEFAULT_TYPE):
    # Раздаем награды и сбрасываем рейтинг
    bot = context.bot
    for chat_id, users_scores in rating.items():
        # Сортируем по убыванию
        sorted_scores = sorted(users_scores.items(), key=lambda x: x[1], reverse=True)
        last_week_winners[chat_id] = sorted_scores[:5]  # топ 5

        # Формируем текст с победителями
        text = "<b>🏆 Победители прошлой недели:</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
        for i, (user_id, score) in enumerate(last_week_winners[chat_id]):
            try:
                user = await bot.get_chat_member(chat_id, user_id)
                name = user.user.full_name
            except:
                name = "Пользователь"
            text += f"{medals[i]} {name} — {score} сообщений\n"

        # Отправляем и закрепляем сообщение с наградами
        msg = await bot.send_message(chat_id, text, parse_mode="HTML")
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)

        # Сбрасываем рейтинг
        rating[chat_id].clear()


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"ID этого чата: {chat_id}")


async def start_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))

    # Отслеживаем все сообщения для подсчёта рейтинга и проверки медиа
    app.add_handler(MessageHandler(filters.ALL & (~filters.StatusUpdate.NEW_CHAT_MEMBERS), check_media_restriction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.StatusUpdate.NEW_CHAT_MEMBERS), count_message))

    app.add_handler(CommandHandler("id", cmd_id))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(weekly_awards, "cron", day_of_week="mon", hour=0, minute=0, args=[app.job_queue])
    scheduler.start()

    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(start_bot())
