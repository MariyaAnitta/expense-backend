import pickle
import base64

# Personal Gmail Token
#with open('credentials/token.pickle', 'rb') as f:
  #  token1 = base64.b64encode(f.read()).decode('utf-8')

#Receipts Gmail Token  
with open('credentials/token_receipts.pickle', 'rb') as f:
    token2 = base64.b64encode(f.read()).decode('utf-8')

#print("=== GMAIL_TOKEN_BASE64 (Personal) ===")
#print(token1)
print("\n=== GMAIL_RECEIPTS_TOKEN_BASE64 (Receipts) ===")
print(token2)
