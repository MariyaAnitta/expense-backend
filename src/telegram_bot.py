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
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://xpenseflow-telegram-bot.onrender.com")

# Initialize receipt extractor and Firebase client
receipt_extractor = ReceiptExtractor()
firebase_client = FirebaseClient()

# Create Flask app for webhook
app = Flask(__name__)

# Initialize bot application
application = None
bot_loop = None

# Conversation states
WAITING_FOR_CATEGORY = 1
WAITING_FOR_REIMBURSEMENT = 2
WAITING_FOR_PROJECT = 3
WAITING_FOR_NOTES = 4

# Store pending receipt queues per user
# Structure: {user_id: [receipt1, receipt2, ...]}
pending_receipt_queues = {}

# Store conversation state per user
user_states = {}


def format_items(items):
    """Format items list for display"""
    if not items or len(items) == 0:
        return None
    
    formatted = "🛒 Items:\n"
    for item in items:
        name = item.get('name', 'Unknown')
        qty = item.get('quantity', 1)
        amount = item.get('amount', 0)
        formatted += f"• {name} ({qty}x) - INR {amount:.2f}\n"
    
    return formatted.strip()


def format_receipt_extraction(receipt_data, receipt_num=None, total_receipts=None):
    """Format receipt extraction message"""
    merchant = receipt_data.get('merchant_name', 'Unknown')
    amount = receipt_data.get('total_amount', 0)
    currency = receipt_data.get('currency', 'INR')
    date = receipt_data.get('date', 'Unknown')
    category = receipt_data.get('category', 'Other')
    items = receipt_data.get('items', [])
    tax = receipt_data.get('tax_amount')
    
    # Header
    if receipt_num and total_receipts:
        message = f"✅ Receipt {receipt_num}/{total_receipts} Extracted!\n\n"
    else:
        message = "✅ Receipt Extracted!\n\n"
    
    # Basic info
    message += f"🏪 Merchant: {merchant}\n"
    message += f"💰 Amount: {currency} {amount}\n"
    message += f"📅 Date: {date}\n"
    message += f"📦 Category: {category}\n"
    
    # Items (if available)
    items_text = format_items(items)
    if items_text:
        message += f"\n{items_text}\n"
        if tax:
            message += f"Tax: {currency} {tax}\n"
    
    return message


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user starts the bot"""
    welcome_message = """
👋 Welcome to ExpenseFlow Bot!

Send me receipt photos (single or multiple) and I'll:

✅ Extract details using AI
✅ Guide you through categorization
✅ Save to your expense tracker

**Categories:**
📝 Personal - Your personal expenses
💼 Business - Work expenses
  ├─ Reimbursable (you'll be paid back)
  └─ Company expense (no reimbursement)

Just send a photo to get started! 📸
"""
    await update.message.reply_text(welcome_message)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt photos sent by users"""
    try:
        user_id = update.effective_user.id
        logger.info(f"📸 Received photo from user {user_id}")

        # Check if user already has pending receipts being processed
        if user_id in user_states and user_states[user_id].get('processing'):
            await update.message.reply_text(
                "⏳ Please finish categorizing your current receipts first!\n\n"
                "Type /cancel to start over."
            )
            return user_states[user_id].get('state', ConversationHandler.END)

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

        # Store temporarily
        pending_receipt_queues[user_id].append({
            'file_path': file_path,
            'file_id': photo.file_id,
            'extracted': False
        })

        # Don't process yet - wait for more photos or timeout
        # Set a flag that user is uploading
        if user_id not in user_states:
            user_states[user_id] = {}
        
        user_states[user_id]['uploading'] = True
        
        # Start a timer - if no more photos in 2 seconds, start processing
        if 'timer' in user_states[user_id]:
            user_states[user_id]['timer'].cancel()
        
        timer = asyncio.create_task(process_after_delay(update, context, user_id))
        user_states[user_id]['timer'] = timer
        
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"❌ Error handling photo: {e}")
        await update.message.reply_text("❌ Error receiving photo. Please try again.")
        return ConversationHandler.END


async def process_after_delay(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Process receipts after 2 second delay (allows batch upload)"""
    try:
        await asyncio.sleep(2)  # Wait 2 seconds for more uploads
        
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            return
        
        user_states[user_id]['uploading'] = False
        user_states[user_id]['processing'] = True
        
        total_receipts = len(pending_receipt_queues[user_id])
        
        # Show processing message
        if total_receipts > 1:
            await update.message.reply_text(
                f"📸 Received {total_receipts} receipts!\n⏳ Processing..."
            )
        else:
            await update.message.reply_text("⏳ Processing your receipt...")
        
        # Extract all receipts
        for i, receipt_info in enumerate(pending_receipt_queues[user_id]):
            if not receipt_info['extracted']:
                expense_data = receipt_extractor.extract_expense_from_receipt(receipt_info['file_path'])
                
                if expense_data.get('error'):
                    await update.message.reply_text(
                        f"⚠️ Receipt {i+1}: Could not process\n{expense_data['error']}\n\nSkipping..."
                    )
                    pending_receipt_queues[user_id][i]['error'] = True
                    continue
                
                pending_receipt_queues[user_id][i]['expense_data'] = expense_data
                pending_receipt_queues[user_id][i]['extracted'] = True
        
        # Remove failed receipts
        pending_receipt_queues[user_id] = [r for r in pending_receipt_queues[user_id] if not r.get('error')]
        
        if len(pending_receipt_queues[user_id]) == 0:
            await update.message.reply_text("❌ Could not process any receipts. Please try again.")
            del pending_receipt_queues[user_id]
            del user_states[user_id]
            return
        
        # Start processing first receipt
        await ask_category_for_current_receipt(update, context, user_id)
        
    except Exception as e:
        logger.error(f"❌ Error in process_after_delay: {e}")


async def ask_category_for_current_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Ask category for the current receipt in queue"""
    try:
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            return ConversationHandler.END
        
        current_receipt = pending_receipt_queues[user_id][0]
        expense_data = current_receipt['expense_data']
        total_receipts = len(pending_receipt_queues[user_id])
        current_num = user_states[user_id].get('processed_count', 0) + 1
        
        # Format extraction message
        extraction_msg = format_receipt_extraction(
            expense_data, 
            receipt_num=current_num if total_receipts > 1 else None,
            total_receipts=total_receipts if total_receipts > 1 else None
        )
        
        # Add category question
        extraction_msg += "\n\n📋 Is this Personal or Business?\n\n"
        extraction_msg += "Reply:\n• P or Personal\n• B or Business"
        
        await update.message.reply_text(extraction_msg)
        
        user_states[user_id]['state'] = WAITING_FOR_CATEGORY
        return WAITING_FOR_CATEGORY
        
    except Exception as e:
        logger.error(f"❌ Error asking category: {e}")
        return ConversationHandler.END


async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Personal or Business category selection"""
    try:
        user_id = update.effective_user.id
        text = update.message.text.strip().upper()
        
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
            return ConversationHandler.END
        
        # Parse category
        is_personal = text in ['P', 'PERSONAL']
        is_business = text in ['B', 'BUSINESS']
        
        if not is_personal and not is_business:
            await update.message.reply_text(
                "❌ Please reply with 'P' or 'B'\n\n"
                "• P = Personal\n• B = Business"
            )
            return WAITING_FOR_CATEGORY
        
        # Store category
        current_receipt = pending_receipt_queues[user_id][0]
        current_receipt['category'] = 'Personal' if is_personal else 'Business'
        
        if is_personal:
            # Personal expense - save and ask for notes
            expense_data = current_receipt['expense_data']
            merchant = expense_data.get('merchant_name', 'Unknown')
            amount = expense_data.get('total_amount', 0)
            currency = expense_data.get('currency', 'INR')
            date = expense_data.get('date', 'Unknown')
            
            message = f"✅ Expense Saved!\n\n"
            message += f"🏪 Merchant: {merchant}\n"
            message += f"💰 Amount: {currency} {amount}\n"
            message += f"📅 Date: {date}\n"
            message += f"📂 Category: Personal\n"
            message += f"📝 Notes: -\n\n"
            message += "Want to add notes? Reply with text or type 'skip'"
            
            await update.message.reply_text(message)
            user_states[user_id]['state'] = WAITING_FOR_NOTES
            return WAITING_FOR_NOTES
            
        else:
            # Business expense - ask about reimbursement
            message = "💼 Business Expense\n\n"
            message += "Will you be reimbursed for this?\n\n"
            message += "Reply:\n• Y or Yes (Reimbursable)\n• N or No (Company expense)"
            
            await update.message.reply_text(message)
            user_states[user_id]['state'] = WAITING_FOR_REIMBURSEMENT
            return WAITING_FOR_REIMBURSEMENT
            
    except Exception as e:
        logger.error(f"❌ Error handling category: {e}")
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_reimbursement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reimbursement question"""
    try:
        user_id = update.effective_user.id
        text = update.message.text.strip().upper()
        
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
            return ConversationHandler.END
        
        is_yes = text in ['Y', 'YES']
        is_no = text in ['N', 'NO']
        
        if not is_yes and not is_no:
            await update.message.reply_text(
                "❌ Please reply with 'Y' or 'N'\n\n"
                "• Y = Reimbursable\n• N = Company expense"
            )
            return WAITING_FOR_REIMBURSEMENT
        
        # Store reimbursement status
        current_receipt = pending_receipt_queues[user_id][0]
        current_receipt['is_reimbursable'] = is_yes
        
        # Ask for project
        message = "🏢 Which client/project is this for?\n\n"
        message += "Examples:\n• 10xDS\n• Acme Corp\n• Internal\n• Type 'skip' to save without project"
        
        # If there's a last used project, suggest it
        if user_id in user_states and 'last_project' in user_states[user_id]:
            last_project = user_states[user_id]['last_project']
            message = f"🏢 Which client/project is this for?\n\n"
            message += f"Last used: {last_project}\n"
            message += f"• Type '1' for {last_project}\n"
            message += f"• Or type new project name\n"
            message += f"• Type 'skip' for no project"
        
        await update.message.reply_text(message)
        user_states[user_id]['state'] = WAITING_FOR_PROJECT
        return WAITING_FOR_PROJECT
        
    except Exception as e:
        logger.error(f"❌ Error handling reimbursement: {e}")
        return ConversationHandler.END


async def handle_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle project name"""
    try:
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
            return ConversationHandler.END
        
        current_receipt = pending_receipt_queues[user_id][0]
        expense_data = current_receipt['expense_data']
        
        # Handle project input
        project = None
        if text == '1' and user_id in user_states and 'last_project' in user_states[user_id]:
            project = user_states[user_id]['last_project']
        elif text.lower() != 'skip':
            project = text
            user_states[user_id]['last_project'] = project  # Remember for next time
        
        current_receipt['project'] = project
        
        # Show saved confirmation
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')
        is_reimbursable = current_receipt.get('is_reimbursable', False)
        
        message = f"✅ Expense Saved!\n\n"
        message += f"🏪 Merchant: {merchant}\n"
        message += f"💰 Amount: {currency} {amount}\n"
        message += f"📅 Date: {date}\n"
        message += f"📂 Category: Business - {'Reimbursable' if is_reimbursable else 'Company expense'}\n"
        if project:
            message += f"🏢 Project: {project}\n"
        message += f"\nWant to add notes? Reply or type 'skip'"
        
        await update.message.reply_text(message)
        user_states[user_id]['state'] = WAITING_FOR_NOTES
        return WAITING_FOR_NOTES
        
    except Exception as e:
        logger.error(f"❌ Error handling project: {e}")
        return ConversationHandler.END


async def handle_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notes and complete receipt processing"""
    try:
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if user_id not in pending_receipt_queues or len(pending_receipt_queues[user_id]) == 0:
            await update.message.reply_text("❌ No pending receipts. Send a receipt to start!")
            return ConversationHandler.END
        
        current_receipt = pending_receipt_queues[user_id][0]
        expense_data = current_receipt['expense_data']
        
        # Store notes
        notes = None if text.lower() == 'skip' else text
        current_receipt['notes'] = notes
        
        # Update expense data with all collected info
        expense_data['category'] = current_receipt.get('category')
        expense_data['is_reimbursable'] = current_receipt.get('is_reimbursable')
        expense_data['project_name'] = current_receipt.get('project')
        expense_data['notes'] = notes
        
        # Save to Firebase
        save_result = firebase_client.save_telegram_receipt(
            expense_data,
            telegram_user_id=str(user_id)
        )
        
        if not save_result.get('success'):
            await update.message.reply_text("❌ Failed to save. Please try again.")
            return ConversationHandler.END
        
        # Show completion message
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        category = current_receipt.get('category')
        is_reimbursable = current_receipt.get('is_reimbursable')
        project = current_receipt.get('project')
        
        if notes:
            message = f"📝 Notes added!\n\n"
        else:
            message = ""
        
        message += (
    f"✅ "
    f"{'Receipt Complete' if len(pending_receipt_queues[user_id]) == 1 else 'Receipt ' + str(user_states[user_id].get('processed_count', 0) + 1) + ' Complete'}:\n"
)
        message += f"🏪 {merchant}\n"
        message += f"💰 {currency} {amount}\n"
        
        if category == 'Personal':
            message += f"📂 Personal\n"
        else:
            message += f"📂 Business - {'Reimbursable' if is_reimbursable else 'Company expense'}\n"
            if project:
                message += f"🏢 {project}\n"
        
        if notes:
            message += f"📝 {notes}\n"
        
        # Track processed count
        if 'processed_count' not in user_states[user_id]:
            user_states[user_id]['processed_count'] = 0
        user_states[user_id]['processed_count'] += 1
        
        # Remove completed receipt from queue
        pending_receipt_queues[user_id].pop(0)
        
        # Check if more receipts in queue
        if len(pending_receipt_queues[user_id]) > 0:
            message += "\n\n───────────────────────\n"
            await update.message.reply_text(message)
            
            # Process next receipt
            await ask_category_for_current_receipt(update, context, user_id)
            return user_states[user_id].get('state', WAITING_FOR_CATEGORY)
            
        else:
            # All receipts processed - show summary if multiple
            processed_count = user_states[user_id].get('processed_count', 1)
            
            if processed_count > 1:
                message += "\n\n═══════════════════════\n\n"
                message += f"🎉 All {processed_count} receipts processed!\n\n"
                message += "Send more receipts anytime! 📸"
            else:
                message += "\n\nSend more receipts anytime! 📸"
            
            await update.message.reply_text(message)
            
            # Cleanup
            del pending_receipt_queues[user_id]
            user_states[user_id] = {'last_project': user_states[user_id].get('last_project')}
            
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"❌ Error handling notes: {e}")
        await update.message.reply_text("❌ Error saving. Please try again.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    user_id = update.effective_user.id
    
    if user_id in pending_receipt_queues:
        count = len(pending_receipt_queues[user_id])
        del pending_receipt_queues[user_id]
        await update.message.reply_text(
            f"❌ Cancelled. {count} pending receipt(s) cleared.\n\nSend new receipts to start again."
        )
    else:
        await update.message.reply_text("❌ No pending receipts.\n\nSend a receipt to start!")
    
    if user_id in user_states:
        user_states[user_id] = {'last_project': user_states[user_id].get('last_project')}
    
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
        ],
        states={
            WAITING_FOR_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            WAITING_FOR_REIMBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reimbursement)],
            WAITING_FOR_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project)],
            WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)]
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