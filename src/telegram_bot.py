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
WAITING_FOR_DETAILS = 1

# Store pending receipt queues per user (supports multiple receipts)
pending_receipt_queues = {}


def parse_expense_details(text):
    """
    Flexibly parse expense details from user input.
    Supports multiple formats:
    - Multi-line format
    - Single line: "10xDS, Business, Client meeting"
    - Keywords anywhere
    """
    text = text.strip()
    
    company_project = None
    expense_type = None
    notes = None
    
    # Try multi-line format first
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        line_lower = line.lower()
        
        if 'company' in line_lower or 'project' in line_lower:
            # Extract value after colon
            if ':' in line:
                company_project = line.split(':', 1)[1].strip()
            else:
                # Try to extract after the word
                parts = re.split(r'company/project|company|project', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    company_project = parts[1].strip()
        
        elif 'type' in line_lower and 'company' not in line_lower:
            if ':' in line:
                expense_type = line.split(':', 1)[1].strip()
            else:
                parts = re.split(r'type', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    expense_type = parts[1].strip()
        
        elif 'note' in line_lower:
            if ':' in line:
                notes_value = line.split(':', 1)[1].strip()
                notes = None if notes_value.lower() == 'skip' else notes_value
    
    # If multi-line parsing didn't work, try comma-separated format
    if not company_project or not expense_type:
        parts = [p.strip() for p in text.split(',')]
        
        if len(parts) >= 2:
            company_project = parts[0]
            expense_type = parts[1]
            if len(parts) >= 3 and parts[2].lower() != 'skip':
                notes = parts[2]
    
    # If still nothing, try to extract keywords
    if not company_project or not expense_type:
        text_lower = text.lower()
        
        # Try to find expense type keywords
        type_keywords = {
            'business': 'Business',
            'personal': 'Personal',
            'reimbursable': 'Reimbursable',
            'non-reimbursable': 'Non-reimbursable'
        }
        
        for keyword, value in type_keywords.items():
            if keyword in text_lower:
                expense_type = value
                # Remove the type keyword to get company/project
                text_cleaned = re.sub(keyword, '', text_lower, flags=re.IGNORECASE).strip()
                text_cleaned = re.sub(r'[,:]', '', text_cleaned).strip()
                if text_cleaned and not company_project:
                    company_project = text_cleaned
                break
    
    return {
        'company_project': company_project,
        'expense_type': expense_type,
        'notes': notes
    }


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user starts the bot"""
    welcome_message = """
👋 Welcome to ExpenseFlow Bot!

Send me receipt photos, PDFs, or documents and I'll automatically:

✅ Extract expense details using AI
✅ Ask for company/project and expense type
✅ Save to your expense tracker

**How to use:**
1️⃣ Send receipt photo(s)
2️⃣ I'll extract the details
3️⃣ Reply with: Company, Type, Notes

**Example formats (all work):**
• 10xDS, Business, Client meeting
• Company: 10xDS, Type: Business
• Personal, Personal, Shopping
• Skip (to save without details)

Just send a photo to get started!
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
            return ConversationHandler.END

        # Initialize queue if not exists
        if user_id not in pending_receipt_queues:
            pending_receipt_queues[user_id] = []
        
        # Add to queue
        pending_receipt_queues[user_id].append(expense_data)

        # Format extracted info
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')

        queue_position = len(pending_receipt_queues[user_id])

        # Ask for additional details
        details_prompt = f"""
✅ **Receipt {queue_position} Extracted!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
📅 Date: {date}

📋 **Reply with details (flexible format):**

**Quick:** 10xDS, Business, Client meeting
**Or:** Personal, Personal, Shopping
**Or:** Type 'skip' to save without details

(Any format works - I'm smart! 🤖)
"""
        await update.message.reply_text(details_prompt, parse_mode='Markdown')
        
        return WAITING_FOR_DETAILS

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        await update.message.reply_text("❌ Sorry, there was an error processing your receipt. Please try again.")
        return ConversationHandler.END


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
            return ConversationHandler.END

        # Initialize queue if not exists
        if user_id not in pending_receipt_queues:
            pending_receipt_queues[user_id] = []
        
        # Add to queue
        pending_receipt_queues[user_id].append(expense_data)

        # Format extracted info
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')

        queue_position = len(pending_receipt_queues[user_id])

        # Ask for additional details
        details_prompt = f"""
✅ **Receipt {queue_position} Extracted!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
📅 Date: {date}

📋 **Reply with details (flexible format):**

**Quick:** 10xDS, Business, Client meeting
**Or:** Personal, Personal, Shopping
**Or:** Type 'skip' to save without details

(Any format works! 🤖)
"""
        await update.message.reply_text(details_prompt, parse_mode='Markdown')
        
        return WAITING_FOR_DETAILS

    except Exception as e:
        logger.error(f"❌ Error handling document: {e}")
        await update.message.reply_text("❌ Sorry, there was an error processing your document. Please try again.")
        return ConversationHandler.END


async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's response with expense details"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip()

        # Check if user has pending receipts
        if user_id not in pending_receipt_queues or not pending_receipt_queues[user_id]:
            await update.message.reply_text("⚠️ No pending receipts. Please send a new receipt photo.")
            return ConversationHandler.END

        # Get the first receipt from queue
        expense_data = pending_receipt_queues[user_id][0]

        # Check if user wants to skip
        if user_message.lower() == 'skip':
            # Save without additional details
            save_result = firebase_client.save_telegram_receipt(
                expense_data, 
                telegram_user_id=str(user_id)
            )

            if save_result.get('success'):
                merchant = expense_data.get('merchant_name', 'Unknown')
                amount = expense_data.get('total_amount', 0)
                currency = expense_data.get('currency', 'INR')
                
                await update.message.reply_text(
                    f"✅ **Expense Saved!**\n\n🏪 {merchant}\n💰 {currency} {amount}\n\n(No additional details)"
                )
            else:
                await update.message.reply_text("❌ Failed to save expense. Please try again.")

            # Remove from queue
            pending_receipt_queues[user_id].pop(0)
            
            # Check if more receipts in queue
            if pending_receipt_queues[user_id]:
                next_receipt = pending_receipt_queues[user_id][0]
                merchant = next_receipt.get('merchant_name', 'Unknown')
                amount = next_receipt.get('total_amount', 0)
                currency = next_receipt.get('currency', 'INR')
                
                await update.message.reply_text(
                    f"📋 **Next receipt in queue:**\n\n"
                    f"🏪 {merchant}\n💰 {currency} {amount}\n\n"
                    f"Please provide details or type 'skip'"
                )
                return WAITING_FOR_DETAILS
            else:
                # No more receipts
                del pending_receipt_queues[user_id]
                await update.message.reply_text("🎉 All receipts processed! Send more anytime.")
                return ConversationHandler.END

        # Parse user input using flexible parser
        parsed = parse_expense_details(user_message)
        company_project = parsed['company_project']
        expense_type = parsed['expense_type']
        notes = parsed['notes']

        # Validate required fields
        if not company_project or not expense_type:
            await update.message.reply_text(
                "⚠️ Could not understand. Please provide at least:\n"
                "• Company/Project name\n"
                "• Type (Business/Personal/Reimbursable)\n\n"
                "Example: 10xDS, Business, Client meeting"
            )
            return WAITING_FOR_DETAILS

        # Validate expense type
        valid_types = ['business', 'personal', 'reimbursable', 'non-reimbursable']
        if expense_type.lower() not in valid_types:
            await update.message.reply_text(
                f"⚠️ Invalid expense type: '{expense_type}'\n\n"
                f"Valid types: Business, Personal, Reimbursable"
            )
            return WAITING_FOR_DETAILS

        # Add new fields to expense data
                # Add new fields to expense data
        expense_data['company_project'] = company_project
        expense_data['expense_type'] = expense_type.capitalize()
        expense_data['notes'] = notes

        # CHECK FOR DUPLICATES BEFORE SAVING
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        date = expense_data.get('date')
        
        duplicate_check = firebase_client.check_duplicate_receipt(
            merchant=merchant,
            amount=amount,
            date=date,
            telegram_user_id=str(user_id)
        )
        
        if duplicate_check['is_duplicate']:
            existing = duplicate_check['existing_receipt']
            existing_date = existing.get('created_at')
            
            # Format timestamp
            if existing_date:
                from datetime import datetime
                if hasattr(existing_date, 'timestamp'):
                    uploaded_date = datetime.fromtimestamp(existing_date.timestamp()).strftime('%b %d, %Y at %I:%M %p')
                else:
                    uploaded_date = "recently"
            else:
                uploaded_date = "previously"
            
            duplicate_msg = f"""
⚠️ **Duplicate Receipt Detected!**

🏪 Merchant: {merchant}
💰 Amount: {expense_data.get('currency', 'INR')} {amount}
📅 Date: {date}

❌ This receipt was already uploaded on {uploaded_date}

Details of existing receipt:
🏢 Company/Project: {existing.get('company_project', 'N/A')}
💼 Type: {existing.get('expense_type', 'N/A')}
📝 Notes: {existing.get('notes', 'None')}

**Not saving duplicate.** Send a different receipt.
"""
            await update.message.reply_text(duplicate_msg, parse_mode='Markdown')
            logger.info(f"⚠️ Duplicate prevented: {merchant} - {amount}")
            
            # Remove from queue and check for next receipt
            pending_receipt_queues[user_id].pop(0)
            
            if pending_receipt_queues[user_id]:
                next_receipt = pending_receipt_queues[user_id][0]
                merchant_next = next_receipt.get('merchant_name', 'Unknown')
                amount_next = next_receipt.get('total_amount', 0)
                currency_next = next_receipt.get('currency', 'INR')
                
                await update.message.reply_text(
                    f"📋 **Next receipt in queue:**\n\n"
                    f"🏪 {merchant_next}\n💰 {currency_next} {amount_next}\n\n"
                    f"Please provide details"
                )
                return WAITING_FOR_DETAILS
            else:
                del pending_receipt_queues[user_id]
                return ConversationHandler.END
        
        # If not duplicate, save to Firebase
        save_result = firebase_client.save_telegram_receipt(
            expense_data, 
            telegram_user_id=str(user_id)
        )


        if save_result.get('success'):
            merchant = expense_data.get('merchant_name', 'Unknown')
            amount = expense_data.get('total_amount', 0)
            currency = expense_data.get('currency', 'INR')
            
            confirmation_msg = f"""
✅ **Expense Saved!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
🏢 Company/Project: {company_project}
💼 Type: {expense_type.capitalize()}
"""
            if notes:
                confirmation_msg += f"📝 Notes: {notes}"

            await update.message.reply_text(confirmation_msg, parse_mode='Markdown')
            logger.info(f"✅ Saved expense: {merchant} - {currency} {amount} ({company_project}, {expense_type})")
        else:
            await update.message.reply_text("❌ Failed to save expense. Please try again.")

        # Remove from queue
        pending_receipt_queues[user_id].pop(0)
        
        # Check if more receipts in queue
        if pending_receipt_queues[user_id]:
            next_receipt = pending_receipt_queues[user_id][0]
            merchant = next_receipt.get('merchant_name', 'Unknown')
            amount = next_receipt.get('total_amount', 0)
            currency = next_receipt.get('currency', 'INR')
            
            await update.message.reply_text(
                f"📋 **Next receipt in queue:**\n\n"
                f"🏪 {merchant}\n💰 {currency} {amount}\n\n"
                f"Please provide details (any format works!)"
            )
            return WAITING_FOR_DETAILS
        else:
            # No more receipts
            del pending_receipt_queues[user_id]
            await update.message.reply_text("🎉 All receipts processed! Send more anytime.")
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"❌ Error handling details: {e}")
        await update.message.reply_text("❌ Error processing your details. Please try again.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    user_id = update.effective_user.id
    if user_id in pending_receipt_queues:
        count = len(pending_receipt_queues[user_id])
        del pending_receipt_queues[user_id]
        await update.message.reply_text(
            f"❌ Cancelled. {count} pending receipt(s) cleared. Send new receipts to start again."
        )
    else:
        await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
    
    return ConversationHandler.END


@app.route('/')
def health_check():
    """Health check endpoint for Render"""
    return "ExpenseFlow Telegram Bot is running!", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)

        # Schedule update processing in the bot's event loop
        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            bot_loop
        )

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


async def run_bot_loop():
    """Run the bot's event loop"""
    # Keep the loop running forever
    while True:
        await asyncio.sleep(1)


def start_bot_loop():
    """Start bot event loop in background thread"""
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_until_complete(run_bot_loop())


def init_bot():
    """Initialize the bot application"""
    global application

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
        return

    logger.info("🤖 Initializing Telegram bot with webhook...")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Initialize in separate event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())

    # Create conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.PHOTO, handle_photo),
            MessageHandler(filters.Document.ALL, handle_document)
        ],
        states={
            WAITING_FOR_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_details)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_user=True,
        per_chat=True
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    # Setup webhook
    webhook_url = f"{WEBHOOK_URL}/webhook"
    loop.run_until_complete(application.bot.set_webhook(webhook_url))

    logger.info(f"✅ Webhook set to: {webhook_url}")

    # Start bot event loop in background thread
    Thread(target=start_bot_loop, daemon=True).start()

    logger.info("✅ Telegram bot initialized with webhook!")


def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


if __name__ == "__main__":
    init_bot()
    start_flask_server()
