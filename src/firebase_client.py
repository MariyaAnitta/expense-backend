import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

class FirebaseClient:
    """Handles all Firebase Firestore operations"""
    
    def __init__(self):
        # Initialize Firebase Admin SDK
        if not firebase_admin._apps:
            cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'credentials/firebase-credentials.json')
            
            # Check if file exists (local development)
            if os.path.exists(cred_path):
                print(f"Loading Firebase credentials from {cred_path}")
                cred = credentials.Certificate(cred_path)
            else:
                # On Render, use environment variable with JSON content
                print("Loading Firebase credentials from environment variable")
                import json
                cred_json_str = os.getenv('FIREBASE_CREDENTIALS_JSON')
                if not cred_json_str:
                    raise Exception("Firebase credentials not found! Need either file or FIREBASE_CREDENTIALS_JSON env var")
                cred_json = json.loads(cred_json_str)
                cred = credentials.Certificate(cred_json)
            
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
        print("✅ Connected to Firebase Firestore")
    
    def transaction_exists(self, gmail_message_id):
        """
        Check if transaction already exists
        """
        try:
            docs = self.db.collection('expenses').where(
                'gmail_message_id', '==', gmail_message_id
            ).limit(1).stream()
            
            return len(list(docs)) > 0
        except Exception as e:
            print(f"Error checking duplicate: {e}")
            return False
    
    def save_transaction(self, transaction_data):
        """
        Save transaction to Firestore
        """
        try:
            gmail_message_id = transaction_data.get('gmail_message_id')
            
            # Check for duplicate
            if self.transaction_exists(gmail_message_id):
                print(f"⚠️ Transaction already exists (Message ID: {gmail_message_id[:20]}...)")
                return False
            
            expense_data = {
                'merchant': transaction_data.get('merchant'),
                'amount': float(transaction_data.get('amount', 0)),
                'currency': transaction_data.get('currency', 'INR'),
                'date': transaction_data.get('date'),
                'time': transaction_data.get('time'),
                'category': transaction_data.get('bank'), # This seems like a bug, should be 'category'
                'source': transaction_data.get('source', 'gmail_alert'), # Default changed to gmail_alert
                'description': f"{transaction_data.get('transaction_type', 'transaction')} - {transaction_data.get('merchant')}",
                'confidence': 0.95,
                'gmail_message_id': gmail_message_id,
                'card_digits': transaction_data.get('card_digits'),
                'card_details': transaction_data.get('card_digits') or transaction_data.get('card_details'),
                'transaction_type': transaction_data.get('transaction_type'),
                'bank': transaction_data.get('bank'),
                'account_holder': transaction_data.get('account_holder'),
                'email_subject': transaction_data.get('email_subject'),
                'email_sender': transaction_data.get('email_sender'),
                'user_id': 'SHARED_POOL',
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            self.db.collection('expenses').add(expense_data)
            
            print(f"✅ Saved: {expense_data['currency']} {expense_data['amount']} - {expense_data['merchant'][:40]}")
            return True
            
        except Exception as e:
            print(f"❌ Error saving transaction: {str(e)}")
            return False

    def check_duplicate_receipt(self, merchant, amount, date, telegram_user_id):
        """
        Check if a similar receipt already exists
        
        Args:
            merchant: Merchant name
            amount: Transaction amount
            date: Transaction date (YYYY-MM-DD format)
            telegram_user_id: User's Telegram ID
        
        Returns:
            Dictionary with 'is_duplicate' (bool) and 'existing_receipt' (doc data if found)
        """
        try:
            docs = self.db.collection('expenses') \
                .where('merchant', '==', merchant.upper()) \
                .where('amount', '==', float(amount)) \
                .where('date', '==', date) \
                .where('telegram_user_id', '==', telegram_user_id) \
                .limit(1) \
                .stream()
            
            existing_receipts = list(docs)
            
            if existing_receipts:
                receipt_data = existing_receipts[0].to_dict()
                receipt_data['id'] = existing_receipts[0].id
                
                print(f"⚠️ Duplicate detected: {merchant} - {amount} on {date}")
                
                return {
                    'is_duplicate': True,
                    'existing_receipt': receipt_data
                }
            
            return {
                'is_duplicate': False,
                'existing_receipt': None
            }
            
        except Exception as e:
            print(f"❌ Error checking duplicate: {e}")
            return {
                'is_duplicate': False,
                'existing_receipt': None
            }
    
    def save_telegram_receipt(self, expense_data, telegram_user_id=None):
        """
        Save Telegram receipt expense to Firestore
        """
        try:
            # CHECK FOR DUPLICATES FIRST (by Gmail message ID if available)
            gmail_message_id = expense_data.get('gmail_message_id')
            if gmail_message_id and self.transaction_exists(gmail_message_id):
                print(f"⚠️ Receipt already exists (Message ID: {gmail_message_id[:20]}...)")
                return {'success': False, 'error': 'duplicate'}

            # Standardize category name
            cat = expense_data.get('category', 'General')
            if cat == 'Food': cat = 'Meals'
            
            receipt_data = {
                'merchant': str(expense_data.get('merchant', 'Unknown')).upper(),
                'amount': float(expense_data.get('amount', 0)),
                'bank': expense_data.get('bank', 'Other'),
                'card_digits': expense_data.get('card_digits'), # Auto-Pilot field
                'card_details': expense_data.get('card_digits') or expense_data.get('card_details'), # Compatibility
                'currency': expense_data.get('currency', 'INR'),
                'date': expense_data.get('date'),
                'cat': cat,
                'source': expense_data.get('source', 'telegram'),
                'items': expense_data.get('items', []),
                'payment_method': expense_data.get('payment_method'),
                'tax_amount': expense_data.get('tax_amount'),
                'telegram_user_id': telegram_user_id,
                'file_path': expense_data.get('file_path'),
                'confidence': 0.90,
                
                # Metadata fields
                'main_category': expense_data.get('main_category'),
                'company_project': expense_data.get('company_project'),
                'reimbursement_status': expense_data.get('reimbursement_status'),
                'paid_by': expense_data.get('paid_by'),
                'notes': expense_data.get('notes'),
                'user_id': 'SHARED_POOL',
                'gmail_message_id': expense_data.get('gmail_message_id'),
                
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # Remove null values
            receipt_data = {k: v for k, v in receipt_data.items() if v is not None}
            
            doc_ref = self.db.collection('expenses').add(receipt_data)
            expense_id = doc_ref[1].id
            
            print(f"✅ Saved to Financial Ledger: {receipt_data['currency']} {receipt_data['amount']} - {receipt_data['merchant']}")
            
            # Check if this should also go to Mobility Ledger
            if cat in ['Lodging', 'Transport'] or expense_data.get('is_mobility'):
                self.save_mobility_log(expense_data, telegram_user_id)

            return {'success': True, 'expense_id': expense_id}
            
        except Exception as e:
            print(f"❌ Error saving Telegram receipt: {str(e)}")
            return {'success': False, 'error': str(e)}

    def save_mobility_log(self, mobility_data, user_id=None):
        """
        Save data to mobility_logs collection (Flights, Hotels, Visas)
        """
        try:
            log_data = {
                'type': mobility_data.get('mobility_type'), # 'flight' or 'accommodation'
                'provider': mobility_data.get('merchant_name') or mobility_data.get('provider'),
                'destination': mobility_data.get('destination'),
                'date': mobility_data.get('date'), # Start/Departure
                'end_date': mobility_data.get('end_date'), # Return/Check-out
                'pnr': mobility_data.get('pnr') or mobility_data.get('booking_id'),
                'guest_name': mobility_data.get('guest_name'),
                'user_id': 'SHARED_POOL',
                'amount': float(mobility_data.get('total_amount', 0)),
                'currency': mobility_data.get('currency', 'INR'),
                'source': mobility_data.get('source', 'unknown'),
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # Clean nulls
            log_data = {k: v for k, v in log_data.items() if v is not None}
            
            self.db.collection('travel_logs').add(log_data)
            print(f"✈️ Saved to Mobility Ledger: {log_data.get('type')} at {log_data.get('provider')}")
            return True
        except Exception as e:
            print(f"❌ Error saving mobility log: {e}")
            return False

    def save_batch(self, transactions):
        """
        Save multiple transactions
        """
        results = {'saved': 0, 'duplicates': 0, 'failed': 0}
        
        print(f"\n📦 Saving {len(transactions)} transactions to Firebase...")
        
        for transaction in transactions:
            if self.save_transaction(transaction):
                results['saved'] += 1
            else:
                if self.transaction_exists(transaction.get('gmail_message_id')):
                    results['duplicates'] += 1
                else:
                    results['failed'] += 1
        
        return results
    
    def save_reconciliation_report(self, report_data):
        """Save monthly reconciliation report"""
        try:
            doc_ref = self.db.collection('reconciliations').add({
                'month': report_data.get('month'),
                'year': report_data.get('year'),
                'matched_transactions': report_data.get('matched_transactions', []),
                'unmatched_transactions': report_data.get('unmatched_transactions', []),
                'summary': report_data.get('summary', {}),
                'created_at': firestore.SERVER_TIMESTAMP
            })
            return doc_ref[1].id
        except Exception as e:
            print(f"❌ Error saving reconciliation: {e}")
            return None
    
    def get_reconciliation_reports(self, year=None):
        """Get saved reconciliation reports"""
        try:
            query = self.db.collection('reconciliations')
            if year:
                query = query.where('year', '==', year)
            query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
            
            return [{**doc.to_dict(), 'id': doc.id} for doc in query.stream()]
        except Exception as e:
            print(f"❌ Error fetching reconciliations: {e}")
            return []
    
    def get_last_processed_timestamp(self, source_filter=None):
        """
        Get the timestamp of most recently processed email.
        Optional source_filter: e.g. 'gmail_alert' or 'forwarded_email'
        """
        try:
            query = self.db.collection('expenses')
            
            if source_filter:
                query = query.where('source', '==', source_filter)
                
            docs = query.order_by('created_at', direction=firestore.Query.DESCENDING) \
                .limit(1) \
                .stream()
            
            for doc in docs:
                return doc.to_dict().get('created_at')
            return None
        except Exception as e:
            print(f"❌ Error getting last timestamp: {e}")
            return None
