import os
import json
import base64
import logging
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ReceiptExtractor:
    """Extracts expense data from receipt images/PDFs using Google Gemini Vision"""
    
    def __init__(self):
        # Configuration
        self.use_vertex = os.getenv('VITE_GOOGLE_GENAI_USE_VERTEXAI', 'false').lower() == 'true'
        self.model_name = os.getenv('VITE_MODEL', 'gemini-2.0-flash')
        self.temperature = float(os.getenv('VITE_TEMPERATURE', 0.1))
        
        if self.use_vertex:
            import vertexai
            import google.auth
            from google.oauth2 import service_account
            
            try:
                from vertexai.generative_models import GenerativeModel, GenerationConfig
            except ImportError:
                from vertexai.preview.generative_models import GenerativeModel, GenerationConfig
            
            # Initialize Vertex AI
            project = os.getenv('VITE_GOOGLE_CLOUD_PROJECT')
            location = os.getenv('VITE_GOOGLE_CLOUD_LOCATION', 'us-east1')
            cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            cred_json_str = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            
            credentials = None
            if cred_path and os.path.exists(cred_path):
                logger.info(f"📍 Loading Vertex credentials from file: {cred_path}")
                # vertexai.init will automatically find it via env var
            elif cred_json_str:
                logger.info("📍 Loading Vertex credentials from environment variable JSON")
                cred_dict = json.loads(cred_json_str)
                credentials = service_account.Credentials.from_service_account_info(cred_dict)
            else:
                logger.warning("⚠️ No explicit Vertex credentials found. Relying on default environment.")
            
            vertexai.init(project=project, location=location, credentials=credentials)
            
            self.vertex_model = GenerativeModel(self.model_name)
            self.generation_config = GenerationConfig(
                temperature=self.temperature,
                top_p=float(os.getenv('VITE_TOP_P', 0.95)),
                top_k=int(os.getenv('VITE_TOP_K', 40)),
                max_output_tokens=800,
            )
            logger.info(f"🤖 Initialized Vertex AI for Receipts with model {self.model_name}")
        else:
            # Initialize Google Gemini client (AI Studio - Legacy)
            genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"🤖 Initialized Google AI Studio for Receipts with model {self.model_name}")
    
    def encode_image_to_base64(self, file_path: str) -> str:
        """Convert image/PDF to base64 for API"""
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def extract_data_from_document(self, body_text: str = None, attachment_paths: list = None) -> dict:
        """
        Extract expense and travel data from email body and/or attachments.
        
        Args:
            body_text: The text content of the email
            attachment_paths: List of local paths to downloaded attachments
            
        Returns:
            dict: Extracted data containing both Financial and Mobility ledgers
        """
        try:
            logger.info(f"🔍 Analyzing document: {len(attachment_paths) if attachment_paths else 0} attachments found")
            
            # Prompt
            prompt = """
You are an expert at financial and travel data extraction. Analyze the provided text and/or files (PDFs/Images) and extract data for TWO ledgers:

1. THE FINANCIAL LEDGER (Expenses)
Extract the following fields in JSON format:
{
    "merchant_name": "store/airline/hotel/description",
    "total_amount": numeric value only,
    "currency": "3-letter code (AED, USD, INR, etc.)",
    "date": "YYYY-MM-DD",
    "cat": "Transport/Meals/Lodging/Office/Utilities/Salary/Transfer/General",
    "items": ["list of items bought"],
    "tax_amount": numeric tax value or null,
    "main_category": "Business or Personal",
    "payment_method": "Card/Cash/UPI/etc"
}

2. THE MOBILITY LEDGER (Travel Logs)
ONLY if the document is a flight ticket, hotel booking, or visa, ALSO extract:
{
    "is_mobility": true,
    "mobility_type": "flight or accommodation",
    "provider": "Airline name or Hotel name",
    "destination": "City, Country",
    "end_date": "Check-out or Return flight date (YYYY-MM-DD)",
    "pnr": "PNR number or Booking ID",
    "guest_name": "Name of the traveler/guest"
}

Categorization Rules:
- "Lodging": Hotels, stays, room charges.
- "Transport": Flights (ALWAYS), taxis, trains, fuel, parking.
- "Meals": Restaurants, cafes, food delivery.
- "Utilities": Phone, internet, electricity.
- "General": Anything else that doesn't fit.

Context Rule:
- Use all available context (text + images) to fill missing fields.
- If the document mentions a hotel stay, prioritize "Lodging" even if food is listed.
- If it's a flight, it's ALWAYS "Transport".

Return ONLY a single valid JSON object combining both. If no mobility data is found, "is_mobility" should be false.
"""
            
            # Combine parts for Gemini
            parts = [prompt]
            if body_text:
                parts.append(f"EMAIL BODY CONTENT:\n{body_text}")
            
            # Prepare attachments
            if attachment_paths:
                for path in attachment_paths:
                    mime_type = self._get_mime_type(path)
                    with open(path, "rb") as f:
                        file_bytes = f.read()
                    
                    if self.use_vertex:
                        try:
                            from vertexai.generative_models import Part
                        except ImportError:
                            from vertexai.preview.generative_models import Part
                        parts.append(Part.from_data(data=file_bytes, mime_type=mime_type))
                    else:
                        parts.append({"mime_type": mime_type, "data": file_bytes})

            # Call Gemini
            if self.use_vertex:
                response = self.vertex_model.generate_content(
                    parts,
                    generation_config=self.generation_config
                )
                result_text = response.text.strip()
            else:
                response = self.model.generate_content(
                    parts,
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": 1000,
                    }
                )
                result_text = response.text.strip()
            
            # Clean markdown
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            
            data = json.loads(result_text.strip())
            
            logger.info(f"✅ Extracted: {data.get('merchant_name')} - {data.get('currency')} {data.get('total_amount')}")
            return data
            
        except Exception as e:
            logger.error(f"❌ Error in advanced extraction: {e}")
            return {"error": str(e)}

    def _get_mime_type(self, file_path: str) -> str:
        file_extension = file_path.lower().split('.')[-1]
        if file_extension in ['jpg', 'jpeg']: return "image/jpeg"
        if file_extension == 'png': return "image/png"
        if file_extension == 'pdf': return "application/pdf"
        return "image/jpeg"

    def extract_expense_from_receipt(self, file_path: str) -> dict:
        """Old method kept for backward compatibility with telegram_bot.py"""
        return self.extract_data_from_document(attachment_paths=[file_path])

if __name__ == "__main__":
    extractor = ReceiptExtractor()
    test_file = "temp/test.jpg"
    
    if os.path.exists(test_file):
        result = extractor.extract_expense_from_receipt(test_file)
        print("\n📊 Extracted Receipt Data:")
        print(json.dumps(result, indent=2))
    else:
        print("No test file found.")