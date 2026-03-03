import os
import json
import logging
from gemini_receipt_extractor import ReceiptExtractor

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_multimodal_mobility():
    extractor = ReceiptExtractor()
    
    # Test case: Forwarded hotel booking email with attachment
    body_text = "Hey, here is the hotel receipt for the Dubai trip. Please add it to the expenses."
    
    # Assume we have a test image (using the one we just generated)
    test_attachment = "temp/mock_hotel_receipt.png"
    
    logger.info("🧪 Testing multimodal mobility extraction...")
    
    if os.path.exists(test_attachment):
        result = extractor.extract_data_from_document(
            body_text=body_text,
            attachment_paths=[test_attachment]
        )
        
        print("\n📊 EXTRACTED DATA:")
        print(json.dumps(result, indent=2))
        
        # Validation checks
        if result.get('is_mobility'):
            print("\n✅ Mobility data detected!")
            if result.get('mobility_type') == 'accommodation':
                print("✅ Correct type: accommodation")
            if 'Grand Palace' in result.get('merchant_name', '') or 'Grand Palace' in result.get('provider', ''):
                print("✅ Correct provider")
        else:
            print("\n❌ Mobility data NOT detected")
            
        if result.get('cat') == 'Lodging':
            print("✅ Correct Financial category: Lodging")
    else:
        print(f"❌ Test file missing: {test_attachment}")

if __name__ == "__main__":
    test_multimodal_mobility()
