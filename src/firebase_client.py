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
        print("Connected to Firebase Firestore")

    
    def transaction_exists(self, gmail_message_id):
        """
        Check if transaction already exists
        
        Args:
            gmail_message_id: Gmail message ID
            
        Returns:
            Boolean - True if exists, False if not
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
        
        Args:
            transaction_data: Dictionary with transaction details
            
        Returns:
            Boolean - True if saved, False if failed or duplicate
        """
        try:
            gmail_message_id = transaction_data.get('gmail_message_id')
            
            # Check for duplicate
            if self.transaction_exists(gmail_message_id):
                print(f"Transaction already exists (Message ID: {gmail_message_id[:20]}...)")
                return False
            
            # Prepare data for Firestore
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
            
            # Add document to Firestore
            self.db.collection('expenses').add(expense_data)
            
            print(f"Saved: {expense_data['currency']} {expense_data['amount']} - {expense_data['merchant'][:40]}")
            return True
            
        except Exception as e:
            print(f"Error saving transaction: {str(e)}")
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
        
        print(f"Saving {len(transactions)} transactions to Firebase...")
        
        for i, transaction in enumerate(transactions, 1):
            print(f"[{i}/{len(transactions)}]", end=" ")
            
            if self.save_transaction(transaction):
                results['saved'] += 1
            else:
                # Check if it was duplicate or error
                if self.transaction_exists(transaction.get('gmail_message_id')):
                    results['duplicates'] += 1
                else:
                    results['failed'] += 1
    
        # Summary
        print("=" * 60)
        print("SAVE SUMMARY:")
        print(f"  Saved: {results['saved']}")
        print(f"  Duplicates: {results['duplicates']}")
        print(f"  Failed: {results['failed']}")
        print("=" * 60)
        
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
            print(f"Saved reconciliation: {report_data.get('month')} {report_data.get('year')}")
            return doc_ref[1].id
        except Exception as e:
            print(f"Error saving reconciliation: {e}")
            return None

    def get_reconciliation_reports(self, year=None):
        """Get saved reconciliation reports"""
        try:
            query = self.db.collection('reconciliations')
            if year:
                query = query.where('year', '==', year)

            query = query.order_by(
                'created_at',
                direction=firestore.Query.DESCENDING
            )
            
            docs = query.stream()
            reports = []

            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                reports.append(data)

            return reports
        except Exception as e:
            print(f"Error fetching reconciliations: {e}")
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
            print(f"Error getting last timestamp: {e}")
            return None
