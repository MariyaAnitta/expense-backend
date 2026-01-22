import os
import pickle
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service_personal():
    """Authenticate personal Gmail (transaction alerts)"""
    return _get_service(
        token_path='credentials/token.pickle',
        token_env_var='GMAIL_TOKEN_BASE64',
        client_id_env='GMAIL_CLIENT_ID',
        client_secret_env='GMAIL_CLIENT_SECRET',
        account_name='Personal Gmail'
    )

def get_gmail_service_receipts():
    """Authenticate receipts Gmail (forwarded receipts)"""
    return _get_service(
        token_path='credentials/token_receipts.pickle',
        token_env_var='GMAIL_RECEIPTS_TOKEN_BASE64',
        client_id_env='GMAIL_RECEIPTS_CLIENT_ID',
        client_secret_env='GMAIL_RECEIPTS_CLIENT_SECRET',
        account_name='Receipts Gmail'
    )

def _get_service(token_path, token_env_var, client_id_env, client_secret_env, account_name):
    """Generic authentication function"""
    creds = None
    
    # Try environment variable first (for Render deployment)
    token_base64 = os.getenv(token_env_var)
    if token_base64:
        try:
            token_data = base64.b64decode(token_base64)
            creds = pickle.loads(token_data)
            print(f"Loaded {account_name} from {token_env_var}")
        except Exception as e:
            print(f" Failed to load {token_env_var}: {e}")
    
    # Fallback to local token file
    if not creds and os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
        print(f" Loaded {account_name} from {token_path}")
    
    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            print(f" {account_name} token refreshed")
        except Exception as e:
            print(f"{account_name} refresh failed: {e}")
            creds = None
    
    # Re-authenticate if needed (local only)
    if not creds or not creds.valid:
        client_id = os.getenv(client_id_env)
        client_secret = os.getenv(client_secret_env)
        
        if not client_id or not client_secret:
            raise Exception(f"{client_id_env} and {client_secret_env} must be set")
        
        print(f"Authenticating {account_name}...")
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
        
        # Save token
        os.makedirs('credentials', exist_ok=True)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)
        print(f" {account_name} authenticated and saved")
    
    return build('gmail', 'v1', credentials=creds)
