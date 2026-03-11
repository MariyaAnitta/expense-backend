import os
import logging
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from gemini_receipt_extractor import ReceiptExtractor
from firebase_client import FirebaseClient

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Credentials from .env
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WABA_ID = os.getenv("WHATSAPP_WABA_ID")
VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "shared_finance_pool_verify_token")

# Initialize shared components
receipt_extractor = ReceiptExtractor()
firebase_client = FirebaseClient()

app = Flask(__name__)

# --- WHATSAPP API HELPERS ---

def send_whatsapp_message(to, text):
    """Send a simple text message via WhatsApp Cloud API"""
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()

def send_whatsapp_template(to, template_name="hello_world"):
    """Send a template message (required for initiating conversations)"""
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en_US"}
        }
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()

def download_whatsapp_media(media_id):
    """Download media from WhatsApp and return local path"""
    # 1. Get Media URL
    url = f"https://graph.facebook.com/{VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(url, headers=headers)
    media_url = res.json().get("url")
    
    if not media_url:
        return None
        
    # 2. Download Media Content
    res = requests.get(media_url, headers=headers)
    file_path = f"temp/whatsapp_{media_id}.jpg" # Simplified extension
    os.makedirs("temp", exist_ok=True)
    
    with open(file_path, "wb") as f:
        f.write(res.content)
    
    return file_path

# --- WEBHOOK ENDPOINTS ---

@app.route("/whatsapp/webhook", methods=["GET"])
def verify_webhook():
    """Verify webhook with Meta"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ WhatsApp Webhook Verified")
        return challenge, 200
    return "Verification failed", 403

@app.route("/whatsapp/webhook", methods=["POST"])
def handle_whatsapp_message():
    """Handle incoming messages and images"""
    data = request.get_json()
    
    try:
        # WhatsApp sends a complex nested object
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        message = value.get("messages", [{}])[0]
        
        if not message:
            return jsonify({"status": "no message"}), 200
            
        sender_id = message.get("from")
        msg_type = message.get("type")
        
        # --- HANDLE IMAGES (Receipts) ---
        if msg_type == "image":
            image_id = message["image"]["id"]
            send_whatsapp_message(sender_id, "⏳ Processing your receipt...")
            
            # Download and Extract
            file_path = download_whatsapp_media(image_id)
            if file_path:
                expense_data = receipt_extractor.extract_expense_from_receipt(file_path)
                
                if "error" not in expense_data:
                    # FILL IN YOUR DETAILS HERE:
                    # You can add logic to ask questions like Telegram does
                    # For now, we save it directly to the shared pool
                    expense_data["source"] = "whatsapp"
                    expense_data["user_id"] = "SHARED_POOL"
                    
                    firebase_client.save_telegram_receipt(expense_data, telegram_user_id=f"wa_{sender_id}")
                    
                    merchant = expense_data.get('merchant_name', 'Unknown')
                    amount = expense_data.get('total_amount', 0)
                    currency = expense_data.get('currency', 'INR')
                    
                    send_whatsapp_message(sender_id, f"✅ Saved: {merchant} ({currency} {amount}) to Shared Pool.")
                else:
                    send_whatsapp_message(sender_id, "❌ Sorry, I couldn't read that receipt.")
            
        # --- HANDLE TEXT ---
        elif msg_type == "text":
            text = message["text"]["body"].lower()
            # FILL IN YOUR DETAILS HERE: 
            # Handle menu commands or category replies
            send_whatsapp_message(sender_id, "👋 Welcome to ExpenseFlow! Send me a photo of a receipt to get started.")

    except Exception as e:
        logger.error(f"❌ WhatsApp Webhook Error: {e}")
        
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10001))
    app.run(host="0.0.0.0", port=port)
