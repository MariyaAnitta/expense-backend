import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class TransactionExtractor:
    """Extracts transaction data from email text using AI"""
    
    def __init__(self):
        # Initialize OpenRouter client (works with Gemini and other models)
        self.client = OpenAI(
            api_key=os.getenv('OPENROUTER_API_KEY'),
            base_url="https://openrouter.ai/api/v1"
        )
        self.model = os.getenv('OPENROUTER_MODEL', 'google/gemini-2.0-flash-exp')
    
    def extract_transaction(self, email_body, email_subject=""):
        """
        Extract transaction details from email text
        
        Args:
            email_body: The email content
            email_subject: Email subject (optional, helps with context)
            
        Returns:
            Dictionary with transaction details or None if extraction fails
        """
        try:
            print(f"🤖 Extracting transaction data using {self.model}...")
            
            # Craft the prompt
            prompt = f"""
You are a financial data extraction expert. Extract credit card/bank transaction details from this email.

EMAIL SUBJECT: {email_subject}

EMAIL BODY:
{email_body}

Extract and return ONLY a valid JSON object (no markdown, no explanation) with these exact fields:
{{
    "merchant": "merchant name or transaction description",
    "amount": numeric value only (no currency symbol),
    "currency": "currency code (INR, USD, etc.)",
    "date": "YYYY-MM-DD format",
    "time": "HH:MM:SS format if available, otherwise null",
    "card_last_4": "last 4 digits of card/account",
    "transaction_type": "credit or debit",
    "bank": "bank name",
    "account_holder": "account holder name if mentioned"
}}

Rules:
- If a field is not found in the email, use null
- Amount must be a number (no commas, no currency symbols)
- Date must be in YYYY-MM-DD format
- Return ONLY the JSON object, nothing else
"""

            # Call AI model
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=500
            )
            
            # Get response
            result_text = response.choices[0].message.content.strip()
            
            # Clean markdown if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            
            # Parse JSON
            transaction_data = json.loads(result_text)
            
            print("✅ Transaction extracted successfully!")
            return transaction_data
            
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse AI response as JSON: {e}")
            print(f"Raw response: {result_text}")
            return None
        except Exception as e:
            print(f"❌ Error during extraction: {str(e)}")
            return None
    
    def extract_batch(self, emails):
        """
        Extract transactions from multiple emails
        
        Args:
            emails: List of email dictionaries (from GmailMonitor)
            
        Returns:
            List of extracted transaction data
        """
        transactions = []
        
        print(f"\n🔄 Processing {len(emails)} emails for extraction...")
        
        for i, email in enumerate(emails, 1):
            print(f"\n--- Email {i}/{len(emails)} ---")
            print(f"Subject: {email['subject'][:60]}...")
            
            transaction = self.extract_transaction(
                email_body=email['body'],
                email_subject=email['subject']
            )
            
            if transaction:
                # Add email metadata
                transaction['gmail_message_id'] = email['message_id']
                transaction['email_subject'] = email['subject']
                transaction['email_sender'] = email['sender']
                transaction['source'] = 'email'
                
                transactions.append(transaction)
                
                # Display extracted data
                print(f"   💰 Amount: {transaction.get('currency')} {transaction.get('amount')}")
                print(f"   🏪 Merchant: {transaction.get('merchant')}")
                print(f"   📅 Date: {transaction.get('date')}")
            else:
                print(f"   ⚠️ Failed to extract transaction data")
        
        print(f"\n✅ Extracted {len(transactions)} transactions successfully")
        return transactions


# Test function
if __name__ == "__main__":
    from gmail_auth import GmailAuthenticator
    from gmail_monitor import GmailMonitor
    
    print("Testing Transaction Extractor...")
    
    # Authenticate Gmail
    auth = GmailAuthenticator()
    gmail_service = auth.authenticate()
    
    # Fetch emails
    monitor = GmailMonitor(gmail_service)
    emails = monitor.fetch_new_transactions(days_back=7)
    
    if not emails:
        print("\n❌ No emails to test extraction")
        exit()
    
    # Extract transactions
    extractor = TransactionExtractor()
    transactions = extractor.extract_batch(emails)
    
    # Display results
    if transactions:
        print("\n" + "="*60)
        print("📊 EXTRACTED TRANSACTIONS:")
        print("="*60)
        for i, t in enumerate(transactions, 1):
            print(f"\n{i}. Transaction Details:")
            print(f"   Merchant: {t.get('merchant')}")
            print(f"   Amount: {t.get('currency')} {t.get('amount')}")
            print(f"   Date: {t.get('date')}")
            print(f"   Time: {t.get('time')}")
            print(f"   Card: {t.get('card_last_4')}")
            print(f"   Type: {t.get('transaction_type')}")
            print(f"   Bank: {t.get('bank')}")
            print(f"   Account Holder: {t.get('account_holder')}")
