import os
import json
import base64
import logging
import re
import pickle
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger('ExpenseMonitor')


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

        # Patterns don't need to change, but they will work now because we haven't stripped < > yet
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
            # VERIFY THE ACCOUNT
            try:
                profile = self.service.users().getProfile(userId='me').execute()
                email_addr = profile.get('emailAddress')
                logger.info(f"📧 AUTHENTICATED AS: {email_addr}")
            except Exception as e:
                logger.warning(f"⚠️ Could not verify email address: {e}")

            # Simplified query: look for anything in the inbox from the last 2 days
            # We filter for duplicates using logic in main.py / firebase_client.py
            # This avoids complex date boundary issues.
            query = "in:inbox newer_than:2d"
            logger.info(f"Final Receipts Search Query: {query}")

            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()

            messages = results.get('messages', [])
            logger.info(f"Gmail Search Results: {len(messages)} messages found.")
            
            # If no messages found, search WITHOUT filters just once to see if account has ANY mail
            if not messages:
                debug_results = self.service.users().messages().list(userId='me', maxResults=5).execute()
                debug_count = len(debug_results.get('messages', []))
                logger.info(f"🔍 DEBUG: Any mail in account? {debug_count} found (unfiltered)")

            if not messages:
                logger.info("No new receipt emails found")
                return []
 
            logger.info(f"Found {len(messages)} receipt emails")
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

            # Get raw body for extraction
            body = self._extract_body(message['payload'])
            
            # Extract attachments
            attachments = self._get_attachments(message_id, message['payload'])

            # Extract original sender if it's a forwarded email
            # IMPORTANT: Extract BEFORE cleaning/stripping tags
            forwarded_from = self._extract_forwarded_from(body)

            # Now clean the body for AI processing
            cleaned_body = re.sub(r'<[^>]+>', ' ', body)
            cleaned_body = re.sub(r'\s+', ' ', cleaned_body).strip()

            return {
                'message_id': message_id,
                'subject': subject,
                'sender': sender,
                'forwarded_from': forwarded_from,
                'date': date,
                'body': cleaned_body,
                'snippet': message.get('snippet', ''),
                'attachments': attachments
            }

        except Exception as e:
            print(f"Error fetching receipt email {message_id}: {str(e)}")
            return None

    def _get_attachments(self, message_id, payload):
        """Recursively extract and download attachments from email payload"""
        attachments = []
        
        mime_type = payload.get('mimeType', 'unknown')
        filename = payload.get('filename', '')
        part_id = payload.get('partId', '0')
        
        # Log structure for debugging
        logger.info(f"  [DEBUG] Email Part {part_id}: Mime={mime_type}, Filename='{filename}'")

        # Check for nested parts FIRST
        if 'parts' in payload:
            for part in payload['parts']:
                attachments.extend(self._get_attachments(message_id, part))
        
        # Then check if CURRENT part is a useful attachment
        if filename:
            # We care about images and PDFs
            is_valid_type = any(t in mime_type.lower() for t in ['image', 'pdf'])
            
            # Special case: octet-stream might be a PDF/Image if filename says so
            if not is_valid_type and 'octet-stream' in mime_type.lower():
                if any(ext in filename.lower() for ext in ['.pdf', '.jpg', '.jpeg', '.png']):
                    is_valid_type = True
                    logger.info(f"  [DEBUG] Found octet-stream attachment that looks like a valid file: {filename}")

            if is_valid_type:
                attachment_id = payload.get('body', {}).get('attachmentId')
                if attachment_id:
                    file_path = self._download_attachment(message_id, attachment_id, filename)
                    if file_path:
                        attachments.append({
                            'path': file_path,
                            'filename': filename,
                            'mime_type': mime_type
                        })
                else:
                    logger.warning(f"  ⚠️ Part has filename '{filename}' but no attachmentId in body")
        
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
            
            return path
        except Exception as e:
            logger.error(f"Failed to download attachment {filename}: {e}")
            return None

    def _extract_body(self, payload):
        """Recursively extract email body"""
        body = ""

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                    body += base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
                elif part['mimeType'] == 'text/html' and 'data' in part['body']:
                    # Use HTML if plain text isn't available or alongside it
                    html_content = base64.urlsafe_b64decode(
                        part['body']['data']
                    ).decode('utf-8')
                    # Very basic HTML cleaning
                    body += re.sub(r'<[^>]+>', ' ', html_content)
                elif 'parts' in part:
                    # Handle nested multi-part
                    body += self._extract_body(part)
        else:
            if 'data' in payload['body']:
                body = base64.urlsafe_b64decode(
                    payload['body']['data']
                ).decode('utf-8')

        body = re.sub(r'\s+', ' ', body).strip()
        return body

    def _extract_forwarded_from(self, body):
        """
        Extract the original sender's email address from a forwarded message body.
        Look for patterns like "From: Name <email@example.com>" or "From: email@example.com"
        """
        if not body:
            return None
            
        # Common patterns for forwarded messages:
        # 1. From: Name <email@example.com>
        # 2. From: email@example.com
        # 3. ---------- Forwarded message --------- From: ...
        
        # Regular expression to find "From:" followed by an email address
        # We look for the FIRST occurrence of "From:" which usually represents the most recent forward
        forward_patterns = [
            r"From:\s*.*<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>", # From: Name <email>
            r"From:\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"      # From: email
        ]
        
        for pattern in forward_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None

    def fetch_new_receipts(self, after_timestamp=None):
        print("\n" + "=" * 60)
        print("Starting Receipt Email Monitor")
        print("=" * 60)

        message_ids = self.search_receipt_emails(after_timestamp)
        if not message_ids:
            return []

        emails = []
        for i, msg in enumerate(message_ids, 1):
            logger.info(f"\nFetching receipt {i}/{len(message_ids)}...")
            email_data = self.get_email_content(msg['id'])
            if email_data:
                emails.append(email_data)
                logger.info(f"Subject: {email_data['subject'][:60]}...")
                logger.info(f"From: {email_data['sender'][:50]}")

        print("\n" + "=" * 60)
        print(f"Successfully fetched {len(emails)} receipt emails")
        print("=" * 60 + "\n")

        return emails