"""
Debug CSV formatting issue.
"""

import sys
import os
import json

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.response.processor import ResponseProcessor, ResponseFormat
from ai.models.base import ModelResponse


def debug_csv_formatting():
    """Debug CSV formatting issue."""
    processor = ResponseProcessor()
    
    # Test CSV formatting
    list_content = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]
    processed = processor.parse_response(ModelResponse(content=json.dumps(list_content), model_name="test"))
    
    print(f"Processed content: {processed.processed_content}")
    print(f"Content type: {type(processed.processed_content)}")
    
    formatted = processor.format_response(processed, ResponseFormat.CSV)
    print(f"Formatted CSV:\n{formatted}")
    
    # Check what's in the formatted output
    print(f"'name,age' in formatted: {'name,age' in formatted}")
    print(f"'Alice,30' in formatted: {'Alice,30' in formatted}")
    print(f"'Bob,25' in formatted: {'Bob,25' in formatted}")


if __name__ == "__main__":
    debug_csv_formatting()
