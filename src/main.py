import time
import os
from datetime import datetime
from dotenv import load_dotenv
from logger import setup_logger
from gmail_auth import GmailAuthenticator
from gmail_monitor import GmailMonitor
from gemini_extractor import TransactionExtractor
from supabase_client import SupabaseClient

load_dotenv()

class ExpenseMonitor:
    """Main orchestrator for 24/7 expense monitoring"""
    
    def __init__(self):
        self.logger = setup_logger()  # Add this line
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', 10)) * 60
        self.gmail_service = None
        self.monitor = None
        self.extractor = None
        self.supabase = None
        
        print("="*70)
        print("💰 EXPENSE MANAGEMENT SYSTEM - STARTING UP")
        print("="*70)
        
        # Initialize all components
        self._initialize()
    
    def _initialize(self):
        """Initialize all components"""
        try:
            # 1. Authenticate Gmail
            print("\n🔐 Authenticating Gmail...")
            auth = GmailAuthenticator()
            self.gmail_service = auth.authenticate()
            
            # 2. Initialize Gmail Monitor
            print("📧 Initializing Gmail Monitor...")
            self.monitor = GmailMonitor(self.gmail_service)
            
            # 3. Initialize AI Extractor
            print("🤖 Initializing AI Extractor...")
            self.extractor = TransactionExtractor()
            
            # 4. Connect to Supabase
            print("💾 Connecting to Database...")
            self.supabase = SupabaseClient()
            
            print("\n✅ ALL SYSTEMS READY!")
            print(f"⏰ Will check for new transactions every {self.check_interval // 60} minutes")
            
        except Exception as e:
            print(f"\n❌ INITIALIZATION FAILED: {str(e)}")
            raise
    
    def process_cycle(self):
        """Run one complete monitoring cycle"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print("\n" + "="*70)
            print(f"🔄 STARTING MONITORING CYCLE - {current_time}")
            print("="*70)
            
            # Step 1: Fetch new emails (last 24 hours)
            emails = self.monitor.fetch_new_transactions(days_back=1)
            
            if not emails:
                print("\n📭 No new transaction emails found")
                return
            
            # Step 2: Extract transaction data
            transactions = self.extractor.extract_batch(emails)
            
            if not transactions:
                print("\n⚠️  No transactions could be extracted")
                return
            
            # Step 3: Save to database
            results = self.supabase.save_batch(transactions)
            
            # Step 4: Summary
            print("\n" + "="*70)
            print("📊 CYCLE COMPLETE")
            print(f"   Emails Found: {len(emails)}")
            print(f"   Extracted: {len(transactions)}")
            print(f"   Saved: {results['saved']}")
            print(f"   Duplicates: {results['duplicates']}")
            print(f"   Failed: {results['failed']}")
            print("="*70)
            
        except Exception as e:
            print(f"\n❌ ERROR IN CYCLE: {str(e)}")
    
    def run(self):
        """Run continuous monitoring loop"""
        print("\n" + "="*70)
        print("🚀 STARTING CONTINUOUS MONITORING")
        print("   Press Ctrl+C to stop")
        print("="*70)
        
        cycle_count = 0
        
        try:
            while True:
                cycle_count += 1
                
                # Run monitoring cycle
                self.process_cycle()
                
                # Wait before next check
                next_check = datetime.now()
                next_check = next_check.replace(second=0, microsecond=0)
                next_check_str = (
                    datetime.now().replace(second=0, microsecond=0)
                    .replace(minute=datetime.now().minute + (self.check_interval // 60))
                    .strftime("%H:%M:%S")
                )
                
                print(f"\n⏸️  Sleeping for {self.check_interval // 60} minutes...")
                print(f"   Next check at approximately: {next_check_str}")
                print(f"   Total cycles completed: {cycle_count}")
                
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            print("\n\n" + "="*70)
            print("🛑 MONITORING STOPPED BY USER")
            print(f"   Total cycles completed: {cycle_count}")
            print("="*70)
        except Exception as e:
            print(f"\n\n❌ FATAL ERROR: {str(e)}")
            raise


# Entry point
if __name__ == "__main__":
    try:
        monitor = ExpenseMonitor()
        monitor.run()
    except Exception as e:
        print(f"\n❌ Failed to start: {str(e)}")
        exit(1)
