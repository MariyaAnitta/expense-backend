import os
import logging
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler , PicklePersistence
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler # Add this to your imports

from dotenv import load_dotenv
from gemini_receipt_extractor import ReceiptExtractor
from firebase_client import FirebaseClient
from flask import Flask, request, jsonify
import asyncio

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Preferred URL from environment or fallback to the new Render URL
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegram-bot-5gu6.onrender.com")

# Initialize receipt extractor and Firebase client
receipt_extractor = ReceiptExtractor()
firebase_client = FirebaseClient()

# Create Flask app for webhook
app = Flask(__name__)

# Conversation states
WAITING_FOR_CATEGORY, WAITING_FOR_REIMBURSEMENT, WAITING_FOR_PROJECT, WAITING_FOR_NOTES, WAITING_FOR_BANK = range(5)

# Global application and loop management
application = None
bot_loop = None
bot_thread = None

async def _build_application():
    """Internal helper to build and start the application"""
    global application
    if application is None:
        persistence = PicklePersistence(filepath="bot_state.pickle")
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .build()
        )
        
        # Add handlers
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.PHOTO, handle_photo),
                MessageHandler(filters.Document.ALL, handle_document)
            ],
            states={
                WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
                WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
                WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
                WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)],
                WAITING_FOR_BANK: [CallbackQueryHandler(handle_bank)]
            },
            fallbacks=[],
            name="receipt_conversation",
            persistent=True
        )
        
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(conv_handler)
        
        await application.initialize()
        await application.start()
        logger.info("✅ Global Telegram Application initialized in background loop")
    
    return application

def get_application():
    """Get the running application instance (sync helper)"""
    global application
    return application

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

        # Use context.user_data for persistence
        if 'pending_queue' not in context.user_data:
            context.user_data['pending_queue'] = []

        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_path = f"temp/receipt_{photo.file_id}.jpg"
        os.makedirs("temp", exist_ok=True)
        await file.download_to_drive(file_path)

        # AI EXTRACTION
        expense_data = receipt_extractor.extract_expense_from_receipt(file_path)

        if expense_data.get('error'):
            await update.message.reply_text(
                f"⚠️ Could not process receipt:\n{expense_data['error']}\n\nPlease try again with a clearer image."
            )
            return ConversationHandler.END

        # Add local path and source for database
        expense_data['source'] = 'telegram'

        context.user_data['pending_queue'].append(expense_data)

        if len(context.user_data['pending_queue']) == 1:
            await update.message.reply_text("⏳ Processing your receipt...")
            return await process_next_receipt(update, context, user_id)
        else:
            await update.message.reply_text("📸 Received another receipt! Will process next.")
            return WAITING_FOR_CATEGORY

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        await update.message.reply_text("❌ Error processing receipt.")
        return ConversationHandler.END

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt documents (PDFs) sent by users"""
    try:
        user_id = update.effective_user.id
        doc = update.message.document
        
        # Filter for PDFs
        if not doc.mime_type or 'pdf' not in doc.mime_type.lower():
            await update.message.reply_text("⚠️ Please send photos or PDF documents only.")
            return ConversationHandler.END

        logger.info(f"📄 Received PDF from user {user_id}: {doc.file_name}")

        # Use context.user_data for persistence
        if 'pending_queue' not in context.user_data:
            context.user_data['pending_queue'] = []

        file = await context.bot.get_file(doc.file_id)
        file_path = f"temp/receipt_{doc.file_id}.pdf"
        os.makedirs("temp", exist_ok=True)
        await file.download_to_drive(file_path)

        # AI EXTRACTION
        expense_data = receipt_extractor.extract_expense_from_receipt(file_path)

        if expense_data.get('error'):
            await update.message.reply_text(
                f"⚠️ Could not process document:\n{expense_data['error']}\n\nPlease try again."
            )
            return ConversationHandler.END

        # Add source for database
        expense_data['source'] = 'telegram'

        context.user_data['pending_queue'].append(expense_data)

        if len(context.user_data['pending_queue']) == 1:
            await update.message.reply_text("⏳ Processing your document...")
            return await process_next_receipt(update, context, user_id)
        else:
            await update.message.reply_text("📄 Received another document! Will process next.")
            return WAITING_FOR_CATEGORY

    except Exception as e:
        logger.error(f"❌ Error handling document: {e}")
        await update.message.reply_text("❌ Error processing document.")
        return ConversationHandler.END

async def process_next_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Process next receipt in queue"""
    if 'pending_queue' not in context.user_data or not context.user_data['pending_queue']:
        return ConversationHandler.END
        
    expense_data = context.user_data['pending_queue'][0]
    context.user_data['active_receipt'] = expense_data # Keep tracking active one

    merchant = expense_data.get('merchant', 'Unknown')
    amount = expense_data.get('amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date', 'Unknown')
    items = expense_data.get('items', [])
    
    items_text = ""
    if items and isinstance(items, list):
        items_text = "\n🛒 Items:\n" + "\n".join([f"• {str(item).title()}" for item in items])

    await update.message.reply_text(
        f"✅ Receipt Extracted!\n\n"
        f"🏪 Merchant: {merchant.upper()}\n"
        f"💰 Amount: {currency} {amount}\n"
        f"📅 Date: {date}"
        f"{items_text}\n\n"
        f"Is this Personal or Business?\n"
        f"Reply P or B"
    )
    return WAITING_FOR_CATEGORY

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Personal or Business selection"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip().upper()

    if 'active_receipt' not in context.user_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = context.user_data['active_receipt']

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

    if 'active_receipt' not in context.user_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = context.user_data['active_receipt']

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

    if 'active_receipt' not in context.user_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END

    expense_data = context.user_data['active_receipt']

    if user_message.lower() != 'skip':
        expense_data['company_project'] = user_message
    else:
        expense_data['company_project'] = 'Not Specified'

    await update.message.reply_text("📝 Add notes (or reply 'skip'):")
    return WAITING_FOR_NOTES

async def handle_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notes and ask for Bank selection"""
    user_id = update.effective_user.id
    user_message = update.message.text.strip()
    
    if 'active_receipt' not in context.user_data:
        await update.message.reply_text("⚠️ No pending receipt. Send a new photo.")
        return ConversationHandler.END
    
    expense_data = context.user_data['active_receipt']
    
    # Save notes
    if user_message.lower() not in ['done', 'skip']:
        expense_data['notes'] = user_message
    else:
        expense_data['notes'] = None

    # CREATE INLINE BUTTONS FOR BANK
    keyboard = [
        [
            InlineKeyboardButton("Amex", callback_data='bank_Amex'),
            InlineKeyboardButton("Citi", callback_data='bank_Citi'),
        ],
        [
            InlineKeyboardButton("HSBC", callback_data='bank_HSBC'),
            InlineKeyboardButton("Cash / Other", callback_data='bank_Cash'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "💳 Which account did you use for this?",
        reply_markup=reply_markup
    )
    return WAITING_FOR_BANK
async def handle_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bank selection and finally save to Firebase"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    bank_name = query.data.split('_')[1] # Extracts 'Amex', 'Citi', etc.
    
    if 'active_receipt' not in context.user_data:
        await query.edit_message_text("⚠️ No pending receipt data found.")
        return ConversationHandler.END
    
    expense_data = context.user_data['active_receipt']
    expense_data['bank'] = bank_name # Add to payload
    
    # FINAL SAVE TO FIREBASE
    merchant = expense_data.get('merchant', 'Unknown')
    amount = expense_data.get('amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date', 'Unknown')
    telegram_user_id = str(user_id)
    
    # Check for duplicates before saving
    dup_check = firebase_client.check_duplicate_receipt(merchant, amount, date, telegram_user_id)
    
    if dup_check.get('is_duplicate'):
        await query.edit_message_text(
            f"⚠️ Duplicate Receipt Detected.\n"
            f"This receipt from {merchant} ({amount}) on {date} is already in the system."
        )
    else:
        save_result = firebase_client.save_telegram_receipt(
            expense_data,
            telegram_user_id=telegram_user_id
        )
        
        if save_result.get('success'):
            await query.edit_message_text(
                f"✅ Complete!\n\n"
                f"🏪 {merchant.upper()}\n"
                f"💰 {currency} {amount}\n"
                f"💳 Bank: {bank_name}\n"
                f"📝 Saved to Forensic Audit pool."
            )
        else:
            await query.edit_message_text("❌ Error saving receipt to ledger.")
    
    # Clean up
    context.user_data['pending_queue'].pop(0)
    if 'active_receipt' in context.user_data:
        del context.user_data['active_receipt']
    
    if context.user_data.get('pending_queue'):
        # Process next if exists
        return await process_next_receipt(query, context, user_id)
    else:
        await context.bot.send_message(chat_id=user_id, text="🎉 All receipts processed!")
        return ConversationHandler.END



@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "bot": "ExpenseFlow"}), 200

# Removed build_application in favor of get_application global


@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook updates"""
    try:
        json_data = request.get_json(force=True)

        async def process():
            app_bot = get_application()
            if app_bot:
                update = Update.de_json(json_data, app_bot.bot)
                await app_bot.process_update(update)
            else:
                logger.error("❌ Application not initialized yet")

        # Process update in the dedicated background loop
        if bot_loop and bot_loop.is_running():
            asyncio.run_coroutine_threadsafe(process(), bot_loop)
        else:
            logger.error("❌ Background loop NOT running")

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return jsonify({"ok": False}), 500

def start_bot_worker():
    """Start the background worker thread with its own loop"""
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    # Initialize application inside this loop
    bot_loop.run_until_complete(_build_application())
    
    # Set webhook
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_loop.run_until_complete(bot.set_webhook(
            url=f"{WEBHOOK_URL}/webhook",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        ))
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}/webhook")
    except Exception as e:
        logger.error(f"❌ Failed to set webhook: {e}")

    # Run loop forever
    logger.info("🤖 Bot worker thread started")
    bot_loop.run_forever()

def init_bot():
    """Initialize bot in a background thread"""
    from threading import Thread
    global bot_thread
    bot_thread = Thread(target=start_bot_worker, daemon=True)
    bot_thread.start()

def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

# Try to set webhook (but don't crash if it fails)
init_bot()

if __name__ == "__main__":
    start_flask_server()