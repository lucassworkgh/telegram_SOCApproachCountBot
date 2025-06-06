import os
import datetime
import re
from collections import defaultdict

import pandas as pd
from flask import Flask, request
from telegram import Bot, Update, ParseMode, ChatMemberUpdated, ChatMember
from telegram.ext import (Dispatcher, CommandHandler, MessageHandler, Filters,
                          CallbackContext, ChatMemberHandler, JobQueue)
from telegram.error import BadRequest

# ── CONFIG ────────────────────────────────────────────────────────────────
TOKEN         = os.getenv("BOT_TOKEN")                    # BotFather token (required)
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")                  # e.g. https://xxx.onrender.com  (required)
WEBHOOK_PATH  = "webhook"                                 # URL-safe path for Telegram
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")                # -100xxxxxxxxxxxx  (optional)
ADMINS        = {123456789, 987654321}                    # <‑‑ put real admin IDs here
DATA_DIR      = "leaderboards"                            # Excel export folder

# ── STATE ────────────────────────────────────────────────────────────────
bot            = Bot(TOKEN)
app            = Flask(__name__)
dispatcher     = Dispatcher(bot, None, use_context=True)

# approach_log[user_id] -> list[(count, timestamp)]
approach_log   = defaultdict(list)
# name_cache[user_id]  -> str (full name)
name_cache     = {}

# ── UTILITIES ─────────────────────────────────────────────────────────────

def extract_approaches(text: str):
    m = re.search(r"approaches\s*:\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None


def full_name(user):
    if user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.full_name


def cache_name(user):
    name_cache[user.id] = full_name(user)


def name_for(uid: int):
    # 1) cached; 2) try getChat; 3) fallback to str(uid)
    if uid in name_cache:
        return name_cache[uid]
    try:
        chat = bot.get_chat(uid)  # works if user has started bot in DM
        return full_name(chat)
    except BadRequest:
        return str(uid)

# ── COMMANDS ──────────────────────────────────────────────────────────────

def cmd_leaderboard(update: Update, ctx: CallbackContext):
    update.message.reply_text(build_leaderboard(), parse_mode=ParseMode.MARKDOWN)


def cmd_myrank(update: Update, ctx: CallbackContext):
    board = compute_scores()
    uid = update.effective_user.id
    rank = next((i for i, (u, _) in enumerate(board, 1) if u == uid), None)
    if rank is None:
        update.message.reply_text("You have no logged approaches this week.")
    else:
        score = dict(board)[uid]
        update.message.reply_text(
            f"Your rank this week: *#{rank}* with *{score}* approaches.",
            parse_mode=ParseMode.MARKDOWN)


def cmd_set(update: Update, ctx: CallbackContext):
    if update.effective_user.id not in ADMINS:
        return
    if len(ctx.args) != 2 or not ctx.args[1].isdigit():
        update.message.reply_text("Usage: /set <user_id|@username> <number>")
        return
    target, num = ctx.args[0], int(ctx.args[1])
    uid = resolve_user_id(target, update)
    if uid is None:
        update.message.reply_text("User not found.")
        return
    approach_log[uid] = [(num, datetime.datetime.utcnow())]
    update.message.reply_text(f"Set {name_for(uid)}'s count to {num}.")


def cmd_reset(update: Update, ctx: CallbackContext):
    if update.effective_user.id not in ADMINS:
        return
    approach_log.clear()
    update.message.reply_text("Weekly data cleared.")

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────

def log_message(update: Update, ctx: CallbackContext):
    if not update.message or not update.message.text:
        return
    count = extract_approaches(update.message.text.lower())
    if count is None:
        return
    uid = update.effective_user.id
    cache_name(update.effective_user)
    approach_log[uid].append((count, datetime.datetime.utcnow()))

# ── INTRO HANDLER ─────────────────────────────────────────────────────────

def intro(update: Update, ctx: CallbackContext):
    cmu: ChatMemberUpdated = update.chat_member
    if cmu.new_chat_member.user.id == bot.id and cmu.new_chat_member.status in {ChatMember.MEMBER, ChatMember.ADMINISTRATOR}:
        ctx.bot.send_message(cmu.chat.id,
            "Hi everyone! Log approaches with `approaches: N`. Ask me /leaderboard in group or /myrank in DM.",
            parse_mode=ParseMode.MARKDOWN)

# ── SCHEDULER & BOARD ─────────────────────────────────────────────────────

def compute_scores():
    week_start = datetime.datetime.utcnow() - datetime.timedelta(days=datetime.datetime.utcnow().weekday())
    scores = defaultdict(int)
    for uid, entries in approach_log.items():
        for c, ts in entries:
            if ts >= week_start:
                scores[uid] += c
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def build_leaderboard():
    board = compute_scores()
    if not board:
        return "No approaches logged this week."
    lines = ["🏆 *Leaderboard This Week:*\n"]
    for i, (uid, score) in enumerate(board, 1):
        lines.append(f"{i}. {name_for(uid)}: {score} approaches")
    return "\n".join(lines)


def save_week(board):
    if not board:
        return
    year, week, _ = datetime.datetime.utcnow().isocalendar()
    os.makedirs(DATA_DIR, exist_ok=True)
    pd.DataFrame([
        {"Rank": i, "UserID": uid, "Name": name_for(uid), "Score": s}
        for i, (uid, s) in enumerate(board, 1)
    ]).to_excel(os.path.join(DATA_DIR, f"leaderboard_{year}_W{week}.xlsx"), index=False)


def weekly_job(ctx: CallbackContext):
    board = compute_scores()
    if not board:
        return
    top3 = board[:3]
    winners = "\n".join(f"{i}. {name_for(uid)} — {s}" for i, (uid, s) in enumerate(top3, 1))
    msg = "🎉 *This Week's Winners!* 🎉\n" + winners
    if GROUP_CHAT_ID:
        ctx.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)
    save_week(board)
    approach_log.clear()

# ── HELPERS ───────────────────────────────────────────────────────────────

def resolve_user_id(target: str, update: Update):
    if target.startswith("@"):
        uname = target[1:].lower()
        for uid in name_cache:
            if update.effective_chat.get_member(uid).user.username and update.effective_chat.get_member(uid).user.username.lower() == uname:
                return uid
    elif target.isdigit():
        return int(target)
    return None

# ── FLASK ROUTES ──────────────────────────────────────────────────────────

@app.route(f"/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

# ── REGISTER HANDLERS ─────────────────────────────────────────────────────

dispatcher.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
dispatcher.add_handler(CommandHandler("myrank", cmd_myrank))
dispatcher.add_handler(CommandHandler("set", cmd_set))
dispatcher.add_handler(CommandHandler("reset", cmd_reset))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, log_message))
dispatcher.add_handler(ChatMemberHandler(intro, ChatMemberHandler.MY_CHAT_MEMBER))

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.set_webhook(f"{WEBHOOK_URL}/{WEBHOOK_PATH}")
    print("Webhook set to:", f"{WEBHOOK_URL}/{WEBHOOK_PATH}")

    # scheduler
    jq = JobQueue()
    jq.set_dispatcher(dispatcher)
    jq.run_daily(weekly_job, time=datetime.time(hour=22, minute=0))  # Sunday 22:00 UTC
    jq.start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
