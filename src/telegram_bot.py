import os
import logging
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler , PicklePersistence
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

# Conversation states
WAITING_FOR_CATEGORY, WAITING_FOR_REIMBURSEMENT, WAITING_FOR_PROJECT, WAITING_FOR_NOTES = range(4)

# Store pending receipt queues per user
pending_receipt_queues = {}
user_expense_data = {}

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

    pending_receipt_queues[user_id].pop(0)
    del user_expense_data[user_id]

    if user_id in pending_receipt_queues and pending_receipt_queues[user_id]:
        return await process_next_receipt(update, context, user_id)
    else:
        if user_id in pending_receipt_queues:
            del pending_receipt_queues[user_id]
        await update.message.reply_text("🎉 All receipts processed!")

    return ConversationHandler.END

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "bot": "ExpenseFlow"}), 200

def build_application():
    """Build application with handlers"""
    # ✅ Add this line to create the persistence object
    persistence = PicklePersistence(filepath="bot_state.pickle")
    
    app_instance = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)  # ✅ Link persistence here
        .build()
    )
    
    app_instance.add_handler(CommandHandler("start", start_command))
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, handle_photo)],
        states={
            WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
            WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
            WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)]
        },
        fallbacks=[],
        name="receipt_conversation",  # ✅ Ensure this name is set
        persistent=True               # ✅ Set this to True
    )
    
    app_instance.add_handler(conv_handler)
    return app_instance


@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook updates"""
    try:
        json_data = request.get_json(force=True)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            application = build_application()
            loop.run_until_complete(application.initialize())

            update = Update.de_json(json_data, application.bot)
            loop.run_until_complete(application.process_update(update))

            loop.run_until_complete(application.shutdown())
        finally:
            loop.close()

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False}), 500

def init_bot():
    """Set webhook on startup - with retry"""
    max_retries = 3

    for attempt in range(max_retries):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                loop.run_until_complete(bot.set_webhook(
                    f"{WEBHOOK_URL}/webhook",
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30
                ))
                logger.info("✅ Webhook set successfully")
                return
            finally:
                loop.close()

        except Exception as e:
            logger.warning(f"⚠️ Webhook setup attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error("❌ Failed to set webhook after retries. Bot will still work when requests come in.")
                # Don't crash - webhook can be set later by first request

def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

# Try to set webhook (but don't crash if it fails)
init_bot()

if __name__ == "__main__":
    start_flask_server()