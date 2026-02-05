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
from queue import Queue

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
#bot_loop = None
#update_queue = Queue()

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
        logger.error(f"❌ Error handling photo: {e}")
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
        logger.error(f"❌ Error handling category: {e}")
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_reimbursement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reimbursement question"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip().lower()
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        if user_message in ['y', 'yes']:
            expense_data['reimbursement_status'] = 'Pending'
            expense_data['paid_by'] = 'Employee'
        elif user_message in ['n', 'no']:
            expense_data['reimbursement_status'] = 'Not Needed'
            expense_data['paid_by'] = 'Company'
        else:
            await update.message.reply_text("⚠️ Please reply with Y (Yes) or N (No)")
            return WAITING_FOR_REIMBURSEMENT
        
        # Ask for project
        project_prompt = """🏢 Which client/project is this for?

Examples:
- 10xDS
- Acme Corp
- Internal
- Type 'skip' to save without project"""
        
        await update.message.reply_text(project_prompt)
        return WAITING_FOR_PROJECT
        
    except Exception as e:
        logger.error(f"❌ Error handling reimbursement: {e}")
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle project/client name"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        if user_message.lower() == 'skip':
            expense_data['company_project'] = 'Not Specified'
        else:
            expense_data['company_project'] = user_message
        
        # Show saved confirmation
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        currency = expense_data.get('currency', 'INR')
        date = expense_data.get('date', 'Unknown')
        reimbursement = expense_data.get('reimbursement_status')
        project = expense_data.get('company_project')
        
        reimbursement_text = "Reimbursable" if reimbursement == 'Pending' else "Company Expense"
        
        confirmation_msg = f"""✅ Expense Saved!

🏪 Merchant: {merchant.upper()}
💰 Amount: {currency} {amount}
📅 Date: {date}
📂 Category: Business - {reimbursement_text}
🏢 Project: {project}

Want to add notes? Reply or type 'done'"""
        
        await update.message.reply_text(confirmation_msg)
        return WAITING_FOR_NOTES
        
    except Exception as e:
        logger.error(f"❌ Error handling project: {e}")
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def handle_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notes (final step)"""
    try:
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        
        if user_id not in user_expense_data:
            await update.message.reply_text("⚠️ No pending receipt.")
            return ConversationHandler.END
        
        expense_data = user_expense_data[user_id]
        
        # Add notes (or skip)
        if user_message.lower() not in ['done', 'skip']:
            expense_data['notes'] = user_message
            notes_text = user_message
        else:
            expense_data['notes'] = None
            notes_text = "-"
        
        # CHECK FOR DUPLICATES
        merchant = expense_data.get('merchant_name', 'Unknown')
        amount = expense_data.get('total_amount', 0)
        date = expense_data.get('date')
        currency = expense_data.get('currency', 'INR')
        
        duplicate_check = firebase_client.check_duplicate_receipt(
            merchant=merchant,
            amount=amount,
            date=date,
            telegram_user_id=str(user_id)
        )
        
        if duplicate_check['is_duplicate']:
            existing = duplicate_check['existing_receipt']
            existing_date = existing.get('created_at')
            
            from datetime import datetime
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
🏪 {merchant.UPPER()}
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
        logger.error(f"❌ Error handling notes: {e}")
        await update.message.reply_text("❌ Error processing. Please try again.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    user_id = update.effective_user.id
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
        
        # Create a new event loop for each request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(application.process_update(update))
        finally:
            loop.close()
        
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# async def run_bot_loop():
#     """Run the bot's event loop - keeps it alive"""
#     while True:
#         await asyncio.sleep(3600) 
#
#
#
# def start_bot_loop():
#     """Start bot event loop in background thread"""
#     global bot_loop
#     bot_loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(bot_loop)
#     bot_loop.run_forever()


def init_bot():
    """Initialize the bot application"""
    global application
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
        return
    
    logger.info("🤖 Initializing Telegram bot with webhook...")
    
    # Build application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Initialize bot (run in new event loop)
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
            WAITING_FOR_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notes)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_user=True,
        per_chat=True
    )
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)
    
    webhook_url = f"{WEBHOOK_URL}/webhook"
    loop.run_until_complete(application.bot.set_webhook(webhook_url))
    
    logger.info(f"✅ Webhook set to: {webhook_url}")
    logger.info("✅ Telegram bot initialized with webhook!")




def start_flask_server():
    """Start Flask server for webhook"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


if __name__ == "__main__":
    init_bot()
    start_flask_server()
