import os
import pickle
import base64
from dotenv import load_dotenv
from gmail_auth_dual import get_gmail_service_personal, get_gmail_service_receipts

load_dotenv()

def generate_and_print_tokens():
    print("=" * 50)
    print("GOOGLE OAUTH TOKEN GENERATOR")
    print("=" * 50)
    
    # 1. Personal Account
    print("\n[1/2] Authenticating Personal Gmail...")
    try:
        get_gmail_service_personal()
        token_path = 'credentials/token.pickle'
        if os.path.exists(token_path):
            with open(token_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
                print(f"✅ Success! Local token saved to {token_path}")
                print(f"\nCOPY THIS FOR RENDER (GMAIL_TOKEN_BASE64):")
                print("-" * 20)
                print(b64)
                print("-" * 20)
    except Exception as e:
        print(f"❌ Failed: {e}")

    # 2. Receipts Account
    print("\n[2/2] Authenticating Receipts Gmail...")
    try:
        get_gmail_service_receipts()
        token_path = 'credentials/token_receipts.pickle'
        if os.path.exists(token_path):
            with open(token_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
                print(f"✅ Success! Local token saved to {token_path}")
                print(f"\nCOPY THIS FOR RENDER (GMAIL_RECEIPTS_TOKEN_BASE64):")
                print("-" * 20)
                print(b64)
                print("-" * 20)
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    # Ensure credentials directory exists
    os.makedirs('credentials', exist_ok=True)
    generate_and_print_tokens()
