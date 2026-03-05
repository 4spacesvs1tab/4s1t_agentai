"""
Debug data extraction issue.
"""

import sys
import os
import json

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.response.processor import ResponseProcessor
from ai.models.base import ModelResponse


def debug_data_extraction():
    """Debug data extraction issue."""
    processor = ResponseProcessor()
    
    # Create a response with extractable data
    response_content = """
    Product: SuperWidget Pro
    Price: $99.99
    Rating: 4.5 stars
    Description: The best widget ever made.
    """
    
    processed = processor.parse_response(ModelResponse(content=response_content, model_name="test"))
    print(f"Original content: {repr(response_content)}")
    
    # Define extraction rules
    extraction_rules = {
        "product": r"Product:\s*(.+)",
        "price": r"Price:\s*(\$[\d.]+)",
        "rating": r"Rating:\s*([\d.]+)"
    }
    
    print(f"Extraction rules: {extraction_rules}")
    
    # Extract data
    processed = processor.extract_data(processed, extraction_rules)
    
    print(f"Extracted data: {processed.extracted_data}")
    
    # Check each extraction individually
    import re
    for field_name, pattern in extraction_rules.items():
        match = re.search(pattern, response_content, re.DOTALL | re.IGNORECASE)
        if match:
            if match.groups():
                value = match.group(1).strip()
            else:
                value = match.group().strip()
            print(f"{field_name}: matched '{value}'")
        else:
            print(f"{field_name}: no match")


if __name__ == "__main__":
    debug_data_extraction()
