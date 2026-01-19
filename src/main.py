import time
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import setup_logger
from gmail_auth import get_gmail_service
from gmail_monitor import GmailMonitor
from gemini_extractor import TransactionExtractor
#from supabase_client import SupabaseClient
from firebase_client import FirebaseClient
from threading import Thread
from flask import Flask


load_dotenv()


# Create Flask app for health check (required by Render)
app = Flask(__name__)


@app.route('/')
def health_check():
    return "Expense Monitor is running", 200


@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200


def run_flask():
    """Run Flask server in background thread"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


class ExpenseMonitor:
    """Main orchestrator for 24/7 expense monitoring"""
    
    def __init__(self):
        self.logger = setup_logger()
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', 10)) * 60
        self.gmail_service = None
        self.monitor = None
        self.extractor = None
        self.supabase = None
        
        self.logger.info("="*70)
        self.logger.info("EXPENSE MANAGEMENT SYSTEM - STARTING UP")
        self.logger.info("="*70)
        
        # Initialize all components
        self._initialize()
    
    def _initialize(self):
        """Initialize all components"""
        try:
            # 1. Authenticate Gmail
            self.logger.info("Authenticating Gmail...")
            self.gmail_service = get_gmail_service()
            
            # 2. Initialize Gmail Monitor
            self.logger.info("Initializing Gmail Monitor...")
            self.monitor = GmailMonitor(self.gmail_service)
            
            # 3. Initialize AI Extractor
            self.logger.info("Initializing AI Extractor...")
            self.extractor = TransactionExtractor()
            
            # 4. Connect to Supabase
            self.logger.info("Connecting to Database...")
            #self.supabase = SupabaseClient()
            self.firebase = FirebaseClient()

            
            self.logger.info("ALL SYSTEMS READY")
            self.logger.info(f"Will check for new transactions every {self.check_interval // 60} minutes")
            
        except Exception as e:
            self.logger.error(f"INITIALIZATION FAILED: {str(e)}")
            raise
    
    def process_cycle(self):
        """Run one complete monitoring cycle"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            self.logger.info("="*70)
            self.logger.info(f"STARTING MONITORING CYCLE - {current_time}")
            self.logger.info("="*70)
            
            # Step 1: Fetch new emails (last 24 hours)
            emails = self.monitor.fetch_new_transactions(days_back=1)
            
            if not emails:
                self.logger.info("No new transaction emails found")
                return
            
            # Step 2: Extract transaction data
            transactions = self.extractor.extract_batch(emails)
            
            if not transactions:
                self.logger.warning("No transactions could be extracted")
                return
            
            # Step 3: Save to database
            #results = self.supabase.save_batch(transactions)
            results = self.firebase.save_batch(transactions)

            # Step 4: Summary
            self.logger.info("="*70)
            self.logger.info("CYCLE COMPLETE")
            self.logger.info(f"Emails Found: {len(emails)}")
            self.logger.info(f"Extracted: {len(transactions)}")
            self.logger.info(f"Saved: {results['saved']}")
            self.logger.info(f"Duplicates: {results['duplicates']}")
            self.logger.info(f"Failed: {results['failed']}")
            self.logger.info("="*70)
            
        except Exception as e:
            self.logger.error(f"ERROR IN CYCLE: {str(e)}", exc_info=True)
    
    def run(self):
        """Run continuous monitoring loop"""
        self.logger.info("="*70)
        self.logger.info("STARTING CONTINUOUS MONITORING")
        self.logger.info("Press Ctrl+C to stop")
        self.logger.info("="*70)
        
        cycle_count = 0
        
        try:
            while True:
                cycle_count += 1
                
                # Run monitoring cycle
                self.process_cycle()
                
                # Calculate next check time using timedelta to handle minute overflow
                next_check_time = datetime.now() + timedelta(seconds=self.check_interval)
                next_check_str = next_check_time.strftime("%Y-%m-%d %H:%M:%S")
                
                self.logger.info(f"Sleeping for {self.check_interval // 60} minutes...")
                self.logger.info(f"Next check at approximately: {next_check_str}")
                self.logger.info(f"Total cycles completed: {cycle_count}")
                
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            self.logger.info("="*70)
            self.logger.info("MONITORING STOPPED BY USER")
            self.logger.info(f"Total cycles completed: {cycle_count}")
            self.logger.info("="*70)
        except Exception as e:
            self.logger.critical(f"FATAL ERROR: {str(e)}", exc_info=True)
            raise


# Entry point
if __name__ == "__main__":
    try:
        # Start Flask health check server in background thread
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Get logger to log Flask startup
        logger = logging.getLogger(__name__)
        logger.info(f"Health check endpoint started on port {os.getenv('PORT', 10000)}")
        
        # Start main monitoring
        monitor = ExpenseMonitor()
        monitor.run()
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.critical(f"Failed to start: {str(e)}", exc_info=True)
        exit(1)