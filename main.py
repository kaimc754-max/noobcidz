#!/usr/bin/env python3
import os
import asyncio
import logging
import random
import string
import urllib.parse
import aiohttp
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ====== CONFIG ======
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")  # 🔑 Store token in Replit Secrets
MAILCX_API_BASE = "https://mail.cx/api/api/v1/mailbox/"
DEFAULT_DOMAIN = "nqmo.com"
USERNAME_MIN = 6
USERNAME_MAX = 7
POLL_INTERVAL = 10  # poll every 10 seconds
# ====================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store user email + seen messages
USER_MAILBOXES = {}  # {telegram_id: {"email": ..., "seen": set()}}

# ---------------- WEB SERVER (keep-alive) ----------------
app_web = Flask('')

@app_web.route('/')
def home():
    return "✅ Telegram Temp Mail Bot is running!"

def run():
    app_web.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
# ---------------------------------------------------------


def make_random_username() -> str:
    length = random.choice([USERNAME_MIN, USERNAME_MAX])
    alphabet = string.ascii_lowercase + string.digits
    first = random.choice(string.ascii_lowercase)
    rest = "".join(random.choice(alphabet) for _ in range(length - 1))
    return first + rest


async def fetch_mailcx(session: aiohttp.ClientSession, email: str, message_id: str | None = None):
    """Fetch mailbox or specific message from mail.cx"""
    encoded = urllib.parse.quote(email, safe="")
    url = MAILCX_API_BASE.rstrip("/") + "/" + encoded
    if message_id:
        url += f"/{message_id}"
    try:
        async with session.get(url, timeout=15) as resp:
            text = await resp.text()
            try:
                return await resp.json()
            except Exception:
                return text
    except Exception as e:
        return {"error": str(e)}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome!\nUse /generate to create a temp email.\n"
        f"New emails will be pushed to you automatically every {POLL_INTERVAL} seconds."
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = make_random_username()
    email = f"{username}@{DEFAULT_DOMAIN}"

    USER_MAILBOXES[user_id] = {"email": email, "seen": set()}
    await update.message.reply_text(
        f"✅ Your temp mail: `{email}`\n\nI’ll notify you when new mails arrive (checked every {POLL_INTERVAL}s).",
        parse_mode="Markdown"
    )


async def callback_view_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        email, mid = query.data.split("|", 1)
    except ValueError:
        await query.edit_message_text("❌ Invalid data")
        return

    async with aiohttp.ClientSession() as session:
        data = await fetch_mailcx(session, email, mid)

    if isinstance(data, dict):
        subj = data.get("subject", "(no subject)")
        frm = data.get("from", "unknown")
        body = data.get("body", "(no body)")
        text = f"*Subject:* {subj}\n*From:* {frm}\n\n{body}"
    else:
        text = str(data)

    if len(text) > 3800:
        text = text[:3800] + "\n...truncated..."
    await query.edit_message_text(text, parse_mode="Markdown")


async def poll_inboxes(app):
    """Background task: poll mailboxes and send new mails"""
    async with aiohttp.ClientSession() as session:
        while True:
            for user_id, data in list(USER_MAILBOXES.items()):
                email = data["email"]
                seen = data["seen"]

                mails = await fetch_mailcx(session, email)
                if isinstance(mails, dict) and "messages" in mails:
                    messages = mails["messages"]
                elif isinstance(mails, list):
                    messages = mails
                else:
                    continue

                for msg in messages:
                    mid = msg.get("id")
                    if not mid or mid in seen:
                        continue  # already delivered

                    subj = msg.get("subject", "(no subject)")
                    frm = msg.get("from", "unknown")
                    date = msg.get("date", "")

                    text = f"✉️ *New Mail!*\n\n*Subject:* {subj}\n*From:* {frm}\n*Date:* {date}"
                    keyboard = InlineKeyboardMarkup.from_button(
                        InlineKeyboardButton("📩 View", callback_data=f"{email}|{mid}")
                    )
                    try:
                        await app.bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=keyboard)
                        seen.add(mid)
                    except Exception as e:
                        logger.error(f"Failed to send message to {user_id}: {e}")

            await asyncio.sleep(POLL_INTERVAL)


def main():
    keep_alive()  # ✅ start small webserver so Replit can stay awake

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CallbackQueryHandler(callback_view_message))

    async def on_startup():
        asyncio.create_task(poll_inboxes(app))  # run forever in background

    app.post_init = on_startup

    app.run_polling()


if __name__ == "__main__":
    main()
  
