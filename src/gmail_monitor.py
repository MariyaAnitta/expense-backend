import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import re


class GmailMonitor:
    """Monitors Gmail for credit card transaction emails"""

    def __init__(self, gmail_service):
        self.service = gmail_service

    def search_transaction_emails(self, after_timestamp=None):
        """
        Search for credit card transaction emails
        """
        try:
            query_parts = [
                'subject:(Credit transaction alert)',
            ]

            if after_timestamp:
                try:
                    if hasattr(after_timestamp, 'timestamp'):
                        dt = datetime.fromtimestamp(after_timestamp.timestamp())
                    else:
                        dt = datetime.now() - timedelta(days=1)

                    date_str = dt.strftime('%Y/%m/%d')
                    query_parts.append(f'after:{date_str}')
                    print(f"Searching emails after: {date_str}")
                except Exception as e:
                    print(f"Error parsing timestamp, using fallback: {e}")
                    query_parts.append('newer_than:1d')
            else:
                query_parts.append('newer_than:7d')
                print("First run: searching last 7 days")

            query = ' '.join(query_parts)
            print(f"Searching Gmail with query: {query}")

            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                print("No transaction emails found")
                return []

            print(f"Found {len(messages)} transaction emails")
            return messages

        except Exception as e:
            print(f"Error searching emails: {str(e)}")
            return []

    def get_email_content(self, message_id):
        """Get full content of an email"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')

            body = self._extract_body(message['payload'])

            return {
                'message_id': message_id,
                'subject': subject,
                'sender': sender,
                'date': date,
                'body': body,
                'snippet': message.get('snippet', '')
            }

        except Exception as e:
            print(f"Error fetching email {message_id}: {str(e)}")
            return None

    def _extract_body(self, payload):
        """Extract email body"""
        body = ""

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                    body = base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
                    break
                elif part['mimeType'] == 'text/html' and 'data' in part['body']:
                    body = base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
        else:
            if 'data' in payload['body']:
                body = base64.urlsafe_b64decode(
                    payload['body']['data']
                ).decode('utf-8')

        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        return body

    def fetch_new_transactions(self, after_timestamp=None):
        print("\n" + "=" * 60)
        print("Starting Gmail Transaction Monitor")
        print("=" * 60)

        message_ids = self.search_transaction_emails(after_timestamp)
        if not message_ids:
            return []

        emails = []
        for i, msg in enumerate(message_ids, 1):
            print(f"\nFetching email {i}/{len(message_ids)}...")
            email_data = self.get_email_content(msg['id'])
            if email_data:
                emails.append(email_data)
                print(f"Subject: {email_data['subject'][:60]}...")
                print(f"From: {email_data['sender'][:50]}")

        print("\n" + "=" * 60)
        print(f"Successfully fetched {len(emails)} transaction emails")
        print("=" * 60 + "\n")

        return emails


# ============================================================
# NEW: RECEIPT EMAIL MONITOR
# ============================================================

class ReceiptEmailMonitor:
    """Monitors receipts Gmail for forwarded booking/invoice emails"""

    def __init__(self, gmail_service):
        self.service = gmail_service

    def search_receipt_emails(self, after_timestamp=None):
        """
        Search for ALL emails in receipts inbox
        Every email = receipt
        """
        try:
            query_parts = []

            if after_timestamp:
                try:
                    if hasattr(after_timestamp, 'timestamp'):
                        dt = datetime.fromtimestamp(after_timestamp.timestamp())
                    else:
                        dt = datetime.now() - timedelta(days=1)

                    date_str = dt.strftime('%Y/%m/%d')
                    query_parts.append(f'after:{date_str}')
                    print(f"Searching receipt emails after: {date_str}")
                except Exception as e:
                    print(f"Error parsing timestamp: {e}")
                    query_parts.append('newer_than:1d')
            else:
                query_parts.append('newer_than:30d')
                print("First run: searching last 30 days of receipts")

            query = ' '.join(query_parts) if query_parts else 'in:inbox'
            print(f"Searching receipts Gmail: {query}")

            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                print("No new receipt emails found")
                return []

            print(f"Found {len(messages)} receipt emails")
            return messages

        except Exception as e:
            print(f"Error searching receipt emails: {str(e)}")
            return []

    def get_email_content(self, message_id):
        """Get full content of receipt email, including attachments"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')

            body = self._extract_body(message['payload'])
            
            # Extract attachments
            attachments = self._get_attachments(message_id, message['payload'])

            return {
                'message_id': message_id,
                'subject': subject,
                'sender': sender,
                'date': date,
                'body': body,
                'snippet': message.get('snippet', ''),
                'attachments': attachments
            }

        except Exception as e:
            print(f"Error fetching receipt email {message_id}: {str(e)}")
            return None

    def _get_attachments(self, message_id, payload):
        """Extract and download attachments from email payload"""
        attachments = []
        if 'parts' in payload:
            for part in payload['parts']:
                if 'filename' in part and part['filename']:
                    mime_type = part.get('mimeType', '')
                    # We only care about images and PDFs
                    if any(t in mime_type for t in ['image', 'pdf']):
                        attachment_id = part['body'].get('attachmentId')
                        if attachment_id:
                            file_path = self._download_attachment(message_id, attachment_id, part['filename'])
                            if file_path:
                                attachments.append({
                                    'path': file_path,
                                    'filename': part['filename'],
                                    'mime_type': mime_type
                                })
        return attachments

    def _download_attachment(self, message_id, attachment_id, filename):
        """Download a single attachment to temp folder"""
        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=attachment_id
            ).execute()
            
            data = base64.urlsafe_b64decode(attachment['data'])
            
            # Ensure temp directory exists
            os.makedirs('temp', exist_ok=True)
            
            # Clean filename to avoid path traversal
            safe_filename = "".join([c for c in filename if c.isalnum() or c in ('.','_','-')]).strip()
            # Add message_id to ensure uniqueness
            path = os.path.join('temp', f"{message_id}_{safe_filename}")
            
            with open(path, 'wb') as f:
                f.write(data)
            
            print(f"📎 Downloaded attachment: {filename}")
            return path
        except Exception as e:
            print(f"Failed to download attachment {filename}: {e}")
            return None

    def _extract_body(self, payload):
        """Extract email body"""
        body = ""

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                    body = base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
                    break
                elif part['mimeType'] == 'text/html' and 'data' in part['body']:
                    body = base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
        else:
            if 'data' in payload['body']:
                body = base64.urlsafe_b64decode(
                    payload['body']['data']
                ).decode('utf-8')

        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        return body

    def fetch_new_receipts(self, after_timestamp=None):
        print("\n" + "=" * 60)
        print("Starting Receipt Email Monitor")
        print("=" * 60)

        message_ids = self.search_receipt_emails(after_timestamp)
        if not message_ids:
            return []

        emails = []
        for i, msg in enumerate(message_ids, 1):
            print(f"\nFetching receipt {i}/{len(message_ids)}...")
            email_data = self.get_email_content(msg['id'])
            if email_data:
                emails.append(email_data)
                print(f"Subject: {email_data['subject'][:60]}...")
                print(f"From: {email_data['sender'][:50]}")

        print("\n" + "=" * 60)
        print(f"Successfully fetched {len(emails)} receipt emails")
        print("=" * 60 + "\n")

        return emails