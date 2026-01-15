import os
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# Gmail API scope - read-only access to emails
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

class GmailAuthenticator:
    """Handles Gmail API authentication using OAuth 2.0"""
    
    def __init__(self):
        self.creds = None
        self.credentials_file = 'credentials/credentials.json'
        self.token_file = 'credentials/token.pickle'
        
    def authenticate(self):
        """
        Authenticate with Gmail API.
        First time: Opens browser for user to authorize.
        Subsequent times: Uses saved token.
        
        Returns:
            Google API service object
        """
        # Check if we have saved credentials
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                self.creds = pickle.load(token)
        
        # If credentials don't exist or are invalid, re-authenticate
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # Refresh expired token
                print("Refreshing expired token...")
                self.creds.refresh(Request())
            else:
                # First time authentication - opens browser
                print("First time authentication - browser will open...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            # Save credentials for next time
            with open(self.token_file, 'wb') as token:
                pickle.dump(self.creds, token)
                print("Credentials saved successfully!")
        
        # Build and return Gmail service
        service = build('gmail', 'v1', credentials=self.creds)
        print("✅ Gmail API authentication successful!")
        return service

# Test function
if __name__ == "__main__":
    print("Testing Gmail Authentication...")
    auth = GmailAuthenticator()
    gmail_service = auth.authenticate()
    
    # Test: Get user's email address
    profile = gmail_service.users().getProfile(userId='me').execute()
    print(f"✅ Connected to: {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']}")
