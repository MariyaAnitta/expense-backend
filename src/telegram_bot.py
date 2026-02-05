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
from threading import Thread, Lock
from datetime import datetime

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = "https://xpenseflow-telegram-bot.onrender.com"

# Initialize receipt extractor and Firebase client
receipt_extractor = ReceiptExtractor()
firebase_client = FirebaseClient()

# Create Flask app for webhook
app = Flask(__name__)

# Global bot state
application = None
bot_loop = None
bot_thread = None
init_lock = Lock()

# Conversation states
WAITING_FOR_CATEGORY, WAITING_FOR_REIMBURSEMENT, WAITING_FOR_PROJECT, WAITING_FOR_NOTES = range(4)

# Store pending receipt queues per user
pending_receipt_queues = {}
user_expense_data = {}  # Store current expense being processed


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user starts the bot"""
    logger.info(f"🎯 /start command received from user {update.effective_user.id}")
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
    logger.info("✅ Welcome message sent")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt photos sent by users"""
    try:
        user_id = update.effective_user.id
        logger.info(f"📸 Received photo from user {user_id}")

        # Initialize queue if not exists
        if user_id not in pending_receipt_queues:
            pending_receipt_queues[user_id] = []

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

        # Add to queue
        pending_receipt_queues[user_id].append(expense_data)
        
        queue_length = len(pending_receipt_queues[user_id])
        
        # If first receipt in queue, show processing message
        if queue_length == 1:
            await update.message.reply_text("⏳ Processing your receipt...")
            # Start processing first receipt
            return await process_next_receipt(update, context, user_id)
        else:
            # Multiple receipts detected
            await update.message.reply_text(f"📸 Received receipt {queue_length}! Will process after current one.")
            return WAITING_FOR_CATEGORY

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}", exc_info=True)
        await update.message.reply_text("❌ Sorry, there was an error processing your receipt. Please try again.")
        return ConversationHandler.END


async def process_next_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Process the next receipt in queue"""
    if user_id not in pending_receipt_queues or not pending_receipt_queues[user_id]:
        return ConversationHandler.END
    
    # Get first receipt from queue
    expense_data = pending_receipt_queues[user_id][0]
    user_expense_data[user_id] = expense_data
    
    # Format extracted info
    merchant = expense_data.get('merchant_name', 'Unknown')
    amount = expense_data.get('total_amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date', 'Unknown')
    category = expense_data.get('category', 'Other')
    items = expense_data.get('items', [])
    tax = expense_data.get('tax_amount')
    
    queue_position = len(pending_receipt_queues[user_id])
    receipt_number = f"Receipt {queue_position}/{queue_position}" if queue_position == 1 else f"Receipt 1/{queue_position}"
    
    # Build items list
    items_text = ""
    if items:
        items_text = "\n🛒 Items:\n"
        for item in items[:5]:  # Show max 5 items
            items_text += f"- {item}\n"
    
    tax_text = f"Tax: {currency} {tax}\n" if tax else ""
    
    # Ask for category (Personal or Business)
    details_prompt = f"""✅ {receipt_number} Extracted!

🏪 Merchant: {merchant.upper()}
💰 Amount: {currency} {amount}
📅 Date: {date}
📦 Category: {category}{items_text}{tax_text}
📋 Is this Personal or Business?

Reply: 
- P or Personal
- B or Business"""

    await update.message.reply_text(details_prompt)
    return WAITING_FOR_CATEGORY


async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Personal or Business category"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip().lower()
        logger.info(f"📝 Category response from {user_id}: {user_message}")
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt. Please send a new receipt photo.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        # Parse category
        if user_message in ['p', 'personal']:
            expense_data['main_category'] = 'Personal'
            expense_data['reimbursement_status'] = 'Not Applicable'
            expense_data['company_project'] = 'Personal'
            
            # Save to Firebase
            merchant = expense_data.get('merchant_name', 'Unknown')
            amount = expense_data.get('total_amount', 0)
            currency = expense_data.get('currency', 'INR')
            date = expense_data.get('date', 'Unknown')
            
            confirmation_msg = f"""✅ Expense Saved!

🏪 Merchant: {merchant.upper()}
💰 Amount: {currency} {amount}
📅 Date: {date}
📂 Category: Personal
📝 Notes: -

Want to add notes? Reply with text or type 'done'"""
            
            await update.message.reply_text(confirmation_msg)
            return WAITING_FOR_NOTES
            
        elif user_message in ['b', 'business']:
            expense_data['main_category'] = 'Business'
            
            # Ask for reimbursement
            reimbursement_prompt = """💼 Business Expense

Will you be reimbursed for this?

Reply:
- Yes / Y (Reimbursable)
- No / N (Company expense)"""
            
            await update.message.reply_text(reimbursement_prompt)
            return WAITING_FOR_REIMBURSEMENT
        else:
            await update.message.reply_text("⚠️ Please reply with P (Personal) or B (Business)")
            return WAITING_FOR_CATEGORY
            
    except Exception as e:
        logger.error(f"❌ Error handling category: {e}", exc_info=True)
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_reimbursement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reimbursement question"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip().lower()
        logger.info(f"💼 Reimbursement response from {user_id}: {user_message}")
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt. Please send a new receipt photo.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        if user_message in ['yes', 'y']:
            expense_data['reimbursement_status'] = 'Pending'
            status_text = "Reimbursable"
        elif user_message in ['no', 'n']:
            expense_data['reimbursement_status'] = 'Not Applicable'
            status_text = "Company Expense"
        else:
            await update.message.reply_text("⚠️ Please reply with Yes/Y or No/N")
            return WAITING_FOR_REIMBURSEMENT
        
        # Ask for project
        project_prompt = f"""💼 {status_text}

Which project or department?

Reply with project name or type 'skip'"""
        
        await update.message.reply_text(project_prompt)
        return WAITING_FOR_PROJECT
        
    except Exception as e:
        logger.error(f"❌ Error handling reimbursement: {e}", exc_info=True)
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle project/department question"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        logger.info(f"🏢 Project response from {user_id}: {user_message}")
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt. Please send a new receipt photo.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        if user_message.lower() == 'skip':
            expense_data['company_project'] = 'Not Specified'
        else:
            expense_data['company_project'] = user_message
        
        # Ask for notes
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        reimbursement = expense_data.get('reimbursement_status')
        project = expense_data.get('company_project')
        
        reimbursement_text = "Reimbursable" if reimbursement == 'Pending' else "Company Expense"
        
        notes_prompt = f"""✅ Business Expense Set!

🏪 Merchant: {merchant.upper()}
💰 Amount: {currency} {amount}
📂 Category: Business - {reimbursement_text}
🏢 Project: {project}

Want to add notes? Reply with text or type 'done'"""
        
        await update.message.reply_text(notes_prompt)
        return WAITING_FOR_NOTES
        
    except Exception as e:
        logger.error(f"❌ Error handling project: {e}", exc_info=True)
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notes and finalize expense"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        logger.info(f"📝 Notes response from {user_id}: {user_message[:50]}...")
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt. Please send a new receipt photo.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        # Handle notes
        if user_message.lower() == 'done':
            notes_text = "-"
        else:
            notes_text = user_message
            expense_data['notes'] = notes_text
        
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')
        
        # Check for duplicate
        is_duplicate, existing_date = firebase_client.check_duplicate_receipt(
            telegram_user_id=str(user_id),
            merchant=merchant,
            amount=amount,
            date=date
        )
        
        if is_duplicate:
            # Format date
            uploaded_date = "recently"
            if existing_date and hasattr(existing_date, 'timestamp'):
                uploaded_date = datetime.fromtimestamp(existing_date.timestamp()).strftime('%b %d, %Y at %I:%M %p')
            else:
                uploaded_date = "previously"
            
            duplicate_msg = f"""⚠️ **Duplicate Receipt Detected!**

🏪 Merchant: {merchant}
💰 Amount: {currency} {amount}
📅 Date: {date}

❌ This receipt was already uploaded on {uploaded_date}

**Not saving duplicate.**"""
            
            await update.message.reply_text(duplicate_msg, parse_mode='Markdown')
            logger.info(f"⚠️ Duplicate prevented: {merchant} - {amount}")
        else:
            # Save to Firebase
            save_result = firebase_client.save_telegram_receipt(
                expense_data,
                telegram_user_id=str(user_id)
            )
            
            if save_result.get('success'):
                main_category = expense_data.get('main_category')
                reimbursement = expense_data.get('reimbursement_status')
                project = expense_data.get('company_project', '-')
                
                if main_category == 'Personal':
                    category_text = "Personal"
                else:
                    reimbursement_text = "Reimbursable" if reimbursement == 'Pending' else "Company Expense"
                    category_text = f"Business - {reimbursement_text}"
                
                if notes_text != "-":
                    final_msg = f"""📝 Notes added!

✅ Final expense:
🏪 {merchant.upper()}
💰 {currency} {amount}
📂 {category_text}"""
                    if main_category == 'Business':
                        final_msg += f"\n🏢 {project}"
                    final_msg += f"\n📝 {notes_text}"
                    
                    if reimbursement == 'Pending':
                        final_msg += "\n\nPerfect for reimbursement tracking! 💰"
                else:
                    final_msg = f"""✅ Complete!

🏪 {merchant.upper()}
💰 {currency} {amount}
📂 {category_text}"""
                    if main_category == 'Business':
                        final_msg += f"\n🏢 {project}"
                
                await update.message.reply_text(final_msg)
            else:
                await update.message.reply_text("❌ Failed to save expense. Please try again.")
        
        # Remove from queue and process next
        pending_receipt_queues[user_id].pop(0)
        del user_expense_data[user_id]
        
        # Check if more receipts in queue
        if pending_receipt_queues[user_id]:
            queue_length = len(pending_receipt_queues[user_id])
            await update.message.reply_text(f"\n{'═'*30}\n\n📋 Processing next receipt ({queue_length} remaining)...")
            return await process_next_receipt(update, context, user_id)
        else:
            # All done
            del pending_receipt_queues[user_id]
            await update.message.reply_text("\n🎉 All receipts processed!\n\nSend more receipts anytime! 📸")
            return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Error handling notes: {e}", exc_info=True)
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    user_id = update.effective_user.id
    logger.info(f"❌ Cancel command from {user_id}")
    if user_id in pending_receipt_queues:
        count = len(pending_receipt_queues[user_id])
        del pending_receipt_queues[user_id]
        if user_id in user_expense_data:
            del user_expense_data[user_id]
        await update.message.reply_text(
            f"❌ Cancelled. {count} pending receipt(s) cleared. Send new receipts to start again."
        )
    else:
        await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
    return ConversationHandler.END


# =====================================================================
# FLASK ROUTES
# =====================================================================

@app.route('/')
def health_check():
    """Health check endpoint for Render"""
    return "ExpenseFlow Telegram Bot is running!", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        # Ensure bot is initialized
        if application is None or bot_loop is None:
            logger.error("❌ Bot not initialized!")
            return jsonify({"ok": False, "error": "Bot not initialized"}), 500
        
        # Parse update
        json_data = request.get_json(force=True)
        
        # Log incoming update for debugging
        logger.info(f"📨 Webhook received: {json_data.get('update_id', 'unknown')}")
        
        # Log message type
        if 'message' in json_data:
            msg = json_data['message']
            if 'text' in msg:
                logger.info(f"💬 Text message: {msg['text']}")
            elif 'photo' in msg:
                logger.info(f"📸 Photo message")
        
        update = Update.de_json(json_data, application.bot)
        logger.info(f"🔧 Update object created: {update}")
        logger.info(f"🔧 Application handlers: {len(application.handlers)}")
        
        # Schedule update processing in bot's event loop
        future = asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            bot_loop
        )
        
        # Wait briefly to catch immediate errors (but don't block Telegram)
        try:
            future.result(timeout=0.1)
        except:
            pass  # Ignore timeout - processing continues in background
        
        # Return immediately - Telegram expects fast response
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# =====================================================================
# BOT INITIALIZATION
# =====================================================================

def run_async_loop():
    """Run asyncio event loop in dedicated thread"""
    global bot_loop
    try:
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        logger.info("🔄 Bot event loop started")
        bot_loop.run_forever()
    except Exception as e:
        logger.error(f"❌ Event loop error: {e}", exc_info=True)


def init_bot():
    """Initialize the Telegram bot with webhook"""
    global application, bot_loop, bot_thread
    
    # Thread-safe initialization
    with init_lock:
        # Only initialize once
        if application is not None:
            logger.info("⚠️ Bot already initialized, skipping...")
            return
        
        if not TELEGRAM_BOT_TOKEN:
            logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
            return
        
        logger.info("🤖 Initializing Telegram bot with webhook...")
        
        try:
            # Start dedicated event loop in background thread
            bot_thread = Thread(target=run_async_loop, daemon=True, name="BotEventLoop")
            bot_thread.start()
            
            # Wait for loop to be ready
            import time
            max_wait = 10
            waited = 0
            while bot_loop is None and waited < max_wait:
                time.sleep(0.5)
                waited += 0.5
            
            if bot_loop is None:
                raise RuntimeError("Failed to initialize bot event loop")
            
            logger.info("✅ Event loop ready")
            
            # Build application
            application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            
            # Initialize application in bot's event loop
            future = asyncio.run_coroutine_threadsafe(
                application.initialize(),
                bot_loop
            )
            future.result(timeout=15)
            
            logger.info("✅ Application initialized")
            
            # Create conversation handler
            conv_handler = ConversationHandler(
                entry_points=[
                    MessageHandler(filters.PHOTO, handle_photo),
                ],
                states={
                    WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
                    WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
                    WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
                    WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)],
                },
                fallbacks=[CommandHandler('cancel', cancel)],
                per_user=True,
                per_chat=True
            )
            
            # Add handlers - /start MUST be added
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(conv_handler)
            
            logger.info("✅ Handlers registered")
            
            # Set webhook
            webhook_url = f"{WEBHOOK_URL}/webhook"
            future = asyncio.run_coroutine_threadsafe(
                application.bot.set_webhook(webhook_url),
                bot_loop
            )
            future.result(timeout=15)
            
            logger.info(f"✅ Webhook set to: {webhook_url}")
            logger.info("✅ Telegram bot initialized successfully!")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize bot: {e}", exc_info=True)
            raise


def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


# Initialize bot when module loads (CRITICAL for Gunicorn)
init_bot()


if __name__ == "__main__":
    start_flask_server()