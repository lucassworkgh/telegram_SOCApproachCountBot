from telegram.ext import Updater, MessageHandler, Filters, CommandHandler, CallbackContext
from telegram.ext import JobQueue
from collections import defaultdict
import datetime
import re
import logging

# === SETUP ===
TOKEN = '7816567657:AAGR0SIbsRu_ShJMzmxvzHl6ZXPKDTZuZUE'
GROUP_CHAT_ID = -1001234567890  # Replace with your group's chat ID
POST_HOUR = 22  # 24-hour format
POST_MINUTE = 0

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === DATA ===
approach_log = defaultdict(list)

# === CORE FUNCTIONS ===
def log_message(update, context):
    user = update.effective_user.first_name
    text = update.message.text.lower()
    count = extract_approaches(text)

    if count:
        timestamp = datetime.datetime.now()
        approach_log[user].append((count, timestamp))
        logger.info(f"{user} logged {count} approaches at {timestamp}")

def extract_approaches(text):
    # Matches: approaches: 3  OR Approaches :   2
    match = re.search(r'approaches\s*:\s*(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else 0

def compute_leaderboard():
    now = datetime.datetime.now()
    week_start = now - datetime.timedelta(days=now.weekday())
    scores = defaultdict(int)

    for user, entries in approach_log.items():
        for count, timestamp in entries:
            if timestamp >= week_start:
                scores[user] += count

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked

def leaderboard_message():
    ranked = compute_leaderboard()
    if not ranked:
        return "No approaches logged this week."

    message = "🏆 *Leaderboard This Week:*\n"
    for i, (user, score) in enumerate(ranked, 1):
        message += f"{i}. {user}: {score} approaches\n"
    return message

def leaderboard_command(update, context):
    msg = leaderboard_message()
    update.message.reply_text(msg, parse_mode='Markdown')

def scheduled_leaderboard(context: CallbackContext):
    msg = leaderboard_message()
    context.bot.send_message(chat_id=GROUP_CHAT_ID, text=msg, parse_mode='Markdown')

# === INIT BOT ===
updater = Updater(TOKEN, use_context=True)
dp = updater.dispatcher

dp.add_handler(MessageHandler(Filters.text & ~Filters.command, log_message))
dp.add_handler(CommandHandler("leaderboard", leaderboard_command))

# === SCHEDULER ===
job_queue: JobQueue = updater.job_queue

# Schedule weekly leaderboard (every Sunday at specified time)
def schedule_weekly_job():
    now = datetime.datetime.now()
    next_sunday = now + datetime.timedelta(days=(6 - now.weekday()))
    scheduled_time = datetime.datetime.combine(
        next_sunday.date(),
        datetime.time(hour=POST_HOUR, minute=POST_MINUTE)
    )
    delay = (scheduled_time - now).total_seconds()

    job_queue.run_repeating(
        scheduled_leaderboard,
        interval=7 * 24 * 3600,  # every 7 days
        first=delay
    )

schedule_weekly_job()

# === START ===
updater.start_polling()
print("Bot is running...")
updater.idle()