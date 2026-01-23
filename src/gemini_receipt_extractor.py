import os
import json
import base64
from openai import OpenAI
from dotenv import load_dotenv
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ReceiptExtractor:
    """Extracts expense data from receipt images/PDFs using OpenRouter + Gemini Vision"""
    
    def __init__(self):
        # Initialize OpenRouter client (same as your email extractor)
        self.client = OpenAI(
            api_key=os.getenv('OPENROUTER_API_KEY'),
            base_url="https://openrouter.ai/api/v1"
        )
        self.model = os.getenv('OPENROUTER_MODEL', 'google/gemini-2.0-flash-exp')
    
    def encode_image_to_base64(self, file_path: str) -> str:
        """Convert image/PDF to base64 for API"""
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def extract_expense_from_receipt(self, file_path: str) -> dict:
        """
        Extract expense data from receipt image/PDF
        
        Args:
            file_path: Path to the receipt file
            
        Returns:
            dict: Extracted expense data
        """
        try:
            logger.info(f"🔍 Processing receipt: {file_path}")
            
            # Determine file type
            file_extension = file_path.lower().split('.')[-1]
            if file_extension in ['jpg', 'jpeg']:
                mime_type = "image/jpeg"
            elif file_extension == 'png':
                mime_type = "image/png"
            elif file_extension == 'pdf':
                mime_type = "application/pdf"
            else:
                mime_type = "image/jpeg"  # default
            
            # Encode image to base64
            image_base64 = self.encode_image_to_base64(file_path)
            
            # Prompt for receipt extraction
            prompt = """
You are an expert at extracting expense data from receipts. Analyze this receipt image and extract the following information in JSON format:

{
    "merchant_name": "store/restaurant name",
    "date": "YYYY-MM-DD format",
    "total_amount": numeric value only (no currency symbols),
    "currency": "INR/USD/etc",
    "tax_amount": numeric value or null,
    "items": ["item1", "item2", "item3"],
    "category": "Food/Transport/Shopping/Entertainment/Bills/Utilities/Other",
    "payment_method": "Cash/Card/UPI/Unknown"
}

Rules:
- If any field is unclear or missing, use null
- For total_amount, extract the FINAL TOTAL (not subtotal)
- Convert date to YYYY-MM-DD format
- Amount must be numeric only (no ₹, $, commas)
- Guess category based on merchant name and items
- Return ONLY valid JSON, no markdown, no explanation
"""
            
            # Call OpenRouter API with vision
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=800
            )
            
            # Parse response
            result_text = response.choices[0].message.content.strip()
            
            # Clean markdown if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            
            expense_data = json.loads(result_text.strip())
            
            logger.info(f"✅ Extracted: {expense_data.get('merchant_name')} - {expense_data.get('currency')} {expense_data.get('total_amount')}")
            
            # Add metadata
            expense_data['source'] = 'telegram'
            expense_data['file_path'] = file_path
            
            return expense_data
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse AI response as JSON: {e}")
            logger.error(f"Response text: {result_text}")
            return {
                "error": "Failed to parse receipt",
                "merchant_name": "Unknown",
                "total_amount": 0,
                "currency": "INR",
                "source": "telegram",
                "file_path": file_path
            }
        except Exception as e:
            logger.error(f"❌ Error extracting receipt: {e}")
            return {
                "error": str(e),
                "merchant_name": "Unknown",
                "total_amount": 0,
                "currency": "INR",
                "source": "telegram",
                "file_path": file_path
            }

if __name__ == "__main__":
    # Test with the receipt you sent
    extractor = ReceiptExtractor()
    test_file = "temp/receipt_AgACAgUAAxkBAAMDaXMqWNfvHYNJis05YgyzOPJ2bzYAAvANaxtx_phXAAGt4MPxzCP9AQADAgADeQADOAQ.jpg"
    
    if os.path.exists(test_file):
        result = extractor.extract_expense_from_receipt(test_file)
        print("\n📊 Extracted Receipt Data:")
        print(json.dumps(result, indent=2))
    else:
        print(" No test file found. Send a receipt to your Telegram bot first!")
