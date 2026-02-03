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
            docs = self.db.collection('telegram_receipts') \
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
        Save Telegram receipt expense to Firestore with new Personal/Business structure
        """
        try:
            # Get category and sub-category info
            category = expense_data.get('category', 'Personal')  # 'Personal' or 'Business'
            is_reimbursable = expense_data.get('is_reimbursable', False)
            
            # Determine the full category string for display
            if category == 'Personal':
                full_category = 'Personal'
            else:
                full_category = 'Business - Reimbursable' if is_reimbursable else 'Business - Company expense'
            
            receipt_data = {
                # Basic info
                'merchant': expense_data.get('merchant_name', 'Unknown'),
                'amount': float(expense_data.get('total_amount', 0)),
                'currency': expense_data.get('currency', 'INR'),
                'date': expense_data.get('date'),
                'source': 'telegram',
                
                # New category structure
                'category': category,  # 'Personal' or 'Business'
                'full_category': full_category,  # Display string
                
                # Business-specific fields
                'is_reimbursable': is_reimbursable if category == 'Business' else None,
                'project_name': expense_data.get('project_name') if category == 'Business' else None,
                
                # Detailed info
                'items': expense_data.get('items', []),
                'tax_amount': expense_data.get('tax_amount'),
                'payment_method': expense_data.get('payment_method'),
                
                # Metadata
                'telegram_user_id': telegram_user_id,
                'file_path': expense_data.get('file_path'),
                'confidence': 0.90,
                'notes': expense_data.get('notes'),
                'created_at': firestore.SERVER_TIMESTAMP
            }
            
            # Remove None values
            receipt_data = {k: v for k, v in receipt_data.items() if v is not None}
            
            # Save to Firestore
            doc_ref = self.db.collection('telegram_receipts').add(receipt_data)
            expense_id = doc_ref[1].id
            
            print(f"✅ Saved Telegram receipt: {receipt_data['currency']} {receipt_data['amount']} - {receipt_data['merchant']} ({full_category})")
            
            return {'success': True, 'expense_id': expense_id}
            
        except Exception as e:
            print(f"❌ Error saving Telegram receipt: {str(e)}")
            return {'success': False, 'error': str(e)}
    
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
    
    def get_last_processed_timestamp(self):
        """Get the timestamp of most recently processed email"""
        try:
            docs = self.db.collection('expenses') \
                .order_by('created_at', direction=firestore.Query.DESCENDING) \
                .limit(1) \
                .stream()
            
            for doc in docs:
                return doc.to_dict().get('created_at')
            return None
        except Exception as e:
            print(f"❌ Error getting last timestamp: {e}")
            return None
    
    def get_monthly_summary(self, telegram_user_id, year, month):
        """
        Get monthly summary for a user
        Returns breakdown by Personal and Business categories
        """
        try:
            # Query receipts for the month
            start_date = f"{year}-{month:02d}-01"
            if month == 12:
                end_date = f"{year + 1}-01-01"
            else:
                end_date = f"{year}-{month + 1:02d}-01"
            
            docs = self.db.collection('telegram_receipts') \
                .where('telegram_user_id', '==', telegram_user_id) \
                .where('date', '>=', start_date) \
                .where('date', '<', end_date) \
                .stream()
            
            receipts = [doc.to_dict() for doc in docs]
            
            # Categorize
            personal_total = 0
            business_reimbursable_total = 0
            business_company_total = 0
            personal_count = 0
            business_count = 0
            
            for receipt in receipts:
                amount = receipt.get('amount', 0)
                category = receipt.get('category', 'Personal')
                
                if category == 'Personal':
                    personal_total += amount
                    personal_count += 1
                else:
                    business_count += 1
                    if receipt.get('is_reimbursable'):
                        business_reimbursable_total += amount
                    else:
                        business_company_total += amount
            
            return {
                'personal': {
                    'count': personal_count,
                    'total': personal_total
                },
                'business': {
                    'count': business_count,
                    'reimbursable': business_reimbursable_total,
                    'company_expense': business_company_total,
                    'total': business_reimbursable_total + business_company_total
                },
                'grand_total': personal_total + business_reimbursable_total + business_company_total
            }
            
        except Exception as e:
            print(f"❌ Error getting monthly summary: {e}")
            return None