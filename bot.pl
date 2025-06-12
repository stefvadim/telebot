import os
import json
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Загрузка токена
TOKEN = os.getenv("BOT_TOKEN")

# Файл с рейтингами
SCORE_FILE = "scores.json"

# Загружаем баллы из файла
if os.path.exists(SCORE_FILE):
    with open(SCORE_FILE, "r", encoding="utf-8") as f:
        scores = json.load(f)
else:
    scores = {}

# Функция сохранения баллов в файл
def save_scores():
    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f)

# Обработка сообщений (начисление очков)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = str(user.id)

    # Начисляем балл за каждое сообщение
    if user_id not in scores:
        scores[user_id] = {"name": user.full_name, "score": 0}

    scores[user_id]["name"] = user.full_name  # обновление имени
    scores[user_id]["score"] += 1
    save_scores()

# Команда /топ
async def top_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sorted_users = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    top_text = "🏆 Топ участников:\n\n"

    for i, (user_id, data) in enumerate(sorted_users[:10], 1):
        top_text += f"{i}. {data['name']} — {data['score']} баллов\n"

    await update.message.reply_text(top_text)

# Запуск бота
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("топ", top_scores))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Бот запущен.")
    app.run_polling()
