import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user starts the bot"""
    welcome_message = """
👋 Welcome to ExpenseFlow Bot!

Send me receipt photos, PDFs, or documents and I'll automatically:
✅ Extract expense details using AI
✅ Identify merchant, amount, date, items
✅ Save to your expense tracker
✅ Send you instant confirmation

Just send a photo or file to get started!
    """
    await update.message.reply_text(welcome_message)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt photos sent by users"""
    try:
        user_id = update.effective_user.id
        logger.info(f"📸 Received photo from user {user_id}")
        
        # Send processing message
        await update.message.reply_text("⏳ Processing your receipt...")
        
        # Get the highest resolution photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Download to temp folder
        file_path = f"temp/receipt_{photo.file_id}.jpg"
        os.makedirs("temp", exist_ok=True)
        await file.download_to_drive(file_path)
        
        logger.info(f"✅ Downloaded photo to {file_path}")
        
        # Extract expense data using Gemini
        expense_data = receipt_extractor.extract_expense_from_receipt(file_path)
        
        # Check if extraction was successful
        if expense_data.get('error'):
            await update.message.reply_text(
                f"⚠️ Could not process receipt:\n{expense_data['error']}\n\nPlease try again with a clearer image."
            )
            return
        
        # Save to Firebase
        save_result = firebase_client.save_telegram_receipt(expense_data, telegram_user_id=str(user_id))
        
        if not save_result.get('success'):
            logger.error(f"❌ Failed to save to Firebase: {save_result.get('error')}")
        
        # Format confirmation message
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')
        category = expense_data.get('category', 'Other')
        items = expense_data.get('items', [])
        
        confirmation_message = f"""
✅ **Expense Saved!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
📅 Date: {date}
📂 Category: {category}
"""
        
        if items:
            items_text = "\n".join([f"  • {item}" for item in items[:5]])
            confirmation_message += f"\n🛒 Items:\n{items_text}"
        
        await update.message.reply_text(confirmation_message, parse_mode='Markdown')
        
        logger.info(f"✅ Processed expense: {merchant} - {currency} {amount}")
        
    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        await update.message.reply_text("❌ Sorry, there was an error processing your receipt. Please try again.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PDF or document receipts sent by users"""
    try:
        user_id = update.effective_user.id
        logger.info(f"📄 Received document from user {user_id}")
        
        # Send processing message
        await update.message.reply_text("⏳ Processing your document...")
        
        document = update.message.document
        file = await context.bot.get_file(document.file_id)
        
        # Download to temp folder
        file_extension = document.file_name.split('.')[-1]
        file_path = f"temp/receipt_{document.file_id}.{file_extension}"
        os.makedirs("temp", exist_ok=True)
        await file.download_to_drive(file_path)
        
        logger.info(f"✅ Downloaded document to {file_path}")
        
        # Extract expense data using Gemini
        expense_data = receipt_extractor.extract_expense_from_receipt(file_path)
        
        # Check if extraction was successful
        if expense_data.get('error'):
            await update.message.reply_text(
                f"⚠️ Could not process document:\n{expense_data['error']}\n\nPlease try again."
            )
            return
        
        # Save to Firebase
        save_result = firebase_client.save_telegram_receipt(expense_data, telegram_user_id=str(user_id))
        
        if not save_result.get('success'):
            logger.error(f"❌ Failed to save to Firebase: {save_result.get('error')}")
        
        # Format confirmation message
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')
        category = expense_data.get('category', 'Other')
        
        confirmation_message = f"""
✅ **Expense Saved!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
📅 Date: {date}
📂 Category: {category}
"""
        
        await update.message.reply_text(confirmation_message, parse_mode='Markdown')
        
        logger.info(f"✅ Processed expense: {merchant} - {currency} {amount}")
        
    except Exception as e:
        logger.error(f"❌ Error handling document: {e}")
        await update.message.reply_text("❌ Sorry, there was an error processing your document. Please try again.")

@app.route('/')
def health_check():
    """Health check endpoint for Render"""
    return "ExpenseFlow Telegram Bot is running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        
        # Process update in async context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

def init_bot():
    """Initialize the bot application"""
    global application
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
        return
    
    logger.info("🤖 Initializing Telegram bot with webhook...")
    
    # Create and initialize application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Initialize the application (IMPORTANT!)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Setup webhook
    webhook_url = f"{WEBHOOK_URL}/webhook"
    loop.run_until_complete(application.bot.set_webhook(webhook_url))
    logger.info(f"✅ Webhook set to: {webhook_url}")
    
    logger.info("✅ Telegram bot initialized with webhook!")

def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    init_bot()
    start_flask_server()
