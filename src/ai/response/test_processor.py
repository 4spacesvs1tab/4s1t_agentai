"""
Tests for the response processing system.
"""

import sys
import os
import json

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.response.processor import (
    ResponseProcessor, 
    ResponseValidationRule, 
    ResponseFormat, 
    ValidationResult,
    initialize_default_rules
)
from ai.models.base import ModelResponse


def test_format_detection():
    """Test response format detection."""
    processor = ResponseProcessor()
    
    # Test JSON detection
    json_content = '{"key": "value", "number": 42}'
    detected = processor.detect_format(json_content)
    assert detected == ResponseFormat.JSON
    
    # Test JSON array
    json_array = '[{"item": 1}, {"item": 2}]'
    detected = processor.detect_format(json_array)
    assert detected == ResponseFormat.JSON
    
    # Test XML detection
    xml_content = '<root><item>value</item></root>'
    detected = processor.detect_format(xml_content)
    assert detected == ResponseFormat.XML
    
    # Test CSV detection
    csv_content = 'name,age\nAlice,30\nBob,25'
    detected = processor.detect_format(csv_content)
    assert detected == ResponseFormat.CSV
    
    # Test text detection (default)
    text_content = "This is plain text content."
    detected = processor.detect_format(text_content)
    assert detected == ResponseFormat.TEXT
    
    print("✓ Format detection test passed")


def test_response_parsing():
    """Test response parsing functionality."""
    processor = ResponseProcessor()
    
    # Test JSON parsing
    json_response = ModelResponse(
        content='{"answer": "42", "explanation": "The answer"}',
        model_name="test-model"
    )
    
    processed = processor.parse_response(json_response)
    assert processed.format_detected == ResponseFormat.JSON
    assert isinstance(processed.processed_content, dict)
    assert processed.processed_content["answer"] == "42"
    assert "answer" in processed.extracted_data
    
    # Test XML parsing
    xml_response = ModelResponse(
        content='<response><answer>42</answer><explanation>The answer</explanation></response>',
        model_name="test-model"
    )
    
    processed = processor.parse_response(xml_response)
    assert processed.format_detected == ResponseFormat.XML
    
    # Test text parsing
    text_response = ModelResponse(
        content="This is a simple text response.",
        model_name="test-model"
    )
    
    processed = processor.parse_response(text_response)
    assert processed.format_detected == ResponseFormat.TEXT
    assert isinstance(processed.processed_content, str)
    
    print("✓ Response parsing test passed")


def test_validation_rules():
    """Test validation rule functionality."""
    processor = ResponseProcessor()
    
    # Create validation rules
    length_rule = ResponseValidationRule(
        name="length_check",
        min_length=10,
        max_length=100
    )
    
    keyword_rule = ResponseValidationRule(
        name="keyword_check",
        required_keywords=["important"],
        forbidden_keywords=["forbidden"]
    )
    
    pattern_rule = ResponseValidationRule(
        name="pattern_check",
        required_patterns=[r'\d+']  # Must contain digits
    )
    
    # Register rules
    assert processor.register_validation_rule(length_rule) == True
    assert processor.register_validation_rule(keyword_rule) == True
    assert processor.register_validation_rule(pattern_rule) == True
    
    # Check rules are registered
    assert "length_check" in processor.validation_rules
    assert "keyword_check" in processor.validation_rules
    assert "pattern_check" in processor.validation_rules
    
    print("✓ Validation rules test passed")


def test_response_validation():
    """Test response validation functionality."""
    processor = ResponseProcessor()
    initialize_default_rules(processor)
    
    # Create a response that should pass validation
    good_response = ModelResponse(
        content='{"result": "success", "message": "Operation completed successfully"}',
        model_name="test-model"
    )
    
    processed = processor.parse_response(good_response)
    
    # Validate with structured output rule
    rules = [processor.validation_rules["structured_output"]]
    validated = processor.validate_response(processed, rules)
    
    # Should pass validation
    assert ValidationResult.INVALID_FORMAT not in validated.validation_results
    assert validated.confidence_score >= 0.5  # Adjusted threshold
    
    # Create a response that should fail validation
    bad_response = ModelResponse(
        content="This response is too short and lacks structure",
        model_name="test-model"
    )
    
    processed = processor.parse_response(bad_response)
    validated = processor.validate_response(processed, rules)
    
    # Should fail validation
    assert len(validated.validation_results) > 0
    assert validated.confidence_score < 0.5
    
    print("✓ Response validation test passed")


def test_response_formatting():
    """Test response formatting functionality."""
    processor = ResponseProcessor()
    
    # Test JSON formatting
    dict_content = {"name": "Alice", "age": 30}
    processed = processor.parse_response(ModelResponse(content=json.dumps(dict_content), model_name="test"))
    
    formatted = processor.format_response(processed, ResponseFormat.JSON)
    assert '"name": "Alice"' in formatted
    assert '"age": 30' in formatted
    
    # Test XML formatting
    formatted = processor.format_response(processed, ResponseFormat.XML)
    assert "<name>Alice</name>" in formatted
    assert "<age>30</age>" in formatted
    
    # Test CSV formatting
    list_content = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]
    processed = processor.parse_response(ModelResponse(content=json.dumps(list_content), model_name="test"))
    
    formatted = processor.format_response(processed, ResponseFormat.CSV)
    # Check that headers are present (order may vary)
    assert "name" in formatted
    assert "age" in formatted
    # Check that data is present
    assert "Alice" in formatted
    assert "30" in formatted
    assert "Bob" in formatted
    assert "25" in formatted
    
    print("✓ Response formatting test passed")


def test_data_extraction():
    """Test data extraction functionality."""
    processor = ResponseProcessor()
    
    # Create a response with extractable data
    response_content = """
    Product: SuperWidget Pro
    Price: $99.99
    Rating: 4.5 stars
    Description: The best widget ever made.
    """
    
    processed = processor.parse_response(ModelResponse(content=response_content, model_name="test"))
    
    # Define extraction rules
    extraction_rules = {
        "product": r"Product:\s*(.+?)\n",
        "price": r"Price:\s*(\$[\d.]+)",
        "rating": r"Rating:\s*([\d.]+)"
    }
    
    # Extract data
    processed = processor.extract_data(processed, extraction_rules)
    
    # Check extracted data
    assert "product" in processed.extracted_data
    assert "price" in processed.extracted_data
    assert "rating" in processed.extracted_data
    assert processed.extracted_data["product"] == "SuperWidget Pro"
    assert processed.extracted_data["price"] == "$99.99"
    assert processed.extracted_data["rating"] == "4.5"
    
    print("✓ Data extraction test passed")


def test_default_rules_initialization():
    """Test initialization of default validation rules."""
    processor = ResponseProcessor()
    
    # Initialize default rules
    assert initialize_default_rules(processor) == True
    
    # Check that rules were added
    assert "basic_safety" in processor.validation_rules
    assert "structured_output" in processor.validation_rules
    assert "concise_response" in processor.validation_rules
    
    # Check rule properties
    safety_rule = processor.validation_rules["basic_safety"]
    assert safety_rule.max_length == 1000
    assert "violence" in safety_rule.forbidden_keywords
    
    print("✓ Default rules initialization test passed")


if __name__ == "__main__":
    # Run all tests
    test_format_detection()
    test_response_parsing()
    test_validation_rules()
    test_response_validation()
    test_response_formatting()
    test_data_extraction()
    test_default_rules_initialization()
    print("\n🎉 All response processor tests passed!")
