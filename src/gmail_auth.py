import os
import pickle
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Authenticate and return Gmail API service"""
    from googleapiclient.discovery import build
    
    creds = None
    
    # Try to load token from base64 environment variable (for Render)
    token_base64 = os.getenv('GMAIL_TOKEN_BASE64')
    if token_base64:
        try:
            token_data = base64.b64decode(token_base64)
            creds = pickle.loads(token_data)
            print("✅ Loaded credentials from GMAIL_TOKEN_BASE64")
        except Exception as e:
            print(f"⚠️ Failed to load GMAIL_TOKEN_BASE64: {e}")
    
    # Fallback: Try to load from local token.pickle file
    if not creds and os.path.exists('credentials/token.pickle'):
        with open('credentials/token.pickle', 'rb') as token:
            creds = pickle.load(token)
            print("✅ Loaded credentials from local token.pickle")
    
    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            print("✅ Token refreshed successfully")
        except Exception as e:
            print(f"⚠️ Token refresh failed: {e}")
            creds = None
    
    # If no valid credentials, authenticate with OAuth
    if not creds or not creds.valid:
        client_id = os.getenv('GMAIL_CLIENT_ID')
        client_secret = os.getenv('GMAIL_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise Exception("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET environment variables must be set")
        
        print("First time authentication - browser will open...")
        
        # Create credentials dict
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"]
            }
        }
        
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
        
        # Save credentials locally
        os.makedirs('credentials', exist_ok=True)
        with open('credentials/token.pickle', 'wb') as token:
            pickle.dump(creds, token)
        
        print("✅ Authentication successful! Token saved locally.")
        print("⚠️ IMPORTANT: Convert token.pickle to base64 and add to GMAIL_TOKEN_BASE64 for production")
    
    return build('gmail', 'v1', credentials=creds)
