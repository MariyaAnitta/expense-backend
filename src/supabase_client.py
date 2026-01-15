import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

class SupabaseClient:
    """Handles all Supabase database operations using REST API"""
    
    def __init__(self):
        self.supabase_url = os.getenv('SUPABASE_URL')
        self.supabase_key = os.getenv('SUPABASE_KEY')
        
        if not self.supabase_url or not self.supabase_key:
            raise Exception("❌ Supabase credentials not found in .env file")
        
        # REST API endpoint
        self.api_url = f"{self.supabase_url}/rest/v1/expenses"
        
        # Headers for authentication
        self.headers = {
            'apikey': self.supabase_key,
            'Authorization': f'Bearer {self.supabase_key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=representation'
        }
        
        print("✅ Connected to Supabase (REST API)")
    
    def transaction_exists(self, gmail_message_id):
        """
        Check if transaction already exists in database
        
        Args:
            gmail_message_id: Gmail message ID
            
        Returns:
            Boolean - True if exists, False if not
        """
        try:
            # Query for existing transaction
            url = f"{self.api_url}?gmail_message_id=eq.{gmail_message_id}&select=id"
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 200:
                return len(response.json()) > 0
            else:
                print(f"⚠️  Error checking duplicate: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error checking duplicate: {e}")
            return False
    
    def save_transaction(self, transaction_data):
        """
        Save transaction to Supabase
        
        Args:
            transaction_data: Dictionary with transaction details
            
        Returns:
            Boolean - True if saved, False if failed or duplicate
        """
        try:
            gmail_message_id = transaction_data.get('gmail_message_id')
            
            # Check for duplicate
            if self.transaction_exists(gmail_message_id):
                print(f"⚠️  Transaction already exists (Message ID: {gmail_message_id[:20]}...)")
                return False
            
            # Prepare data for insertion
            expense_data = {
                'merchant': transaction_data.get('merchant'),
                'amount': transaction_data.get('amount'),
                'currency': transaction_data.get('currency', 'INR'),
                'date': transaction_data.get('date'),
                'time': transaction_data.get('time'),
                'category': transaction_data.get('bank'),
                'source': 'email',
                'description': f"{transaction_data.get('transaction_type', 'transaction')} - {transaction_data.get('merchant')}",
                'confidence': 0.95,
                'gmail_message_id': gmail_message_id,
                'card_last_4': transaction_data.get('card_last_4'),
                'transaction_type': transaction_data.get('transaction_type'),
                'bank': transaction_data.get('bank'),
                'account_holder': transaction_data.get('account_holder'),
                'email_subject': transaction_data.get('email_subject'),
                'email_sender': transaction_data.get('email_sender'),
                'created_at': datetime.now().isoformat()

            }
            
            # Insert into database
            response = requests.post(self.api_url, json=expense_data, headers=self.headers)
            
            if response.status_code in [200, 201]:
                print(f"✅ Saved: {expense_data['currency']} {expense_data['amount']} - {expense_data['merchant'][:40]}")
                return True
            else:
                print(f"❌ Failed to save: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error saving transaction: {str(e)}")
            return False
    
    def save_batch(self, transactions):
        """
        Save multiple transactions
        
        Args:
            transactions: List of transaction dictionaries
            
        Returns:
            Dictionary with success/failure counts
        """
        results = {
            'saved': 0,
            'duplicates': 0,
            'failed': 0
        }
        
        print(f"\n💾 Saving {len(transactions)} transactions to Supabase...")
        
        for i, transaction in enumerate(transactions, 1):
            print(f"\n[{i}/{len(transactions)}]", end=" ")
            
            if self.save_transaction(transaction):
                results['saved'] += 1
            else:
                # Check if it was duplicate or error
                if self.transaction_exists(transaction.get('gmail_message_id')):
                    results['duplicates'] += 1
                else:
                    results['failed'] += 1
        
        # Summary
        print("\n" + "="*60)
        print("📊 SAVE SUMMARY:")
        print(f"   ✅ Saved: {results['saved']}")
        print(f"   ⚠️  Duplicates: {results['duplicates']}")
        print(f"   ❌ Failed: {results['failed']}")
        print("="*60)
        
        return results


# Test function
if __name__ == "__main__":
    from gmail_auth import GmailAuthenticator
    from gmail_monitor import GmailMonitor
    from gemini_extractor import TransactionExtractor
    
    print("Testing Supabase Integration...")
    
    # Step 1: Authenticate Gmail
    auth = GmailAuthenticator()
    gmail_service = auth.authenticate()
    
    # Step 2: Fetch emails
    monitor = GmailMonitor(gmail_service)
    emails = monitor.fetch_new_transactions(days_back=7)
    
    if not emails:
        print("\n❌ No emails found")
        exit()
    
    # Step 3: Extract transactions
    extractor = TransactionExtractor()
    transactions = extractor.extract_batch(emails)
    
    if not transactions:
        print("\n❌ No transactions extracted")
        exit()
    
    # Step 4: Save to Supabase
    supabase = SupabaseClient()
    results = supabase.save_batch(transactions)
    
    print("\n✅ TEST COMPLETE!")
    print(f"Check your Supabase dashboard to see the saved transactions")
