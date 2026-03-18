import time
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import setup_logger
from threading import Thread
from flask import Flask, request, jsonify, make_response

load_dotenv()
logger = logging.getLogger('ExpenseMonitor')

# ================================
# OLD SINGLE ACCOUNT IMPORTS (DISABLED)
# ================================
# from gmail_auth import get_gmail_service
# from gmail_monitor import GmailMonitor

# ================================
# NEW DUAL ACCOUNT IMPORTS (ACTIVE)
# ================================
from gmail_auth_dual import (
    get_gmail_service_personal,
    get_gmail_service_receipts
)
from gmail_monitor import GmailMonitor, ReceiptEmailMonitor

import requests

from gemini_extractor import TransactionExtractor
from gemini_receipt_extractor import ReceiptExtractor
from firebase_client import FirebaseClient


# === WHATSAPP CREDENTIALS ===
WA_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WA_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WA_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "shared_finance_pool_verify_token")

# Flask app initialized here
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Expense Monitor is running", 200

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

@app.route('/api/reconciliations', methods=['POST'])
def save_reconciliation():
    """Save reconciliation report"""
    try:
        data = request.get_json()
        firebase_client = FirebaseClient()
        doc_id = firebase_client.save_reconciliation_report(data)
        if doc_id:
            return jsonify({"success": True, "id": doc_id}), 200
        return jsonify({"success": False, "error": "Failed to save"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/reconciliations', methods=['GET'])
def get_reconciliations():
    """Get saved reconciliation reports"""
    try:
        year = request.args.get('year', type=int)
        firebase_client = FirebaseClient()
        reports = firebase_client.get_reconciliation_reports(year)
        return jsonify({"success": True, "reports": reports}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==========================================
# WHATSAPP WEBHOOK ENDPOINTS & CONVERSATION
# ==========================================

# Simple memory storage for WhatsApp conversation state
wa_pending_queues = {}
wa_user_data = {}

def send_whatsapp_message(to, text):
    """Send a simple text message via WhatsApp"""
    url = f"https://graph.facebook.com/{WA_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        logger.error(f"WhatsApp text send error: {e}")

def send_whatsapp_interactive_buttons(to, body_text, buttons):
    """Send interactive buttons via WhatsApp"""
    url = f"https://graph.facebook.com/{WA_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    
    # Format buttons for API (max 3 allowed per message by Meta)
    # We will chunk them if > 3, but for now we have 4 bank options, which requires a List message ideally.
    # Alternatively, we can use 3 categories. Meta only allows 3 buttons in an interactive message.
    # We will send 3 buttons: Amex, Citi, Other.
    formatted_buttons = []
    for btn_id, btn_title in buttons[:3]:
        formatted_buttons.append({
            "type": "reply",
            "reply": {
                "id": btn_id,
                "title": btn_title[:20] # Max 20 chars
            }
        })

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": formatted_buttons}
        }
    }
    
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        logger.error(f"WhatsApp interactive send error: {e}")

def download_whatsapp_media(media_id, extension="jpg"):
    """Download media from WhatsApp using media ID"""
    url = f"https://graph.facebook.com/{WA_VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
    try:
        res = requests.get(url, headers=headers).json()
        media_url = res.get("url")
        if not media_url: return None
            
        res = requests.get(media_url, headers=headers)
        file_path = f"temp/whatsapp_{media_id}.{extension}"
        os.makedirs("temp", exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(res.content)
        return file_path
    except Exception as e:
        logger.error(f"WhatsApp media download error: {e}")
        return None

@app.route("/whatsapp/webhook", methods=["GET"])
def verify_whatsapp_webhook():
    """Verify webhook with Meta"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    logger.info(f"🔍 Received verification request: mode={mode}, token={token}")
    
    if mode == "subscribe" and token == WA_VERIFY_TOKEN:
        logger.info(f"✅ WhatsApp Webhook Verified successfully with challenge: {challenge}")
        return make_response(str(challenge), 200)
    
    logger.error(f"❌ WhatsApp Verification FAILED: expected {WA_VERIFY_TOKEN}, got {token}")
    return "Verification failed", 403

def wa_process_next_receipt(sender_id):
    """Start conversation for the next receipt in queue"""
    if sender_id not in wa_pending_queues or not wa_pending_queues[sender_id]:
        return
        
    expense_data = wa_pending_queues[sender_id][0]
    wa_user_data[sender_id] = {'expense': expense_data, 'state': 'WAITING_FOR_CATEGORY'}

    merchant = expense_data.get('merchant', 'Unknown')
    amount = expense_data.get('amount', 0)
    currency = expense_data.get('currency', 'INR')
    date = expense_data.get('date', 'Unknown')
    items = expense_data.get('items', [])
    
    items_text = ""
    if items and isinstance(items, list):
        items_text = "\n🛒 Items:\n" + "\n".join([f"• {str(item).title()}" for item in items])

    text = f"✅ Receipt Extracted!\n\n🏪 Merchant: {merchant.upper()}\n💰 Amount: {currency} {amount}\n📅 Date: {date}{items_text}\n\nIs this Personal or Business?"
    buttons = [
        ('cat_personal', 'Personal'),
        ('cat_business', 'Business')
    ]
    send_whatsapp_interactive_buttons(sender_id, text, buttons)

@app.route("/whatsapp/webhook", methods=["POST"])
def handle_whatsapp_webhook():
    """Handle incoming messages and images from WhatsApp"""
    data = request.get_json()
    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        # 🔒 FILTER: Only process messages sent to OUR specific phone number
        # Under a WABA, multiple numbers can exist. This prevents cross-number message handling.
        incoming_phone_id = value.get("metadata", {}).get("phone_number_id")
        if incoming_phone_id and incoming_phone_id != WA_PHONE_NUMBER_ID:
            logger.info(f"⏭️ Ignoring message for different number: {incoming_phone_id}")
            return jsonify({"status": "ignored"}), 200

        message = value.get("messages", [{}])[0]
        if not message: return jsonify({"status": "no message"}), 200
            
        sender_id = message.get("from")
        msg_type = message.get("type")
        
        if msg_type == "interactive":
            btn_id = message["interactive"]["button_reply"]["id"]
            user_session = wa_user_data.get(sender_id)
            if user_session:
                state = user_session['state']
                expense = user_session['expense']

                if btn_id.startswith('cat_'):
                    category = btn_id.replace('cat_', '').title()
                    expense['main_category'] = category
                    if category == 'Personal':
                        expense['reimbursement_status'] = 'Not Applicable'
                        expense['company_project'] = 'Personal'
                        expense['paid_by'] = 'Employee'
                        user_session['state'] = 'WAITING_FOR_NOTES_CHOICE'
                        send_whatsapp_interactive_buttons(sender_id, "📝 Would you like to add notes?", [('note_add', 'Add Note'), ('note_skip', 'Skip')])
                    else:
                        expense['paid_by'] = 'Employee'
                        user_session['state'] = 'WAITING_FOR_REIMBURSEMENT'
                        send_whatsapp_interactive_buttons(sender_id, "💼 Business Expense\n\nWill you be reimbursed?", [('reimb_yes', 'Yes'), ('reimb_no', 'No')])

                elif btn_id.startswith('reimb_'):
                    status = 'Pending' if btn_id == 'reimb_yes' else 'Not Needed'
                    expense['reimbursement_status'] = status
                    if status == 'Pending':
                        user_session['state'] = 'WAITING_FOR_PROJECT'
                        send_whatsapp_message(sender_id, "🏢 Which project/company?\n(or reply 'skip')")
                    else:
                        expense['company_project'] = 'Company Paid'
                        user_session['state'] = 'WAITING_FOR_NOTES_CHOICE'
                        send_whatsapp_interactive_buttons(sender_id, "📝 Would you like to add notes?", [('note_add', 'Add Note'), ('note_skip', 'Skip')])

                elif btn_id == 'note_add':
                    user_session['state'] = 'WAITING_FOR_NOTES_TEXT'
                    send_whatsapp_message(sender_id, "📝 Please type your notes:")

                elif btn_id == 'note_skip':
                    expense['notes'] = None
                    user_session['state'] = 'WAITING_FOR_BANK'
                    buttons = [('bank_Amex', 'Amex'), ('bank_Citi', 'Citi'), ('bank_Other', 'Cash / Other')]
                    send_whatsapp_interactive_buttons(sender_id, "💳 Which account did you use for this?", buttons)

                elif state == 'WAITING_FOR_BANK':
                    bank_name = btn_id.replace('bank_', '')
                    expense['bank'] = bank_name
                    expense['source'] = 'whatsapp'
                    expense['user_id'] = 'SHARED_POOL'
                    
                    # Check duplicates right before saving
                    merchant = expense.get('merchant', 'Unknown')
                    amount = expense.get('amount', 0)
                    date = expense.get('date')
                    telegram_id = f"wa_{sender_id}" # Maps to telegram_user_id in DB
                    
                    db = FirebaseClient()
                    dup_check = db.check_duplicate_receipt(merchant, amount, date, telegram_id)
                    
                    if dup_check.get('is_duplicate'):
                        send_whatsapp_message(sender_id, f"⚠️ Duplicate Receipt Detected.\nThis receipt from {merchant} ({amount}) on {date} is already in the system.")
                    else:
                        save_result = db.save_telegram_receipt(expense, telegram_user_id=telegram_id)
                        currency = expense.get('currency', 'INR')
                        if save_result.get('success'):
                            send_whatsapp_message(sender_id, f"✅ Complete!\n\n🏪 {merchant.upper()}\n💰 {currency} {amount}\n💳 Bank: {bank_name}\n📝 Saved to Forensic Audit pool.")
                        else:
                            send_whatsapp_message(sender_id, "❌ Error saving receipt to ledger.")
                    
                    # Clean up queue and move to next
                    wa_pending_queues[sender_id].pop(0)
                    del wa_user_data[sender_id]
                    
                    if wa_pending_queues[sender_id]:
                        wa_process_next_receipt(sender_id)
                    else:
                        send_whatsapp_message(sender_id, "🎉 All receipts processed!")
            return jsonify({"status": "ok"}), 200

        # --- Handle Image or Document Receipts ---
        if msg_type in ["image", "document"]:
            media_id = message[msg_type]["id"]
            extension = "pdf" if msg_type == "document" else "jpg"
            
            if sender_id not in wa_pending_queues:
                wa_pending_queues[sender_id] = []
                
            send_whatsapp_message(sender_id, f"⏳ Processing your {msg_type}...")
            
            file_path = download_whatsapp_media(media_id, extension=extension)
            if file_path:
                # AI EXTRACTION
                extractor = ReceiptExtractor()
                expense_data = extractor.extract_expense_from_receipt(file_path)
                
                if "error" not in expense_data:
                    # Inject configuration
                    expense_data['source'] = 'whatsapp'
                    
                    wa_pending_queues[sender_id].append(expense_data)
                    
                    if len(wa_pending_queues[sender_id]) == 1:
                        wa_process_next_receipt(sender_id)
                    else:
                        send_whatsapp_message(sender_id, f"✅ Received {msg_type}! Will process next.")
                else:
                    send_whatsapp_message(sender_id, f"❌ Sorry, I couldn't read that {msg_type}:\n{expense_data['error']}")
                    
        # --- Handle Text Conversation ---
        elif msg_type == "text":
            text = message["text"]["body"].strip().upper()
            user_session = wa_user_data.get(sender_id)
            
            # If no active conversation, show welcome
            if not user_session:
                send_whatsapp_message(sender_id, "👋 Welcome to ExpenseFlow Bot!\nHow to use:\n1️⃣ Send receipt photo(s)\n2️⃣ Reply: P (Personal) or B (Business)\n3️⃣ Answer questions\nJust send a photo to get started! 📸")
                return jsonify({"status": "ok"}), 200
                
            state = user_session['state']
            expense = user_session['expense']
            
            if state == 'WAITING_FOR_PROJECT':
                expense['company_project'] = text if text != 'SKIP' else 'Not Specified'
                user_session['state'] = 'WAITING_FOR_NOTES_CHOICE'
                send_whatsapp_interactive_buttons(sender_id, "📝 Would you like to add notes?", [('note_add', 'Add Note'), ('note_skip', 'Skip')])
                
            elif state == 'WAITING_FOR_NOTES_TEXT':
                expense['notes'] = text
                user_session['state'] = 'WAITING_FOR_BANK'
                
                # Ask for Bank using Interactive Buttons
                buttons = [
                    ('bank_Amex', 'Amex'),
                    ('bank_Citi', 'Citi'),
                    ('bank_Other', 'Cash / Other')
                ]
                send_whatsapp_interactive_buttons(sender_id, "💳 Which account did you use for this?", buttons)

    except Exception as e:
        logger.error(f"❌ WhatsApp Webhook Error: {e}", exc_info=True)
        
    return jsonify({"status": "ok"}), 200


def run_flask():
    """Run Flask server in background thread"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)


class ExpenseMonitor:
    """Main orchestrator for 24/7 expense monitoring"""

    # ======================================================
    # OLD __init__ (SINGLE ACCOUNT) — KEPT FOR REFERENCE
    # ======================================================
    # def __init__(self):
    #     self.logger = setup_logger()
    #     self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', 10)) * 60
    #     self.gmail_service = None
    #     self.monitor = None
    #     self.extractor = None
    #     self.firebase = None

    # ======================================================
    # NEW __init__ (DUAL ACCOUNT) — ACTIVE
    # ======================================================
    def __init__(self):
        self.logger = setup_logger()
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', 10)) * 60

        # Primary Gmail service (Consolidated)
        self.gmail_service = None

        # Monitors
        self.transaction_monitor = None
        self.receipt_monitor = None

        self.transaction_extractor = None
        self.receipt_extractor = None
        self.firebase = None

        self.logger.info("=" * 70)
        self.logger.info("EXPENSE MANAGEMENT SYSTEM - STARTING UP")
        self.logger.info("=" * 70)

        self._initialize()

    # ======================================================
    # OLD _initialize (SINGLE ACCOUNT) — DISABLED
    # ======================================================
    # def _initialize(self):
    #     try:
    #         self.logger.info("Authenticating Gmail...")
    #         self.gmail_service = get_gmail_service()
    #
    #         self.logger.info("Initializing Gmail Monitor...")
    #         self.monitor = GmailMonitor(self.gmail_service)
    #
    #         self.logger.info("Initializing AI Extractor...")
    #         self.extractor = TransactionExtractor()
    #
    #         self.logger.info("Connecting to Database...")
    #         self.firebase = FirebaseClient()
    #
    #         self.logger.info("ALL SYSTEMS READY")
    #     except Exception as e:
    #         self.logger.error(f"INITIALIZATION FAILED: {str(e)}")
    #         raise

    # ======================================================
    # NEW _initialize (DUAL ACCOUNT) — ACTIVE
    # ======================================================
    def _initialize(self):
        """Initialize all components"""
        try:
            # Only use the Receipts Gmail account as requested
            self.logger.info("Authenticating expenseflow.10xds@gmail.com...")
            self.gmail_service = get_gmail_service_receipts()
            
            if not self.gmail_service:
                raise Exception("❌ CRITICAL: Failed to initialize Gmail Service for expenseflow.10xds@gmail.com")
            
            # Initialize Monitors using the same primary service
            self.receipt_monitor = ReceiptEmailMonitor(self.gmail_service)
            # (Transaction monitor kept for code structure, but pointed at same service)
            self.transaction_monitor = GmailMonitor(self.gmail_service)
            
            self.logger.info("✅ Gmail Monitor initialized for expenseflow.10xds@gmail.com")
            self.logger.info("Initializing AI Extractors...")
            self.transaction_extractor = TransactionExtractor()
            self.receipt_extractor = ReceiptExtractor()

            # 4. Connect to Firebase
            self.logger.info("Connecting to Database...")
            self.firebase = FirebaseClient()

            self.logger.info("ALL SYSTEMS READY")
            self.logger.info(
                f"Will check both inboxes every {self.check_interval // 60} minutes"
            )

        except Exception as e:
            self.logger.error(f"INITIALIZATION FAILED: {str(e)}")
            raise

    # ======================================================
    # OLD process_cycle (SINGLE ACCOUNT) — DISABLED
    # ======================================================
    # def process_cycle(self):
    #     try:
    #         last_timestamp = self.firebase.get_last_processed_timestamp()
    #         emails = self.monitor.fetch_new_transactions(after_timestamp=last_timestamp)
    #
    #         if not emails:
    #             return
    #
    #         transactions = self.extractor.extract_batch(emails)
    #         results = self.firebase.save_batch(transactions)
    #
    #     except Exception as e:
    #         self.logger.error(f"ERROR IN CYCLE: {str(e)}", exc_info=True)

    # ======================================================
    # NEW process_cycle (DUAL ACCOUNT) — ACTIVE
    # ======================================================
    def process_cycle(self):
        """Run one complete monitoring cycle for the primary account"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info("=" * 70)
            self.logger.info(f"STARTING MONITORING CYCLE - {current_time}")
            self.logger.info(f"Target Account: expenseflow.10xds@gmail.com")
            self.logger.info("=" * 70)

            self.logger.info(f"Checking for new emails...")
            last_sync_ts = self.firebase.get_last_processed_timestamp(source_filter='forwarded_email')
            self.logger.info(f"Last processed timestamp from DB: {last_sync_ts}")
            
            receipt_emails = self.receipt_monitor.fetch_new_receipts(
                after_timestamp=last_sync_ts
            )

            if receipt_emails:
                self.logger.info(f"Processing {len(receipt_emails)} new emails found in inbox...")
                for email in receipt_emails:
                    # Extract using multimodal logic (body + attachments)
                    extracted_data = self.receipt_extractor.extract_data_from_document(
                        body_text=email['body'],
                        attachment_paths=[a['path'] for a in email.get('attachments', [])]
                    )
                    
                    if extracted_data and 'error' not in extracted_data:
                        # Add metadata
                        extracted_data['gmail_message_id'] = email['message_id']
                        extracted_data['email_subject'] = email['subject']
                        extracted_data['email_sender'] = email['sender']
                        extracted_data['forwarded_from'] = email.get('forwarded_from')
                        extracted_data['source'] = 'forwarded_email' # Marker for indexing
                        extracted_data['user_id'] = "SHARED_POOL"
                        
                        # AI extraction already handles local paths for analysis.
                        # We just save the data now without uploading to storage as requested.
                        
                        # Save to Firebase
                        self.firebase.save_telegram_receipt(extracted_data)
                    else:
                        # If AI fails, still log it so we don't try again (optional)
                        self.logger.warning(f"Failed or skipped extracting from {email['message_id']}")
                    

                    # Prevent 429
                    import time
                    import random
                    time.sleep(5 + random.uniform(0, 2))
            else:
                self.logger.info("No new emails found in primary inbox.")

            self.logger.info("=" * 70)
            self.logger.info("CYCLE COMPLETE")
            self.logger.info("=" * 70)

        except Exception as e:
            self.logger.error(f"ERROR IN CYCLE: {str(e)}", exc_info=True)

    def run(self):
        """Run continuous monitoring loop"""
        cycle_count = 0
        try:
            while True:
                cycle_count += 1
                self.process_cycle()

                next_check = datetime.now() + timedelta(seconds=self.check_interval)
                self.logger.info(
                    f"Sleeping {self.check_interval // 60} minutes "
                    f"(Next run: {next_check.strftime('%Y-%m-%d %H:%M:%S')})"
                )
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            self.logger.info("MONITORING STOPPED BY USER")
        except Exception as e:
            self.logger.critical(f"FATAL ERROR: {str(e)}", exc_info=True)
            raise


# ================================
# BACKGROUND MONITOR STARTUP
# ================================
def start_monitor_in_background():
    try:
        # Use a small delay to let the server start
        time.sleep(5)
        logger.info("🧵 Starting Expense Monitor background thread...")
        monitor = ExpenseMonitor()
        monitor.run()
    except Exception as e:
        logger.error(f"Failed to start monitor thread: {e}")

# This ensures the monitor starts when the app is initialized by Gunicorn
if not os.environ.get("MONITOR_STARTED"):
    os.environ["MONITOR_STARTED"] = "true"
    Thread(target=start_monitor_in_background, daemon=True).start()
    logger.info("🚀 Expense Monitor background thread spawned")

# ================================
# ENTRY POINT
# ================================
if __name__ == "__main__":
    try:
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()

        time.sleep(2)

        monitor = ExpenseMonitor()
        monitor.run()

    except Exception as e:
        logging.critical(f"Failed to start: {str(e)}", exc_info=True)
        exit(1)
