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
            try:
                from vertexai.generative_models import GenerativeModel, GenerationConfig
            except ImportError:
                # Fallback for older SDK versions or different structures
                from vertexai.preview.generative_models import GenerativeModel, GenerationConfig
            
            # Initialize Vertex AI
            project = os.getenv('VITE_GOOGLE_CLOUD_PROJECT')
            location = os.getenv('VITE_GOOGLE_CLOUD_LOCATION', 'us-east1')
            cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            
            if cred_path and not os.path.exists(cred_path):
                logger.error(f"❌ Credentials file not found at: {cred_path}")
                if os.path.exists('/etc/secrets'):
                    logger.info(f"📁 Contents of /etc/secrets: {os.listdir('/etc/secrets')}")
                else:
                    logger.info("📁 /etc/secrets directory does not exist")
            
            vertexai.init(project=project, location=location)
            
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
                mime_type = "image/jpeg"
            
            # Read file bytes
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            
            # Prompt
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
            
            # Call Gemini Vision
            if self.use_vertex:
                try:
                    from vertexai.generative_models import Part
                except ImportError:
                    from vertexai.preview.generative_models import Part
                
                # Create file part
                file_part = Part.from_data(data=file_bytes, mime_type=mime_type)
                
                response = self.vertex_model.generate_content(
                    [prompt, file_part],
                    generation_config=self.generation_config
                )
                result_text = response.text.strip()
            else:
                response = self.model.generate_content(
                    [
                        prompt,
                        {
                            "mime_type": mime_type,
                            "data": file_bytes
                        }
                    ],
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": 800,
                    }
                )
                result_text = response.text.strip()
            
            # Clean markdown if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            
            expense_data = json.loads(result_text.strip())
            
            logger.info(f"✅ Extracted: {expense_data.get('merchant_name')} - {expense_data.get('currency')} {expense_data.get('total_amount')}")
            
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
    extractor = ReceiptExtractor()
    test_file = "temp/test.jpg"
    
    if os.path.exists(test_file):
        result = extractor.extract_expense_from_receipt(test_file)
        print("\n📊 Extracted Receipt Data:")
        print(json.dumps(result, indent=2))
    else:
        print("No test file found.")