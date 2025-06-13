import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)

TOKEN = "ВАШ_ТОКЕН_ОТСЮДА"
SPAM_LIMIT = 3  # сообщений
SPAM_INTERVAL = 60  # секунд
MUTE_DURATION = 3600  # секунд (1 час)

# Хранилища данных
join_times = defaultdict(dict)        # chat_id -> user_id -> join_datetime
message_count = defaultdict(lambda: defaultdict(int))  # chat_id -> user_id -> count
user_spam = defaultdict(lambda: defaultdict(deque))    # chat_id -> user_id -> deque of timestamps
muted_until = defaultdict(dict)       # chat_id -> user_id -> mute_deadline
weekly_winners = defaultdict(list)    # chat_id -> last week's winners


# Проверка — админ или владелец?
async def is_admin(chat_id, user_id, bot):
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except:
        return False


# Приветствие + запрет медиа/ссылок
async def welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for u in update.message.new_chat_members:
        join_times[update.effective_chat.id][u.id] = datetime.utcnow()
        msg = await update.effective_chat.send_message(
            f"Добро пожаловать, {u.mention_html()}!\nВ первые 24 ч запрещено публиковать медиа и ссылки.",
            parse_mode="HTML"
        )
        await asyncio.sleep(5)
        await msg.delete()


async def check_new_restrictions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    if user_id in join_times[chat_id]:
        if datetime.utcnow() - join_times[chat_id][user_id] < timedelta(hours=24):
            m = update.message
            if m.photo or m.video or any(e.type in ("url", "text_link") for e in (m.entities or [])):
                try:
                    await m.delete()
                    msg = await update.effective_chat.send_message(
                        f"{user.mention_html()}, нельзя публиковать медиа и ссылки первые 24 ч!",
                        parse_mode="HTML",
                        reply_to_message_id=m.message_id
                    )
                    await asyncio.sleep(3)
                    await msg.delete()
                except: pass


# Подсчет + антиспам + mute
async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if not m or m.text and m.text.startswith("/"):
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    uid = user.id
    bot = ctx.bot

    # Админ не ограничен
    if await is_admin(chat_id, uid, bot):
        message_count[chat_id][uid] += 1
        return

    now = datetime.utcnow()

    # Проверяем mute
    until = muted_until[chat_id].get(uid)
    if until and now < until:
        try:
            await m.delete()
        except: pass
        return  # игнорим

    # Антиспам: track timestamps
    dq = user_spam[chat_id][uid]
    dq.append(now)
    while dq and (now - dq[0]).total_seconds() > SPAM_INTERVAL:
        dq.popleft()

    if len(dq) > SPAM_LIMIT:
        # мутим на час
        muted_until[chat_id][uid] = now + timedelta(seconds=MUTE_DURATION)
        user_spam[chat_id][uid].clear()
        await ctx.bot.restrict_chat_member(
            chat_id, uid,
            permissions=...  # здесь указать ограничения: без отправки сообщений
        )
        await ctx.bot.send_message(chat_id, f"{user.mention_html()} замьючен за спам на час", parse_mode="HTML")
        return

    # Подсчет сообщений
    message_count[chat_id][uid] += 1


# Команда /top
async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    top5 = sorted(message_count[chat_id].items(), key=lambda x: x[1], reverse=True)[:5]
    if not top5:
        return await update.message.reply_text("Пока нет сообщений.")
    txt = "📊 Текущий топ-5:\n"
    medals = ["🥇","🥈","🥉","🎖️","🎖️"]
    for i,(uid,cnt) in enumerate(top5):
        name = (await ctx.bot.get_chat_member(chat_id, uid)).user.full_name
        txt += f"{medals[i]} {name} — {cnt}\n"
    msg = await update.message.reply_text(txt)
    await asyncio.sleep(3); await msg.delete()
    await asyncio.sleep(3); await update.message.delete()


# Команда /myrank
async def cmd_myrank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    ranklist = sorted(message_count[chat_id].items(), key=lambda x: x[1], reverse=True)
    if uid not in dict(ranklist):
        return await update.message.reply_text("У вас нет сообщений.")
    position = next(i+1 for i,(u,_) in enumerate(ranklist) if u==uid)
    score = message_count[chat_id][uid]
    msg = await update.message.reply_text(f"Ваш ранг: {position}, сообщений: {score}")
    await asyncio.sleep(3); await msg.delete()
    await asyncio.sleep(3); await update.message.delete()


# /id и /unmute только для админов
async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if not await is_admin(chat_id, uid, ctx.bot):
        return
    await update.message.reply_text(f"Chat ID = {chat_id}")

async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid_from = update.effective_user.id
    if not await is_admin(chat_id, uid_from, ctx.bot):
        return
    parts = update.message.text.split()
    if len(parts)!=2 or not parts[1].isdigit():
        return await update.message.reply_text("Usage: /unmute <user_id>")
    uid = int(parts[1])
    if uid in muted_until[chat_id]:
        muted_until[chat_id].pop(uid, None)
        await ctx.bot.restrict_chat_member(
            chat_id, uid,
            permissions=None  # вернуть предыдущие права
        )
        await update.message.reply_text(f"User {uid} размьючен.")


# Еженедельный отчет
async def weekly_report(app):
    for chat_id, scores in message_count.items():
        leaderboard = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        if not leaderboard: continue
        txt = "🏆 Победители недели:\n"
        medals = ["🥇","🥈","🥉","🎖️","🎖️"]
        for i,(uid,cnt) in enumerate(leaderboard):
            name=(await app.bot.get_chat_member(chat_id,uid)).user.full_name
            txt+= f"{medals[i]} {name} — {cnt}\n"
        msg=await app.bot.send_message(chat_id, txt)
        await app.bot.pin_chat_message(chat_id, msg.message_id)
    message_count.clear()


async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Handler order: important!
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_new_restrictions))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("myrank", cmd_myrank))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    sched = AsyncIOScheduler()
    sched.add_job(weekly_report, "cron", day_of_week="mon", hour=0, minute=0, args=[app])
    sched.start()

    await app.run_polling()

if __name__=="__main__":
    asyncio.run(main())
