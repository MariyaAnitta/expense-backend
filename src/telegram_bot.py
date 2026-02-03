import os
import logging
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler
from dotenv import load_dotenv
from gemini_receipt_extractor import ReceiptExtractor
from firebase_client import FirebaseClient
from flask import Flask, request, jsonify
import asyncio

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = "https://xpenseflow-telegram-bot.onrender.com"

# Initialize receipt extractor and Firebase client
receipt_extractor = ReceiptExtractor()
firebase_client = FirebaseClient()

# Create Flask app for webhook
app = Flask(__name__)

# Initialize bot application
application = None

# Conversation states
WAITING_FOR_CATEGORY, WAITING_FOR_REIMBURSEMENT, WAITING_FOR_PROJECT, WAITING_FOR_NOTES = range(4)

# Store pending receipt queues per user
pending_receipt_queues = {}
user_expense_data = {}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """
👋 Welcome to ExpenseFlow Bot!

Send me receipt photos or documents 
How to use:
1️⃣ Send receipt photo(s)
2️⃣ Reply: P (Personal) or B (Business)
3️⃣ For Business: Answer reimbursement & project questions
4️⃣ Add notes or skip

You can send multiple receipts at once!

Just send a photo to get started! 📸
"""
    await update.message.reply_text(welcome_message)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        logger.info(f"📸 Received photo from user {user_id}")

        if user_id not in pending_receipt_queues:
            pending_receipt_queues[user_id] = []

        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        file_path = f"temp/receipt_{photo.file_id}.jpg"
        os.makedirs("temp", exist_ok=True)
        await file.download_to_drive(file_path)

        expense_data = receipt_extractor.extract_expense_from_receipt(file_path)

        if expense_data.get("error"):
            await update.message.reply_text(
                f"⚠️ Could not process receipt:\n{expense_data['error']}"
            )
            return ConversationHandler.END

        pending_receipt_queues[user_id].append(expense_data)

        if len(pending_receipt_queues[user_id]) == 1:
            return await process_next_receipt(update, context, user_id)
        else:
            await update.message.reply_text("📸 Receipt added to queue.")
            return WAITING_FOR_CATEGORY

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        return ConversationHandler.END


async def process_next_receipt(update, context, user_id):
    if not pending_receipt_queues.get(user_id):
        return ConversationHandler.END

    expense_data = pending_receipt_queues[user_id][0]
    user_expense_data[user_id] = expense_data

    merchant = expense_data.get("merchant_name", "Unknown")
    amount = expense_data.get("total_amount", 0)
    currency = expense_data.get("currency", "INR")
    date = expense_data.get("date", "Unknown")

    await update.message.reply_text(
        f"""✅ Receipt Extracted!

🏪 {merchant}
💰 {currency} {amount}
📅 {date}

Is this Personal or Business?
Reply with P or B"""
    )

    return WAITING_FOR_CATEGORY


async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message.text.lower().strip()

    expense_data = user_expense_data.get(user_id)
    if not expense_data:
        return ConversationHandler.END

    if msg in ["p", "personal"]:
        expense_data["main_category"] = "Personal"
        await update.message.reply_text("Saved as Personal. Add notes or type done.")
        return WAITING_FOR_NOTES

    if msg in ["b", "business"]:
        expense_data["main_category"] = "Business"
        await update.message.reply_text("Will you be reimbursed? Yes / No")
        return WAITING_FOR_REIMBURSEMENT

    await update.message.reply_text("Reply with P or B")
    return WAITING_FOR_CATEGORY


async def handle_reimbursement(update, context):
    user_id = update.effective_user.id
    msg = update.message.text.lower().strip()

    expense_data = user_expense_data.get(user_id)
    if not expense_data:
        return ConversationHandler.END

    expense_data["reimbursement_status"] = "Pending" if msg in ["yes", "y"] else "Not Needed"

    await update.message.reply_text("Which project? Or type skip.")
    return WAITING_FOR_PROJECT


async def handle_project(update, context):
    user_id = update.effective_user.id
    expense_data = user_expense_data.get(user_id)

    expense_data["company_project"] = (
        update.message.text if update.message.text.lower() != "skip" else "Not Specified"
    )

    await update.message.reply_text("Add notes or type done.")
    return WAITING_FOR_NOTES


async def handle_notes(update, context):
    user_id = update.effective_user.id
    expense_data = user_expense_data.get(user_id)

    expense_data["notes"] = (
        update.message.text if update.message.text.lower() not in ["done", "skip"] else None
    )

    firebase_client.save_telegram_receipt(expense_data, telegram_user_id=str(user_id))

    pending_receipt_queues[user_id].pop(0)
    user_expense_data.pop(user_id, None)

    if pending_receipt_queues[user_id]:
        return await process_next_receipt(update, context, user_id)

    pending_receipt_queues.pop(user_id, None)
    await update.message.reply_text("🎉 All receipts processed!")
    return ConversationHandler.END


async def cancel(update, context):
    user_id = update.effective_user.id
    pending_receipt_queues.pop(user_id, None)
    user_expense_data.pop(user_id, None)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


@app.route("/")
def health_check():
    return "ExpenseFlow Telegram Bot is running!", 200


# ==============================
# ✅ QUICK FIX (ONLY CHANGE)
# ==============================

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)

        asyncio.run(application.process_update(update))

        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


async def run_bot_loop():
    """Keep bot application alive"""
    while True:
        await asyncio.sleep(3600)


def init_bot():
    global application

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, handle_photo)],
        states={
            WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
            WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
            WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    webhook_url = f"{WEBHOOK_URL}/webhook"
    loop.run_until_complete(application.bot.set_webhook(webhook_url))

    logger.info(f"✅ Webhook set to: {webhook_url}")


def start_flask_server():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    init_bot()
    start_flask_server()
