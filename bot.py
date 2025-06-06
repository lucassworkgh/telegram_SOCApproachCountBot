from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from collections import defaultdict
import datetime
import re
import os

# === CONFIG ===
TOKEN = os.getenv("7816567657:AAGR0SIbsRu_ShJMzmxvzHl6ZXPKDTZuZUE")
bot = Bot(token=TOKEN)
approach_log = defaultdict(list)
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")  # Use full -100... ID

# === FLASK ===
app = Flask(__name__)

# === TELEGRAM DISPATCHER ===
dispatcher = Dispatcher(bot, None, use_context=True)

# === BOT LOGIC ===

def extract_approaches(text):
    match = re.search(r'approaches\s*:\s*(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else None

def log_message(update, context):
    user = update.effective_user.first_name
    text = update.message.text.lower()
    count = extract_approaches(text)

    if count:
        timestamp = datetime.datetime.now()
        approach_log[user].append((count, timestamp))

def leaderboard(update, context):
    now = datetime.datetime.now()
    week_start = now - datetime.timedelta(days=now.weekday())
    scores = defaultdict(int)

    for user, entries in approach_log.items():
        for count, timestamp in entries:
            if timestamp >= week_start:
                scores[user] += count

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        update.message.reply_text("No approaches logged this week.")
        return

    message = "🏆 *Leaderboard This Week:*\n"
    for i, (user, score) in enumerate(ranked, 1):
        message += f"{i}. {user}: {score} approaches\n"

    update.message.reply_text(message, parse_mode='Markdown')

dispatcher.add_handler(CommandHandler("leaderboard", leaderboard))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, log_message))

# === FLASK ROUTE TO HANDLE UPDATES ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# === ROOT ROUTE (OPTIONAL) ===
@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

# === SET WEBHOOK ON START ===
@app.before_first_request
def set_webhook():
    webhook_url = os.getenv("WEBHOOK_URL")
    bot.set_webhook(url=f"{webhook_url}/{TOKEN}")

# === RUN FLASK APP ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
