from flask import Flask, request
from telegram import Bot, Update, ChatMember
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler, Filters,
    ChatMemberHandler, CallbackContext, JobQueue
)
from collections import defaultdict
import datetime as dt
import threading, pandas as pd, os, re, pathlib

# ── CONFIG ──────────────────────────────────────────────────────────────────
TOKEN          = os.getenv("BOT_TOKEN")               # set in Render env-vars
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")             # e.g. https://xxx.onrender.com
GROUP_CHAT_ID  = int(os.getenv("GROUP_CHAT_ID", "0")) # optional but advised
WEBHOOK_PATH   = "webhook"
ADMINS         = {123456789, 987654321}               # <<< put your numeric IDs here

DATA_DIR       = pathlib.Path("leaderboards")
DATA_DIR.mkdir(exist_ok=True)

# ── GLOBAL STATE ────────────────────────────────────────────────────────────
bot             = Bot(token=TOKEN)
dispatcher      = Dispatcher(bot, None, use_context=True)
job_queue       = JobQueue()
job_queue.set_dispatcher(dispatcher)
name_cache      = {}                                   # user_id -> "Full Name"
approach_log    = defaultdict(list)                    # user_id -> [(count, timestamp)]
greeted_groups  = set()                                # prevent re-greets

# ── HELPERS ─────────────────────────────────────────────────────────────────
def full_name(user):
    return f"{user.first_name} {user.last_name}".strip() if user.last_name else user.first_name

def extract_approaches(text: str):
    m = re.search(r'approaches\s*:\s*(\d+)', text, re.I)
    return int(m.group(1)) if m else None

def build_leaderboard():
    week_start = dt.datetime.utcnow() - dt.timedelta(days=dt.datetime.utcnow().weekday())
    scores = defaultdict(int)
    for uid, entries in approach_log.items():
        scores[uid] += sum(c for c, ts in entries if ts >= week_start)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (uid, score) in enumerate(ranked, 1):
        name = name_cache.get(uid, f"User {uid}")
        lines.append(f"{i}. {name}: {score} approaches")
    return ranked, "\n".join(lines) if lines else "No approaches logged this week."

def save_week_to_excel(ranked):
    if not ranked:
        return
    year, week, _ = dt.datetime.utcnow().isocalendar()
    df = pd.DataFrame([
        {"Rank": i+1, "User ID": uid, "Name": name_cache.get(uid, str(uid)), "Approaches": score}
        for i, (uid, score) in enumerate(ranked)
    ])
    df.to_excel(DATA_DIR / f"leaderboard_{year}_W{week}.xlsx", index=False)

def is_admin(user_id):
    return user_id in ADMINS

# ── COMMANDS ────────────────────────────────────────────────────────────────
def cmd_start(update: Update, ctx: CallbackContext):
    update.message.reply_text(
        "👋 I’m the Approach Counter Bot.\n\n"
        "• Log approaches by sending:  approaches: {number}\n"
        "• /leaderboard   – show this week’s rankings\n"
        "• /myrank        – see your position (DM only)\n\n"
        "Admins:\n"
        "  /set <user_id or @username> <number>\n"
        "  /reset          – clear this week’s data"
    )

def cmd_leaderboard(update: Update, ctx: CallbackContext):
    _, board_text = build_leaderboard()
    update.message.reply_text(board_text, parse_mode='Markdown')

def cmd_myrank(update: Update, ctx: CallbackContext):
    ranked, _ = build_leaderboard()
    uid = update.effective_user.id
    for pos, (u, score) in enumerate(ranked, 1):
        if u == uid:
            update.message.reply_text(f"Your rank: *{pos}* with *{score}* approaches.",
                                      parse_mode='Markdown')
            return
    update.message.reply_text("You have no logged approaches this week.")

def cmd_set(update: Update, ctx: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) != 2 or not ctx.args[1].isdigit():
        update.message.reply_text("Usage: /set <user_id|@username> <number>")
        return
    target, num = ctx.args
    num = int(num)
    uid = None
    if target.startswith("@"):
        try:
            uid = bot.get_chat(target).id
        except Exception:
            update.message.reply_text("User not found.")
            return
    else:
        uid = int(target)
    approach_log[uid] = [(num, dt.datetime.utcnow())]
    name_cache.setdefault(uid, f"User {uid}")
    update.message.reply_text("Count updated.")

def cmd_reset(update: Update, ctx: CallbackContext):
    if is_admin(update.effective_user.id):
        approach_log.clear()
        update.message.reply_text("Weekly data reset.")

# ── HANDLERS ────────────────────────────────────────────────────────────────
def on_message(update: Update, ctx: CallbackContext):
    uid = update.effective_user.id
    name_cache[uid] = full_name(update.effective_user)
    count = extract_approaches(update.message.text)
    if count:
        approach_log[uid].append((count, dt.datetime.utcnow()))
    if count and update.message.chat.type.endswith("group") and not update.message.chat.get_member(bot.id).can_read_all_group_messages:
        # bot can't actually read, probably privacy ON
        for aid in ADMINS:
            bot.send_message(aid,
                "❗️I couldn’t log an approach because I have no message access. "
                "Disable privacy in @BotFather: Settings → Group Privacy → OFF."
            )
        return

def greet_group(update: Update, ctx: CallbackContext):
    chat = update.my_chat_member.chat
    if chat.id not in greeted_groups and update.my_chat_member.new_chat_member.status in {"member", "administrator"}:
        bot.send_message(chat.id,
            "👋 Hi everyone! Log your sets like `approaches: 3` and call /leaderboard anytime.")
        greeted_groups.add(chat.id)

# ── WEEKLY JOB ──────────────────────────────────────────────────────────────
def weekly_job(ctx: CallbackContext):
    ranked, board = build_leaderboard()
    save_week_to_excel(ranked)
    if GROUP_CHAT_ID and ranked:
        winners = "\n".join(board.splitlines()[:3])
        caption = "🏆 *Weekly Winners*\n" + winners
        bot.send_message(GROUP_CHAT_ID, caption, parse_mode='Markdown')
    approach_log.clear()          # reset for the new week

# ── FLASK APP / WEBHOOK ─────────────────────────────────────────────────────
app = Flask(__name__)

@app.route(f"/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

# ── REGISTER HANDLERS ───────────────────────────────────────────────────────
dispatcher.add_handler(CommandHandler("start",      cmd_start))
dispatcher.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
dispatcher.add_handler(CommandHandler("myrank",     cmd_myrank))
dispatcher.add_handler(CommandHandler("set",        cmd_set, Filters.chat_type.groups))
dispatcher.add_handler(CommandHandler("reset",      cmd_reset, Filters.chat_type.groups))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, on_message))
dispatcher.add_handler(ChatMemberHandler(greet_group, ChatMemberHandler.MY_CHAT_MEMBER))
def err_handler(update, ctx):  # logs & pings first admin
    print("ERROR:", ctx.error)
    if ADMINS:
        bot.send_message(next(iter(ADMINS)), f"⚠️ Bot error:\n{ctx.error}")

dispatcher.add_error_handler(err_handler)

# ── SCHEDULER STARTUP ───────────────────────────────────────────────────────
def start_scheduler():
    # Sunday 22:00 UTC
    first_run = dt.datetime.combine(
        dt.datetime.utcnow().date(),
        dt.time(hour=22, minute=0)
    )
    while first_run.weekday() != 6:  # 0=Mon … 6=Sun
        first_run += dt.timedelta(days=1)
    # seconds until first run
    delay = (first_run - dt.datetime.utcnow()).total_seconds()
    job_queue.run_repeating(weekly_job, interval=7*24*3600, first=delay)
    job_queue.start()

# ── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.set_webhook(f"{WEBHOOK_URL}/{WEBHOOK_PATH}")
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
