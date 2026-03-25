import os
import requests
import uuid
from dotenv import load_dotenv
from datetime import datetime
from supabase import create_client, Client

load_dotenv()

class SupabaseClient:
    """Handles all Supabase database and storage operations"""
    
    def __init__(self):
        # Load from env and strip any potential whitespace/quotes
        self.supabase_url = (os.getenv('SUPABASE_URL') or '').strip().strip('"').strip("'")
        self.supabase_key = (os.getenv('SUPABASE_KEY') or '').strip().strip('"').strip("'")
        
        if not self.supabase_url or not self.supabase_key:
            print("❌ ERROR: SUPABASE_URL or SUPABASE_KEY is missing!")
            print("🔗 If running on Render, ensure you've added these to 'Environment Variables' in the dashboard.")
            raise Exception("Supabase credentials not found. Check your environment variables.")
        
        # REST API endpoint (legacy/database)
        self.api_url = f"{self.supabase_url}/rest/v1/expenses"
        
        # Headers for REST API
        self.headers = {
            'apikey': self.supabase_key,
            'Authorization': f'Bearer {self.supabase_key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=representation'
        }
        
        # Official Supabase Client for Storage
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        
        print("✅ Connected to Supabase (REST API & Storage)")

    def upload_receipt(self, file_path, mime_type):
        """
        Upload file to Supabase Storage and return public URL
        
        Args:
            file_path: Local path to file
            mime_type: MIME type of the file
            
        Returns:
            String - Public URL if success, None if failed
        """
        try:
            if not os.path.exists(file_path):
                print(f"❌ File not found: {file_path}")
                return None
                
            file_name = os.path.basename(file_path)
            # Ensure unique filename to prevent collisions
            unique_name = f"{uuid.uuid4()}_{file_name}"
            
            bucket_name = "receipt-storage"
            
            with open(file_path, "rb") as f:
                file_data = f.read()
                
            # Upload to Supabase Storage
            res = self.client.storage.from_(bucket_name).upload(
                path=unique_name,
                file=file_data,
                file_options={"content-type": mime_type}
            )
            
            # Get public URL
            public_url = self.client.storage.from_(bucket_name).get_public_url(unique_name)
            
            print(f"✅ Uploaded to Supabase: {public_url}")
            return public_url
            
        except Exception as e:
            print(f"❌ Error uploading to Supabase: {str(e)}")
            return None

    
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
