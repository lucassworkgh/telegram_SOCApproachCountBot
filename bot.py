import os
import datetime
import re
from collections import defaultdict

import pandas as pd
from flask import Flask, request
from telegram import (Bot, Update, ChatMember, ChatMemberUpdated,
                      ParseMode)
from telegram.ext import (Dispatcher, CommandHandler, MessageHandler,
                          Filters, CallbackContext, ChatMemberHandler,
                          JobQueue)

# ── CONFIG ────────────────────────────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN")                      # BotFather token
WEBHOOK_URL = os.getenv("WEBHOOK_URL")              # e.g. https://xxx.onrender.com
WEBHOOK_PATH = "webhook"                            # safe path
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")          # -100XXXXXXXXXXXX (str or None)
# Admin IDs who can use /set and /reset (integers)
ADMINS = {123456789, 987654321}  # <‑‑‑ replace with real Telegram user IDs

# Excel output directory
DATA_DIR = "leaderboards"

# ── STATE ────────────────────────────────────────────────────────────────
bot = Bot(TOKEN)
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, use_context=True)

# approach_log[user_id] -> list of (count, timestamp)
approach_log = defaultdict(list)

# ── UTILITIES ─────────────────────────────────────────────────────────────

def extract_approaches(text: str):
    """Return int count if pattern 'approaches: N' found, else None."""
    m = re.search(r"approaches\s*:\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None


def full_name(user):
    """Return first+last or username fallback."""
    if user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.full_name  # this already covers username if set


# ── COMMAND HANDLERS ──────────────────────────────────────────────────────

def cmd_leaderboard(update: Update, ctx: CallbackContext):
    board_text = build_leaderboard()
    update.message.reply_text(board_text, parse_mode=ParseMode.MARKDOWN)


def cmd_myrank(update: Update, ctx: CallbackContext):
    board = compute_scores()
    user_id = update.effective_user.id
    rank = next((i for i, (uid, _) in enumerate(board, 1) if uid == user_id), None)
    if rank is None:
        update.message.reply_text("You have no logged approaches this week.")
    else:
        score = dict(board).get(user_id, 0)
        update.message.reply_text(
            f"Your rank this week: *#{rank}* with *{score}* approaches.",
            parse_mode=ParseMode.MARKDOWN)


def cmd_set(update: Update, ctx: CallbackContext):
    if update.effective_user.id not in ADMINS:
        return
    args = ctx.args
    if len(args) != 2:
        update.message.reply_text("Usage: /set <user_id|@username> <number>")
        return
    target, num_str = args
    if not num_str.isdigit():
        update.message.reply_text("Second argument must be a number.")
        return
    count = int(num_str)
    user_id = resolve_user_id(update, target)
    if user_id is None:
        update.message.reply_text("User not found in recent logs.")
        return
    approach_log[user_id] = [(count, datetime.datetime.utcnow())]
    update.message.reply_text(f"Set {target}'s count to {count}.")


def cmd_reset(update: Update, ctx: CallbackContext):
    if update.effective_user.id not in ADMINS:
        return
    approach_log.clear()
    update.message.reply_text("All approach data cleared for current week.")


# ── MESSAGE HANDLER ───────────────────────────────────────────────────────

def log_message(update: Update, ctx: CallbackContext):
    if not update.message or not update.message.text:
        return
    text = update.message.text.lower()
    count = extract_approaches(text)
    if count is None:
        return
    uid = update.effective_user.id
    ts = datetime.datetime.utcnow()
    approach_log[uid].append((count, ts))


# ── CHAT MEMBER HANDLER ───────────────────────────────────────────────────

def intro_new_member(update: Update, ctx: CallbackContext):
    cmu: ChatMemberUpdated = update.chat_member
    if cmu.new_chat_member.user.id == bot.id and cmu.new_chat_member.status in {ChatMember.MEMBER, ChatMember.ADMINISTRATOR}:
        ctx.bot.send_message(
            chat_id=cmu.chat.id,
            text="Hi everyone! I'm the Approach Counter Bot. Log approaches with `approaches: N` and ask me /leaderboard or /myrank at any time.",
            parse_mode=ParseMode.MARKDOWN)


# ── SCHEDULER JOBS ────────────────────────────────────────────────────────

def compute_scores():
    week_start = datetime.datetime.utcnow() - datetime.timedelta(days=datetime.datetime.utcnow().weekday())
    scores = defaultdict(int)
    for uid, entries in approach_log.items():
        for c, ts in entries:
            if ts >= week_start:
                scores[uid] += c
    # list sorted by score desc
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def build_leaderboard():
    board = compute_scores()
    if not board:
        return "No approaches logged this week."
    lines = ["🏆 *Leaderboard This Week:*\n"]
    for i, (uid, score) in enumerate(board, 1):
        name = full_name(bot.get_chat_member(GROUP_CHAT_ID or uid, uid).user)
        lines.append(f"{i}. {name}: {score} approaches")
    return "\n".join(lines)


def weekly_winners(ctx: CallbackContext):
    board = compute_scores()
    if not board:
        return
    top3 = board[:3]
    winners_text = "\n".join(
        f"{i}. {full_name(bot.get_chat_member(GROUP_CHAT_ID or uid, uid).user)} — {score} approaches"
        for i, (uid, score) in enumerate(top3, 1))
    caption = "🎉 *This Week's Winners!* 🎉\n" + winners_text
    if GROUP_CHAT_ID:
        ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
    # save to Excel and then reset log
    save_weekly_excel(board)
    approach_log.clear()


def save_weekly_excel(board):
    if not board:
        return
    year, week, _ = datetime.datetime.utcnow().isocalendar()
    df = pd.DataFrame([
        {"Rank": i, "UserID": uid, "Score": s} for i, (uid, s) in enumerate(board, 1)
    ])
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"leaderboard_{year}_W{week}.xlsx")
    df.to_excel(path, index=False)


# ── HELPER ────────────────────────────────────────────────────────────────

def resolve_user_id(update: Update, target: str):
    if target.startswith("@"):  # username
        username = target[1:].lower()
        for uid in approach_log.keys():
            member = bot.get_chat_member(update.effective_chat.id, uid)
            if member.user.username and member.user.username.lower() == username:
                return uid
    elif target.isdigit():
        return int(target)
    return None


# ── ROUTES & DISPATCHER SETUP ─────────────────────────────────────────────

@app.route(f"/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

# register handlers
dispatcher.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
dispatcher.add_handler(CommandHandler("myrank", cmd_myrank))
dispatcher.add_handler(CommandHandler("set", cmd_set, filters=Filters.chat_type.groups))
dispatcher.add_handler(CommandHandler("reset", cmd_reset, filters=Filters.chat_type.groups))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, log_message))
dispatcher.add_handler(ChatMemberHandler(intro_new_member, ChatMemberHandler.MY_CHAT_MEMBER))

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # set webhook
    bot.set_webhook(f"{WEBHOOK_URL}/{WEBHOOK_PATH}")
    print(f"Webhook set to: {WEBHOOK_URL}/{WEBHOOK_PATH}")

    # schedule weekly job (Sunday 22:00 UTC) via JobQueue
    jq = JobQueue()
    jq.set_dispatcher(dispatcher)
    jq.run_daily(weekly_winners, time=datetime.time(hour=22, minute=0))
    jq.start()

    # start Flask server
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
