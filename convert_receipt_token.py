import pickle
import base64

# Read the token file
with open('credentials/token_receipts.pickle', 'rb') as f:
    token_data = f.read()

# Convert to base64
token_base64 = base64.b64encode(token_data).decode('utf-8')

# Print it
print("\n" + "="*70)
print("GMAIL_RECEIPTS_TOKEN_BASE64")
print("="*70)
print(token_base64)
print("="*70)
print("\nCopy this value and save it - you'll add it to Render next")
