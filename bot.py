from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from collections import defaultdict
import datetime, re, os

# ── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN          = os.getenv("BOT_TOKEN")          # BotFather token
GROUP_CHAT_ID  = os.getenv("GROUP_CHAT_ID")      # -100xxxxxxxxxx
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")        # https://xxx.onrender.com
WEBHOOK_PATH   = "webhook"                       # keep it simple & URL-safe

bot            = Bot(token=TOKEN)
dispatcher     = Dispatcher(bot, None, use_context=True)
approach_log   = defaultdict(list)

# ── BOT LOGIC ────────────────────────────────────────────────────────────────
def extract_approaches(text):
    m = re.search(r'approaches\s*:\s*(\d+)', text, re.I)
    return int(m.group(1)) if m else None

def log_message(update, ctx):
    user  = update.effective_user.first_name
    count = extract_approaches(update.message.text.lower())
    if count:
        approach_log[user].append((count, datetime.datetime.now()))

def leaderboard(update, ctx):
    week_start = datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())
    scores = defaultdict(int)
    for user, entries in approach_log.items():
        for c, ts in entries:
            if ts >= week_start:
                scores[user] += c
    if not scores:
        update.message.reply_text("No approaches logged this week.")
        return
    board = "\n".join(f"{i}. {u}: {s} approaches"
                      for i, (u, s) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), 1))
    update.message.reply_text(f"🏆 *Leaderboard This Week:*\n{board}", parse_mode='Markdown')

dispatcher.add_handler(CommandHandler("leaderboard", leaderboard))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, log_message))

# ── FLASK APP ────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route(f"/{WEBHOOK_PATH}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    full_url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
    bot.set_webhook(url=full_url)
    print(f"Webhook set to: {full_url}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
