import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler
from dotenv import load_dotenv
from gemini_receipt_extractor import ReceiptExtractor
from firebase_client import FirebaseClient
from flask import Flask, request, jsonify
import asyncio
from threading import Thread

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
bot_loop = None

# Conversation states
WAITING_FOR_CATEGORY, WAITING_FOR_REIMBURSEMENT, WAITING_FOR_PROJECT, WAITING_FOR_NOTES = range(4)

# Store pending receipt queues per user
pending_receipt_queues = {}
user_expense_data = {}  # Store current expense being processed


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user starts the bot"""
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
    """Handle receipt photos sent by users"""
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

        if expense_data.get('error'):
            await update.message.reply_text(
                f"⚠️ Could not process receipt:\n{expense_data['error']}\n\nPlease try again with a clearer image."
            )
            return ConversationHandler.END

        pending_receipt_queues[user_id].append(expense_data)

        if len(pending_receipt_queues[user_id]) == 1:
            await update.message.reply_text("⏳ Processing your receipt...")
            return await process_next_receipt(update, context, user_id)
        else:
            await update.message.reply_text("📸 Received another receipt! Will process next.")
            return WAITING_FOR_CATEGORY

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        await update.message.reply_text("❌ Error processing receipt.")
        return ConversationHandler.END


async def process_next_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Process next receipt in queue"""
    expense_data = pending_receipt_queues[user_id][0]
    user_expense_data[user_id] = expense_data

    merchant = expense_data.get('merchant_name', 'Unknown')
    amount = expense_data.get('total_amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date', 'Unknown')

    await update.message.reply_text(
        f"""✅ Receipt Extracted!

🏪 Merchant: {merchant.upper()}
💰 Amount: {currency} {amount}
📅 Date: {date}

Is this Personal or Business?

Reply P or B"""
    )

    return WAITING_FOR_CATEGORY


# ✅ NEW: Handle category (P/B)
async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Personal or Business selection"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip().upper()

    if user_id not in user_expense_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = user_expense_data[user_id]

    if user_message in ['P', 'PERSONAL']:
        expense_data['main_category'] = 'Personal'
        expense_data['reimbursement_status'] = 'Not Applicable'
        expense_data['company_project'] = 'Personal'
        expense_data['paid_by'] = 'Employee'

        await update.message.reply_text("📝 Add notes (or reply 'skip'):")
        return WAITING_FOR_NOTES

    elif user_message in ['B', 'BUSINESS']:
        expense_data['main_category'] = 'Business'
        expense_data['paid_by'] = 'Employee'

        await update.message.reply_text(
            "💼 Business Expense\n\n"
            "Will you be reimbursed?\n"
            "Reply: Y (Yes) or N (No)"
        )
        return WAITING_FOR_REIMBURSEMENT

    else:
        await update.message.reply_text("⚠️ Please reply P (Personal) or B (Business)")
        return WAITING_FOR_CATEGORY


# ✅ NEW: Handle reimbursement
async def handle_reimbursement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reimbursement question"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip().upper()

    if user_id not in user_expense_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = user_expense_data[user_id]

    if user_message in ['Y', 'YES']:
        expense_data['reimbursement_status'] = 'Pending'
        await update.message.reply_text("🏢 Which project/company?\n(or reply 'skip')")
        return WAITING_FOR_PROJECT

    elif user_message in ['N', 'NO']:
        expense_data['reimbursement_status'] = 'Not Needed'
        expense_data['company_project'] = 'Company Paid'
        await update.message.reply_text("📝 Add notes (or reply 'skip'):")
        return WAITING_FOR_NOTES

    else:
        await update.message.reply_text("⚠️ Please reply Y (Yes) or N (No)")
        return WAITING_FOR_REIMBURSEMENT


# ✅ NEW: Handle project
async def handle_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle project name"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip()

    if user_id not in user_expense_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = user_expense_data[user_id]

    if user_message.lower() != 'skip':
        expense_data['company_project'] = user_message
    else:
        expense_data['company_project'] = 'Not Specified'

    await update.message.reply_text("📝 Add notes (or reply 'skip'):")
    return WAITING_FOR_NOTES


async def handle_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notes and save"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip()

    if user_id not in user_expense_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = user_expense_data[user_id]

    if user_message.lower() not in ['done', 'skip']:
        expense_data['notes'] = user_message
        notes_text = user_message
    else:
        expense_data['notes'] = None
        notes_text = "-"

    merchant = expense_data.get('merchant_name', 'Unknown')
    amount = expense_data.get('total_amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date')

    # ✅ DUPLICATE CHECK FIRST
    duplicate_check = firebase_client.check_duplicate_receipt(
        merchant=merchant,
        amount=amount,
        date=date,
        telegram_user_id=str(user_id)
    )

    if duplicate_check.get('is_duplicate'):
        await update.message.reply_text(
            f"⚠️ Duplicate Receipt!\n\n"
            f"🏪 {merchant.upper()}\n"
            f"💰 {currency} {amount}\n"
            f"📅 {date}\n\n"
            f"This receipt was already saved."
        )
    else:
        save_result = firebase_client.save_telegram_receipt(
            expense_data,
            telegram_user_id=str(user_id)
        )

        if save_result.get('success'):
            final_msg = f"""✅ Complete!

🏪 {merchant.upper()}
💰 {currency} {amount}
📝 {notes_text}"""
            await update.message.reply_text(final_msg)

    # ✅ FIX: Clean up queue properly
    pending_receipt_queues[user_id].pop(0)
    del user_expense_data[user_id]

    # Check if more receipts exist
    if user_id in pending_receipt_queues and pending_receipt_queues[user_id]:
        return await process_next_receipt(update, context, user_id)
    else:
        if user_id in pending_receipt_queues:
            del pending_receipt_queues[user_id]
        await update.message.reply_text("🎉 All receipts processed!")
        return ConversationHandler.END


# ✅ NEW: Health check route
@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "bot": "ExpenseFlow"}), 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)

        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            bot_loop
        )

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False}), 500


def start_bot_loop():
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_forever()


def init_bot():
    global application

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())

    application.add_handler(CommandHandler("start", start_command))

    # ✅ FIX: Add ALL state handlers
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, handle_photo)],
        states={
            WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
            WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
            WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)]
        },
        fallbacks=[]
    )

    application.add_handler(conv_handler)

    loop.run_until_complete(application.bot.set_webhook(f"{WEBHOOK_URL}/webhook"))

    Thread(target=start_bot_loop, daemon=True).start()


# Auto-initialize bot when module is imported
init_bot()
logger.info("✅ Telegram bot initialized and webhook set")


# For local testing only
if __name__ == "__main__":
    def start_flask_server():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)
    
    start_flask_server()
