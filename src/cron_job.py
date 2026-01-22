"""
Render Cron Job - Runs once per execution
"""
import os
from dotenv import load_dotenv
from gmail_auth import get_gmail_service
from gmail_monitor import GmailMonitor
from gemini_extractor import TransactionExtractor
from firebase_client import FirebaseClient

load_dotenv()

def run_once():
    """Run one cycle of email monitoring"""
    print("\n" + "="*70)
    print("🚀 EXPENSE FLOW - Email Monitor (Cron)")
    print("="*70)
    
    try:
        # Initialize services
        print("Initializing services...")
        gmail_service = get_gmail_service()
        monitor = GmailMonitor(gmail_service)
        extractor = TransactionExtractor()
        firebase = FirebaseClient()
        
        # Get last check timestamp
        last_timestamp = firebase.get_last_check_timestamp()
        print(f"Last check: {last_timestamp}")
        
        # Fetch new emails
        emails = monitor.fetch_new_transactions(after_timestamp=last_timestamp)
        
        if not emails:
            print("✅ No new transaction emails found")
            print("="*70 + "\n")
            return
        
        # Extract transaction data
        transactions = extractor.extract_batch(emails)
        
        # Save to Firebase
        if transactions:
            result = firebase.save_transactions(transactions)
            print(f"\n{'='*70}")
            print(f"✅ CYCLE COMPLETE")
            print(f"   Saved: {result['saved']}")
            print(f"   Duplicates: {result['duplicates']}")
            print(f"   Failed: {result['failed']}")
            print(f"{'='*70}\n")
        else:
            print("⚠️ No transactions extracted")
            print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ ERROR: {str(e)}")
        print(f"{'='*70}\n")
        raise  # Re-raise so Render marks job as failed

if __name__ == "__main__":
    run_once()
