"""
Tool result processing for the 4S1T Agent AI system.

This module provides advanced processing capabilities for tool execution results,
including formatting, validation, transformation, and error handling.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
import traceback

from .mcp_types import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class ProcessedToolResult:
    """Processed tool result with additional metadata."""
    
    tool_name: str
    original_result: Any
    processed_result: Any
    is_error: bool = False
    error_message: Optional[str] = None
    processing_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    formatted_output: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)


@dataclass
class ToolResultSchema:
    """Schema for validating tool results."""
    
    tool_name: str
    expected_type: str = "any"  # "string", "number", "object", "array", "boolean", "any"
    required_fields: List[str] = field(default_factory=list)
    allowed_values: List[Any] = field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class ToolResultProcessor:
    """Processor for handling and transforming tool execution results."""
    
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.result_schemas: Dict[str, ToolResultSchema] = {}
    
    def register_result_schema(self, schema: ToolResultSchema) -> bool:
        """
        Register a result schema for a tool.
        
        Args:
            schema: Tool result schema
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.result_schemas[schema.tool_name] = schema
            self.logger.debug(f"Registered result schema for tool: {schema.tool_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register result schema for tool {schema.tool_name}: {e}")
            return False
    
    def process_result(self, tool_result: ToolResult) -> ProcessedToolResult:
        """
        Process a tool result with advanced formatting and validation.
        
        Args:
            tool_result: Original tool result
            
        Returns:
            ProcessedToolResult with enhanced metadata and formatting
        """
        start_time = datetime.now()
        
        try:
            # Create processed result
            processed = ProcessedToolResult(
                tool_name=tool_result.tool_name,
                original_result=tool_result.result,
                processed_result=tool_result.result,
                is_error=tool_result.is_error,
                error_message=tool_result.result if tool_result.is_error else None
            )
            
            # Format the output
            processed.formatted_output = self._format_result(tool_result)
            
            # Validate result if schema exists
            if tool_result.tool_name in self.result_schemas:
                schema = self.result_schemas[tool_result.tool_name]
                validation_result = self._validate_result(tool_result, schema)
                processed.validation_errors = validation_result.errors
                
                # If validation failed and it wasn't already an error, mark as error
                if validation_result.errors and not tool_result.is_error:
                    processed.is_error = True
                    processed.error_message = "; ".join(validation_result.errors)
            
            # Calculate processing time
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            processed.processing_time_ms = processing_time
            
            self.logger.debug(f"Processed result for tool: {tool_result.tool_name} "
                            f"(took {processing_time:.2f}ms)")
            
            return processed
            
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            
            # Return error result
            return ProcessedToolResult(
                tool_name=tool_result.tool_name,
                original_result=tool_result.result,
                processed_result=None,
                is_error=True,
                error_message=f"Result processing failed: {str(e)}",
                processing_time_ms=processing_time,
                formatted_output=f"Error processing result: {str(e)}\n{traceback.format_exc()}"
            )
    
    def _format_result(self, tool_result: ToolResult) -> str:
        """
        Format tool result for display/output.
        
        Args:
            tool_result: Tool result to format
            
        Returns:
            Formatted string representation
        """
        try:
            if tool_result.is_error:
                return f"❌ Error in {tool_result.tool_name}: {tool_result.result}"
            
            # Format based on result type
            if isinstance(tool_result.result, dict):
                # Pretty print JSON-like results
                return json.dumps(tool_result.result, indent=2, ensure_ascii=False)
            elif isinstance(tool_result.result, list):
                # Format lists nicely
                if len(tool_result.result) == 0:
                    return "[]"
                elif len(tool_result.result) == 1:
                    return str(tool_result.result[0])
                else:
                    return "\n".join([f"- {item}" for item in tool_result.result])
            else:
                # Simple string representation for other types
                return str(tool_result.result)
                
        except Exception as e:
            return f"⚠️  Formatting error: {str(e)}\nRaw result: {tool_result.result}"
    
    def _validate_result(self, tool_result: ToolResult, schema: ToolResultSchema) -> object:
        """
        Validate tool result against schema.
        
        Args:
            tool_result: Tool result to validate
            schema: Schema to validate against
            
        Returns:
            Validation result object
        """
        class ValidationResult:
            def __init__(self):
                self.errors = []
                self.warnings = []
        
        result = ValidationResult()
        
        try:
            # Skip validation for error results
            if tool_result.is_error:
                return result
            
            result_data = tool_result.result
            
            # Type validation
            if schema.expected_type != "any":
                type_mapping = {
                    "string": str,
                    "number": (int, float),
                    "object": dict,
                    "array": list,
                    "boolean": bool
                }
                
                expected_type = type_mapping.get(schema.expected_type)
                if expected_type and not isinstance(result_data, expected_type):
                    result.errors.append(
                        f"Expected {schema.expected_type}, got {type(result_data).__name__}"
                    )
            
            # Required fields validation (for objects)
            if isinstance(result_data, dict) and schema.required_fields:
                for field in schema.required_fields:
                    if field not in result_data:
                        result.errors.append(f"Missing required field: {field}")
            
            # Value validation
            if schema.allowed_values and result_data not in schema.allowed_values:
                result.errors.append(
                    f"Value not in allowed values: {result_data}"
                )
            
            # Length validation (for strings/lists)
            if isinstance(result_data, (str, list)):
                if schema.min_length is not None and len(result_data) < schema.min_length:
                    result.errors.append(
                        f"Length {len(result_data)} is less than minimum {schema.min_length}"
                    )
                if schema.max_length is not None and len(result_data) > schema.max_length:
                    result.errors.append(
                        f"Length {len(result_data)} exceeds maximum {schema.max_length}"
                    )
            
            # Value range validation (for numbers)
            if isinstance(result_data, (int, float)):
                if schema.min_value is not None and result_data < schema.min_value:
                    result.errors.append(
                        f"Value {result_data} is less than minimum {schema.min_value}"
                    )
                if schema.max_value is not None and result_data > schema.max_value:
                    result.errors.append(
                        f"Value {result_data} exceeds maximum {schema.max_value}"
                    )
            
        except Exception as e:
            result.errors.append(f"Validation error: {str(e)}")
        
        return result
    
    def transform_result(self, processed_result: ProcessedToolResult, 
                        transformation: str) -> ProcessedToolResult:
        """
        Apply a transformation to a processed result.
        
        Args:
            processed_result: Result to transform
            transformation: Type of transformation ("uppercase", "lowercase", "json", "summary")
            
        Returns:
            Transformed processed result
        """
        try:
            transformed = ProcessedToolResult(
                tool_name=processed_result.tool_name,
                original_result=processed_result.original_result,
                processed_result=processed_result.processed_result,
                is_error=processed_result.is_error,
                error_message=processed_result.error_message,
                processing_time_ms=processed_result.processing_time_ms,
                timestamp=processed_result.timestamp,
                metadata=processed_result.metadata.copy(),
                formatted_output=processed_result.formatted_output,
                validation_errors=processed_result.validation_errors
            )
            
            if processed_result.is_error:
                return transformed  # Don't transform error results
            
            # Apply transformation
            if transformation == "uppercase":
                transformed.formatted_output = str(processed_result.formatted_output).upper()
            elif transformation == "lowercase":
                transformed.formatted_output = str(processed_result.formatted_output).lower()
            elif transformation == "json":
                # Ensure result is JSON-formatted
                if isinstance(processed_result.original_result, dict):
                    transformed.formatted_output = json.dumps(
                        processed_result.original_result, 
                        indent=2, 
                        ensure_ascii=False
                    )
                else:
                    transformed.formatted_output = json.dumps(
                        {"result": processed_result.original_result}, 
                        indent=2, 
                        ensure_ascii=False
                    )
            elif transformation == "summary":
                # Create a summary of the result
                if isinstance(processed_result.original_result, dict):
                    summary_items = []
                    for key, value in processed_result.original_result.items():
                        if isinstance(value, (list, dict)) and len(str(value)) > 100:
                            summary_items.append(f"{key}: <{type(value).__name__} with {len(value)} items>")
                        else:
                            summary_items.append(f"{key}: {value}")
                    transformed.formatted_output = "\n".join(summary_items)
                elif isinstance(processed_result.original_result, list):
                    transformed.formatted_output = f"List with {len(processed_result.original_result)} items"
                else:
                    transformed.formatted_output = str(processed_result.original_result)[:100] + "..."
            
            transformed.metadata["transformation"] = transformation
            
            self.logger.debug(f"Applied {transformation} transformation to result from {processed_result.tool_name}")
            
            return transformed
            
        except Exception as e:
            # Return original result with error metadata
            transformed = ProcessedToolResult(
                tool_name=processed_result.tool_name,
                original_result=processed_result.original_result,
                processed_result=processed_result.processed_result,
                is_error=True,
                error_message=f"Transformation failed: {str(e)}",
                processing_time_ms=processed_result.processing_time_ms,
                timestamp=processed_result.timestamp,
                metadata=processed_result.metadata.copy(),
                formatted_output=processed_result.formatted_output,
                validation_errors=processed_result.validation_errors
            )
            transformed.metadata["transformation_error"] = str(e)
            return transformed
    
    def aggregate_results(self, results: List[ProcessedToolResult]) -> ProcessedToolResult:
        """
        Aggregate multiple processed results into a single result.
        
        Args:
            results: List of processed results to aggregate
            
        Returns:
            Aggregated processed result
        """
        if not results:
            return ProcessedToolResult(
                tool_name="aggregator",
                original_result=[],
                processed_result=[],
                formatted_output="No results to aggregate"
            )
        
        # Combine results
        aggregated_original = [r.original_result for r in results]
        aggregated_processed = [r.processed_result for r in results]
        
        # Count errors
        error_count = sum(1 for r in results if r.is_error)
        
        # Create formatted output
        formatted_parts = []
        for i, result in enumerate(results):
            if result.is_error:
                formatted_parts.append(f"[{i+1}] ❌ {result.tool_name}: {result.error_message}")
            else:
                formatted_parts.append(f"[{i+1}] ✅ {result.tool_name}:\n{result.formatted_output}")
        
        formatted_output = "\n\n".join(formatted_parts)
        
        # Calculate total processing time
        total_time = sum(r.processing_time_ms for r in results)
        
        return ProcessedToolResult(
            tool_name="aggregator",
            original_result=aggregated_original,
            processed_result=aggregated_processed,
            is_error=error_count > 0,
            error_message=f"{error_count} out of {len(results)} results had errors" if error_count > 0 else None,
            processing_time_ms=total_time,
            formatted_output=formatted_output,
            metadata={
                "result_count": len(results),
                "error_count": error_count,
                "success_count": len(results) - error_count
            }
        )


# Example usage
if __name__ == "__main__":
    # Create processor
    processor = ToolResultProcessor()
    
    # Register a schema for a calculator tool
    calc_schema = ToolResultSchema(
        tool_name="calculator",
        expected_type="object",
        required_fields=["result"]
    )
    processor.register_result_schema(calc_schema)
    
    # Process a successful result
    success_result = ToolResult(
        tool_name="calculator",
        result={"result": 42, "operation": "add"},
        is_error=False
    )
    
    processed = processor.process_result(success_result)
    print("Processed success result:")
    print(processed.formatted_output)
    print()
    
    # Process an error result
    error_result = ToolResult(
        tool_name="calculator",
        result="Division by zero",
        is_error=True
    )
    
    processed_error = processor.process_result(error_result)
    print("Processed error result:")
    print(processed_error.formatted_output)
    print()
    
    # Transform a result
    transformed = processor.transform_result(processed, "json")
    print("Transformed result (JSON):")
    print(transformed.formatted_output)
    print()
    
    # Aggregate results
    aggregated = processor.aggregate_results([processed, processed_error])
    print("Aggregated results:")
    print(aggregated.formatted_output)
