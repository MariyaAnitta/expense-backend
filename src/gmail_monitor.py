import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import re

class GmailMonitor:
    """Monitors Gmail for credit card transaction emails"""
    
    def __init__(self, gmail_service):
        self.service = gmail_service
        
    def search_transaction_emails(self, days_back=1):
        """
        Search for credit card transaction emails
        
        Args:
            days_back: How many days back to search (default: 1 day)
            
        Returns:
            List of message IDs matching the criteria
        """
        try:
            # Build search query
            # Look for emails from banks with transaction keywords
            query_parts = [
    # Search by subject only (ignore sender for testing)
    'subject:(Credit transaction alert for Axis Bank)',
    # Only recent emails
    f'newer_than:{days_back}d'
]
            
            query = ' '.join(query_parts)
            
            print(f"🔍 Searching Gmail with query: {query}")
            
            # Execute search
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50  # Limit to 50 emails per check
            ).execute()
            
            messages = results.get('messages', [])
            
            if not messages:
                print("📭 No transaction emails found")
                return []
            
            print(f"📧 Found {len(messages)} transaction emails")
            return messages
            
        except Exception as e:
            print(f"❌ Error searching emails: {str(e)}")
            return []
    
    def get_email_content(self, message_id):
        """
        Get full content of an email
        
        Args:
            message_id: Gmail message ID
            
        Returns:
            Dictionary with email details
        """
        try:
            # Get message details
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extract headers
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')
            
            # Extract body
            body = self._extract_body(message['payload'])
            
            email_data = {
                'message_id': message_id,
                'subject': subject,
                'sender': sender,
                'date': date,
                'body': body,
                'snippet': message.get('snippet', '')
            }
            
            return email_data
            
        except Exception as e:
            print(f"❌ Error fetching email {message_id}: {str(e)}")
            return None
    
    def _extract_body(self, payload):
        """
        Extract email body from payload (handles multipart emails)
        """
        body = ""
        
        if 'parts' in payload:
            # Multipart email
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break
                elif part['mimeType'] == 'text/html':
                    if 'data' in part['body']:
                        # Use HTML if no plain text
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
        else:
            # Simple email
            if 'data' in payload['body']:
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
        
        # Clean HTML tags if present
        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        
        return body
    
    def fetch_new_transactions(self, days_back=1):
        """
        Main method: Search and fetch all transaction emails
        
        Returns:
            List of email data dictionaries
        """
        print("\n" + "="*60)
        print("🚀 Starting Gmail Transaction Monitor")
        print("="*60)
        
        # Search for emails
        message_ids = self.search_transaction_emails(days_back)
        
        if not message_ids:
            return []
        
        # Fetch content for each email
        emails = []
        for i, msg in enumerate(message_ids, 1):
            print(f"\n📩 Fetching email {i}/{len(message_ids)}...")
            email_data = self.get_email_content(msg['id'])
            
            if email_data:
                emails.append(email_data)
                print(f"✅ Subject: {email_data['subject'][:60]}...")
                print(f"   From: {email_data['sender'][:50]}")
        
        print("\n" + "="*60)
        print(f"✅ Successfully fetched {len(emails)} transaction emails")
        print("="*60 + "\n")
        
        return emails


# Test function
if __name__ == "__main__":
    from gmail_auth import GmailAuthenticator
    
    print("Testing Gmail Monitor...")
    
    # Authenticate
    auth = GmailAuthenticator()
    gmail_service = auth.authenticate()
    
    # Create monitor
    monitor = GmailMonitor(gmail_service)
    
    # Fetch transactions from last 7 days
    emails = monitor.fetch_new_transactions(days_back=7)
    
    # Display results
    if emails:
        print("\n📊 TRANSACTION EMAILS FOUND:\n")
        for i, email in enumerate(emails, 1):
            print(f"\n{i}. {email['subject']}")
            print(f"   From: {email['sender']}")
            print(f"   Preview: {email['snippet'][:100]}...")
            print(f"   Body (first 200 chars): {email['body'][:200]}...")
    else:
        print("\n❌ No transaction emails found in the last 7 days")
