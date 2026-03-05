"""
Response processing and validation system for the 4S1T Agent AI framework.

This module provides functionality for parsing, validating, formatting, and handling
AI model responses, including fallback mechanisms for error cases.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union, Callable
from datetime import datetime
import json
import re
import logging
import xml.etree.ElementTree as ET
from enum import Enum

from ..models.base import ModelResponse

logger = logging.getLogger(__name__)


class ResponseFormat(Enum):
    """Enumeration of supported response formats."""
    TEXT = "text"
    JSON = "json"
    XML = "xml"
    MARKDOWN = "markdown"
    CSV = "csv"
    YAML = "yaml"


class ValidationResult(Enum):
    """Enumeration of validation results."""
    VALID = "valid"
    INVALID_FORMAT = "invalid_format"
    INVALID_CONTENT = "invalid_content"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    MISSING_REQUIRED = "missing_required"
    CONTAINS_FORBIDDEN = "contains_forbidden"


@dataclass
class ResponseValidationRule:
    """Rule for validating AI responses."""
    
    name: str
    description: str = ""
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    required_keywords: List[str] = field(default_factory=list)
    forbidden_keywords: List[str] = field(default_factory=list)
    required_patterns: List[str] = field(default_factory=list)
    forbidden_patterns: List[str] = field(default_factory=list)
    format_type: Optional[ResponseFormat] = None
    custom_validator: Optional[Callable[[str], bool]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessedResponse:
    """Processed and validated AI response."""
    
    original_response: ModelResponse
    processed_content: Union[str, Dict[str, Any], List[Any]]
    format_detected: ResponseFormat
    validation_results: List[ValidationResult] = field(default_factory=list)
    extracted_data: Dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.0
    processing_timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ResponseProcessor:
    """
    Processor for AI model responses in the 4S1T Agent AI framework.
    
    This class provides functionality for parsing, validating, formatting,
    and extracting data from AI model responses.
    """
    
    def __init__(self):
        """Initialize the response processor."""
        self.validation_rules: Dict[str, ResponseValidationRule] = {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def register_validation_rule(self, rule: ResponseValidationRule) -> bool:
        """
        Register a validation rule.
        
        Args:
            rule: The validation rule to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.validation_rules[rule.name] = rule
            self.logger.info(f"Registered validation rule: {rule.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register validation rule {rule.name}: {e}")
            return False
    
    def detect_format(self, content: str) -> ResponseFormat:
        """
        Detect the format of response content.
        
        Args:
            content: Response content to analyze
            
        Returns:
            ResponseFormat: Detected format
        """
        content_stripped = content.strip()
        
        # Check for JSON
        if content_stripped.startswith('{') and content_stripped.endswith('}'):
            try:
                json.loads(content_stripped)
                return ResponseFormat.JSON
            except json.JSONDecodeError:
                pass
        
        if content_stripped.startswith('[') and content_stripped.endswith(']'):
            try:
                json.loads(content_stripped)
                return ResponseFormat.JSON
            except json.JSONDecodeError:
                pass
        
        # Check for XML
        if content_stripped.startswith('<') and content_stripped.endswith('>'):
            try:
                ET.fromstring(content_stripped)
                return ResponseFormat.XML
            except ET.ParseError:
                pass
        
        # Check for CSV (simple heuristic)
        if ',' in content_stripped and '\n' in content_stripped:
            lines = content_stripped.split('\n')
            if len(lines) > 1:
                # Check if first few lines have same number of commas
                comma_counts = [line.count(',') for line in lines[:3]]
                if len(set(comma_counts)) <= 1:
                    return ResponseFormat.CSV
        
        # Check for Markdown (headers, lists, code blocks)
        markdown_patterns = [r'^#{1,6}\s', r'^\s*[*\-]\s', r'`{3}.*`{3}', r'\*\*.*\*\*']
        if any(re.search(pattern, content_stripped, re.MULTILINE) for pattern in markdown_patterns):
            return ResponseFormat.MARKDOWN
        
        # Default to text
        return ResponseFormat.TEXT
    
    def parse_response(self, response: ModelResponse) -> ProcessedResponse:
        """
        Parse and process an AI model response.
        
        Args:
            response: The model response to process
            
        Returns:
            ProcessedResponse: Parsed and processed response
        """
        try:
            # Get content
            content = response.content
            if not isinstance(content, str):
                content = str(content)
            
            # Detect format
            detected_format = self.detect_format(content)
            
            # Parse based on format
            parsed_content: Union[str, Dict[str, Any], List[Any]] = content
            extracted_data: Dict[str, Any] = {}
            
            if detected_format == ResponseFormat.JSON:
                try:
                    parsed_content = json.loads(content)
                    # Extract basic data for convenience
                    if isinstance(parsed_content, dict):
                        extracted_data.update(parsed_content)
                    elif isinstance(parsed_content, list):
                        extracted_data["items"] = parsed_content
                except json.JSONDecodeError as e:
                    self.logger.warning(f"JSON parsing failed: {e}")
                    parsed_content = content
            
            elif detected_format == ResponseFormat.XML:
                try:
                    root = ET.fromstring(content)
                    # Convert XML to dict-like structure
                    parsed_content = self._xml_to_dict(root)
                    extracted_data.update(parsed_content)
                except ET.ParseError as e:
                    self.logger.warning(f"XML parsing failed: {e}")
                    parsed_content = content
            
            elif detected_format == ResponseFormat.CSV:
                try:
                    lines = content.strip().split('\n')
                    if lines:
                        # Simple CSV parsing (doesn't handle quoted fields)
                        headers = lines[0].split(',')
                        rows = []
                        for line in lines[1:]:
                            values = line.split(',')
                            row = dict(zip(headers, values))
                            rows.append(row)
                        parsed_content = rows
                        extracted_data["rows"] = rows
                except Exception as e:
                    self.logger.warning(f"CSV parsing failed: {e}")
                    parsed_content = content
            
            # Create processed response
            processed = ProcessedResponse(
                original_response=response,
                processed_content=parsed_content,
                format_detected=detected_format,
                extracted_data=extracted_data
            )
            
            # Calculate basic confidence (based on format detection success)
            processed.confidence_score = 0.8 if detected_format != ResponseFormat.TEXT else 0.6
            
            self.logger.debug(f"Parsed response with format: {detected_format.value}")
            return processed
            
        except Exception as e:
            self.logger.error(f"Failed to parse response: {e}")
            # Return minimal processed response
            return ProcessedResponse(
                original_response=response,
                processed_content=str(response.content) if response.content else "",
                format_detected=ResponseFormat.TEXT,
                validation_results=[ValidationResult.INVALID_FORMAT],
                confidence_score=0.1
            )
    
    def _xml_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """
        Convert XML element to dictionary.
        
        Args:
            element: XML element to convert
            
        Returns:
            Dict[str, Any]: Dictionary representation
        """
        result = {}
        
        # Add attributes
        if element.attrib:
            result["@attributes"] = element.attrib
        
        # Add text content
        if element.text and element.text.strip():
            if len(element) == 0:  # No children
                return element.text.strip()
            result["#text"] = element.text.strip()
        
        # Add children
        for child in element:
            child_data = self._xml_to_dict(child)
            if child.tag in result:
                # Multiple elements with same tag - convert to list
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(child_data)
            else:
                result[child.tag] = child_data
        
        return result
    
    def validate_response(self, processed_response: ProcessedResponse,
                         rules: List[ResponseValidationRule]) -> ProcessedResponse:
        """
        Validate a processed response against rules.
        
        Args:
            processed_response: The response to validate
            rules: List of validation rules to apply
            
        Returns:
            ProcessedResponse: Response with validation results
        """
        content = str(processed_response.processed_content)
        validation_results = []
        
        for rule in rules:
            # Length validation
            if rule.min_length and len(content) < rule.min_length:
                validation_results.append(ValidationResult.TOO_SHORT)
            
            if rule.max_length and len(content) > rule.max_length:
                validation_results.append(ValidationResult.TOO_LONG)
            
            # Keyword validation
            content_lower = content.lower()
            
            if rule.required_keywords:
                missing_keywords = [kw for kw in rule.required_keywords 
                                  if kw.lower() not in content_lower]
                if missing_keywords:
                    validation_results.append(ValidationResult.MISSING_REQUIRED)
            
            if rule.forbidden_keywords:
                forbidden_found = any(kw.lower() in content_lower 
                                    for kw in rule.forbidden_keywords)
                if forbidden_found:
                    validation_results.append(ValidationResult.CONTAINS_FORBIDDEN)
            
            # Pattern validation
            if rule.required_patterns:
                missing_patterns = []
                for pattern in rule.required_patterns:
                    if not re.search(pattern, content, re.IGNORECASE):
                        missing_patterns.append(pattern)
                if missing_patterns:
                    validation_results.append(ValidationResult.MISSING_REQUIRED)
            
            if rule.forbidden_patterns:
                forbidden_found = any(re.search(pattern, content, re.IGNORECASE)
                                    for pattern in rule.forbidden_patterns)
                if forbidden_found:
                    validation_results.append(ValidationResult.CONTAINS_FORBIDDEN)
            
            # Format validation
            if rule.format_type and rule.format_type != processed_response.format_detected:
                validation_results.append(ValidationResult.INVALID_FORMAT)
            
            # Custom validator
            if rule.custom_validator and not rule.custom_validator(content):
                validation_results.append(ValidationResult.INVALID_CONTENT)
        
        # Update processed response
        processed_response.validation_results = validation_results
        
        # Update confidence based on validation
        if not validation_results:
            processed_response.confidence_score = min(1.0, processed_response.confidence_score + 0.2)
        else:
            processed_response.confidence_score = max(0.1, processed_response.confidence_score - 0.3)
        
        return processed_response
    
    def format_response(self, processed_response: ProcessedResponse,
                       target_format: ResponseFormat) -> str:
        """
        Format a processed response to a target format.
        
        Args:
            processed_response: The response to format
            target_format: Target format
            
        Returns:
            str: Formatted response content
        """
        content = processed_response.processed_content
        
        try:
            if target_format == ResponseFormat.JSON:
                if isinstance(content, (dict, list)):
                    return json.dumps(content, indent=2, ensure_ascii=False)
                else:
                    return json.dumps({"content": str(content)}, indent=2, ensure_ascii=False)
            
            elif target_format == ResponseFormat.XML:
                if isinstance(content, dict):
                    return self._dict_to_xml(content, "response")
                else:
                    return f"<response>{self._escape_xml(str(content))}</response>"
            
            elif target_format == ResponseFormat.CSV:
                if isinstance(content, list) and content:
                    if isinstance(content[0], dict):
                        # Convert list of dicts to CSV
                        headers = set()
                        for item in content:
                            headers.update(item.keys())
                        headers = sorted(list(headers))
                        
                        lines = [','.join(headers)]
                        for item in content:
                            row = [str(item.get(h, '')) for h in headers]
                            lines.append(','.join(row))
                        return '\n'.join(lines)
                    else:
                        # Convert list to single-column CSV
                        lines = ["value"]
                        lines.extend(str(item) for item in content)
                        return '\n'.join(lines)
                else:
                    return "content\n" + str(content)
            
            elif target_format == ResponseFormat.YAML:
                # Simple YAML formatting
                if isinstance(content, dict):
                    lines = []
                    for key, value in content.items():
                        lines.append(f"{key}: {self._format_yaml_value(value)}")
                    return '\n'.join(lines)
                else:
                    return f"content: {self._format_yaml_value(content)}"
            
            else:  # TEXT, MARKDOWN
                return str(content)
                
        except Exception as e:
            self.logger.error(f"Failed to format response to {target_format.value}: {e}")
            return str(content)
    
    def _dict_to_xml(self, data: Dict[str, Any], root_tag: str) -> str:
        """Convert dictionary to XML string."""
        def _build_xml(obj, tag):
            if isinstance(obj, dict):
                xml = f"<{tag}>"
                for key, value in obj.items():
                    xml += _build_xml(value, key)
                xml += f"</{tag}>"
                return xml
            elif isinstance(obj, list):
                xml = ""
                for item in obj:
                    xml += _build_xml(item, tag[:-1] if tag.endswith('s') else tag)
                return xml
            else:
                return f"<{tag}>{self._escape_xml(str(obj))}</{tag}>"
        
        return _build_xml(data, root_tag)
    
    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters."""
        return (text.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
                   .replace('"', "&quot;")
                   .replace("'", "&apos;"))
    
    def _format_yaml_value(self, value: Any) -> str:
        """Format value for YAML."""
        if isinstance(value, str) and (' ' in value or ':' in value):
            return f'"{value}"'
        elif isinstance(value, (list, dict)):
            return json.dumps(value)
        else:
            return str(value)
    
    def extract_data(self, processed_response: ProcessedResponse,
                    extraction_rules: Dict[str, str]) -> ProcessedResponse:
        """
        Extract specific data from a processed response using rules.
        
        Args:
            processed_response: The response to extract data from
            extraction_rules: Dictionary mapping field names to regex patterns
            
        Returns:
            ProcessedResponse: Response with extracted data
        """
        content = str(processed_response.processed_content)
        extracted_data = {}
        
        for field_name, pattern in extraction_rules.items():
            try:
                match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
                if match:
                    # If pattern has groups, use first group, otherwise use full match
                    if match.groups():
                        extracted_data[field_name] = match.group(1).strip()
                    else:
                        extracted_data[field_name] = match.group().strip()
            except re.error as e:
                self.logger.warning(f"Invalid regex pattern for {field_name}: {e}")
        
        # Merge with existing extracted data
        processed_response.extracted_data.update(extracted_data)
        
        return processed_response


# Default validation rules for common use cases
DEFAULT_VALIDATION_RULES = [
    ResponseValidationRule(
        name="basic_safety",
        description="Basic safety validation to prevent harmful content",
        forbidden_keywords=["violence", "harm", "illegal", "dangerous"],
        max_length=1000
    ),
    ResponseValidationRule(
        name="structured_output",
        description="Validation for structured output requirements",
        required_patterns=[r'"[^"]*":', r'\{.*\}|\[.*\]'],  # JSON-like structure
        min_length=10
    ),
    ResponseValidationRule(
        name="concise_response",
        description="Validation for concise responses",
        max_length=500,
        required_keywords=[]
    )
]


def initialize_default_rules(processor: ResponseProcessor) -> bool:
    """
    Initialize the processor with default validation rules.
    
    Args:
        processor: The response processor to initialize
        
    Returns:
        bool: True if initialization was successful, False otherwise
    """
    try:
        for rule in DEFAULT_VALIDATION_RULES:
            processor.register_validation_rule(rule)
        return True
    except Exception as e:
        logging.error(f"Failed to initialize default rules: {e}")
        return False


# Example usage
if __name__ == "__main__":
    # Create processor
    processor = ResponseProcessor()
    
    # Initialize default rules
    initialize_default_rules(processor)
    
    # Create a sample model response
    from ..models.base import ModelResponse
    
    sample_response = ModelResponse(
        content='{"answer": "42", "explanation": "This is the answer to life, the universe, and everything"}',
        metadata={"temperature": 0.7},
        model_name="test-model"
    )
    
    # Parse response
    processed = processor.parse_response(sample_response)
    print(f"Parsed response format: {processed.format_detected.value}")
    print(f"Extracted data: {processed.extracted_data}")
    
    # Validate response
    rules = [processor.validation_rules["structured_output"]]
    validated = processor.validate_response(processed, rules)
    print(f"Validation results: {[v.value for v in validated.validation_results]}")
    print(f"Confidence score: {validated.confidence_score:.2f}")
    
    # Format response
    formatted = processor.format_response(validated, ResponseFormat.JSON)
    print(f"Formatted response:\n{formatted}")
