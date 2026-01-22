from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import os

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate():
    creds = None
    
    # Check if token already exists
    if os.path.exists('credentials/token_receipts.pickle'):
        with open('credentials/token_receipts.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials/credentials_receipts.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save credentials for future use
        with open('credentials/token_receipts.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    print("Authentication successful! token_receipts.pickle created.")

if __name__ == '__main__':
    authenticate()
