import time
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import setup_logger
from threading import Thread
from flask import Flask, request, jsonify

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

from gemini_extractor import TransactionExtractor
from firebase_client import FirebaseClient

load_dotenv()

# Create Flask app for health check (required by Render)
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

        # Dual Gmail services
        self.gmail_service_personal = None
        self.gmail_service_receipts = None

        # Dual monitors
        self.transaction_monitor = None
        self.receipt_monitor = None

        self.extractor = None
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
            # 1. Authenticate Both Gmail Accounts
            self.logger.info("Authenticating Personal Gmail (Transaction Alerts)...")
            self.gmail_service_personal = get_gmail_service_personal()

            self.logger.info("Authenticating Receipts Gmail (Forwarded Receipts)...")
            self.gmail_service_receipts = get_gmail_service_receipts()

            # 2. Initialize Both Monitors
            self.logger.info("Initializing Transaction Monitor...")
            self.transaction_monitor = GmailMonitor(self.gmail_service_personal)

            self.logger.info("Initializing Receipt Monitor...")
            self.receipt_monitor = ReceiptEmailMonitor(self.gmail_service_receipts)

            # 3. Initialize AI Extractor
            self.logger.info("Initializing AI Extractor...")
            self.extractor = TransactionExtractor()

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
        """Run one complete monitoring cycle for BOTH accounts"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info("=" * 70)
            self.logger.info(f"STARTING MONITORING CYCLE - {current_time}")
            self.logger.info("=" * 70)

            last_timestamp = self.firebase.get_last_processed_timestamp()

            # === ACCOUNT 1: TRANSACTIONS ===
            self.logger.info("\n[1/2] Checking Personal Gmail for transaction alerts...")
            transaction_emails = self.transaction_monitor.fetch_new_transactions(
                after_timestamp=last_timestamp
            )

            if transaction_emails:
                transactions = self.extractor.extract_batch(transaction_emails)
                if transactions:
                    results = self.firebase.save_batch(transactions)
                    self.logger.info(
                        f"Transaction emails: Saved {results['saved']}, "
                        f"Duplicates {results['duplicates']}"
                    )
            else:
                self.logger.info("No new transaction emails")

            # === ACCOUNT 2: RECEIPTS ===
            self.logger.info("\n[2/2] Checking Receipts Gmail for forwarded receipts...")
            receipt_emails = self.receipt_monitor.fetch_new_receipts(
                after_timestamp=last_timestamp
            )

            if receipt_emails:
                receipts = self.extractor.extract_batch(receipt_emails)
                if receipts:
                    for r in receipts:
                        r['source'] = 'forwarded_email'

                    results = self.firebase.save_batch(receipts)
                    self.logger.info(
                        f"Receipt emails: Saved {results['saved']}, "
                        f"Duplicates {results['duplicates']}"
                    )
            else:
                self.logger.info("No new receipt emails")

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
