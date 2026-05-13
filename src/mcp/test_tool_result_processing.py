"""
Tests for tool result processing functionality.
"""

import unittest
import sys
import os
from datetime import datetime

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.tool_result_processing import (
    ToolResultProcessor, ProcessedToolResult, ToolResultSchema, ToolResult
)


class TestToolResultProcessing(unittest.TestCase):
    """Test cases for tool result processing."""

    def setUp(self):
        """Set up test processor."""
        self.processor = ToolResultProcessor()

    def test_process_successful_result(self):
        """Test processing a successful tool result."""
        tool_result = ToolResult(
            tool_name="test_tool",
            result={"value": 42, "message": "success"},
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        
        self.assertIsInstance(processed, ProcessedToolResult)
        self.assertEqual(processed.tool_name, "test_tool")
        self.assertFalse(processed.is_error)
        self.assertIsNone(processed.error_message)
        self.assertIsNotNone(processed.formatted_output)
        self.assertIn("value", processed.formatted_output)
        self.assertIn("42", processed.formatted_output)

    def test_process_error_result(self):
        """Test processing an error tool result."""
        tool_result = ToolResult(
            tool_name="test_tool",
            result="Something went wrong",
            is_error=True
        )
        
        processed = self.processor.process_result(tool_result)
        
        self.assertIsInstance(processed, ProcessedToolResult)
        self.assertEqual(processed.tool_name, "test_tool")
        self.assertTrue(processed.is_error)
        self.assertEqual(processed.error_message, "Something went wrong")
        self.assertIn("Error", processed.formatted_output)

    def test_result_schema_registration(self):
        """Test registering result schemas."""
        schema = ToolResultSchema(
            tool_name="calculator",
            expected_type="object",
            required_fields=["result"]
        )
        
        result = self.processor.register_result_schema(schema)
        self.assertTrue(result)
        
        # Check that schema is registered
        self.assertIn("calculator", self.processor.result_schemas)
        self.assertEqual(self.processor.result_schemas["calculator"].tool_name, "calculator")

    def test_result_validation_success(self):
        """Test result validation with valid result."""
        # Register schema
        schema = ToolResultSchema(
            tool_name="validator_test",
            expected_type="object",
            required_fields=["result"]
        )
        self.processor.register_result_schema(schema)
        
        # Process valid result
        tool_result = ToolResult(
            tool_name="validator_test",
            result={"result": 42},
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        self.assertEqual(len(processed.validation_errors), 0)

    def test_result_validation_failure(self):
        """Test result validation with invalid result."""
        # Register schema
        schema = ToolResultSchema(
            tool_name="validator_test",
            expected_type="object",
            required_fields=["result"]
        )
        self.processor.register_result_schema(schema)
        
        # Process invalid result (missing required field)
        tool_result = ToolResult(
            tool_name="validator_test",
            result={"value": 42},  # Missing "result" field
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        self.assertTrue(processed.is_error)
        self.assertIn("Missing required field: result", processed.error_message)

    def test_result_formatting_dict(self):
        """Test formatting of dictionary results."""
        tool_result = ToolResult(
            tool_name="formatter_test",
            result={"key1": "value1", "key2": "value2"},
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        # Should be pretty-printed JSON
        self.assertIn("{", processed.formatted_output)
        self.assertIn("}", processed.formatted_output)
        self.assertIn("key1", processed.formatted_output)

    def test_result_formatting_list(self):
        """Test formatting of list results."""
        tool_result = ToolResult(
            tool_name="formatter_test",
            result=["item1", "item2", "item3"],
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        # Should be formatted as bullet points
        self.assertIn("- item1", processed.formatted_output)
        self.assertIn("- item2", processed.formatted_output)

    def test_result_transformation_uppercase(self):
        """Test uppercase transformation."""
        tool_result = ToolResult(
            tool_name="transform_test",
            result="hello world",
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        transformed = self.processor.transform_result(processed, "uppercase")
        
        self.assertEqual(transformed.formatted_output, "HELLO WORLD")

    def test_result_transformation_json(self):
        """Test JSON transformation."""
        tool_result = ToolResult(
            tool_name="transform_test",
            result={"data": "value"},
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        transformed = self.processor.transform_result(processed, "json")
        
        # Should be pretty-printed JSON
        self.assertIn("{", transformed.formatted_output)
        self.assertIn("}", transformed.formatted_output)
        self.assertIn("data", transformed.formatted_output)

    def test_result_aggregation(self):
        """Test aggregating multiple results."""
        # Create test results
        result1 = ProcessedToolResult(
            tool_name="tool1",
            original_result="result1",
            processed_result="result1",
            formatted_output="Result 1 output"
        )
        
        result2 = ProcessedToolResult(
            tool_name="tool2",
            original_result="result2",
            processed_result="result2",
            is_error=True,
            error_message="Something failed",
            formatted_output="Error message"
        )
        
        # Aggregate
        aggregated = self.processor.aggregate_results([result1, result2])
        
        self.assertEqual(aggregated.tool_name, "aggregator")
        self.assertTrue(aggregated.is_error)
        self.assertIn("1 out of 2", aggregated.error_message)
        self.assertIn("tool1", aggregated.formatted_output)
        self.assertIn("tool2", aggregated.formatted_output)

    def test_empty_aggregation(self):
        """Test aggregating empty results list."""
        aggregated = self.processor.aggregate_results([])
        
        self.assertEqual(aggregated.tool_name, "aggregator")
        self.assertFalse(aggregated.is_error)
        self.assertEqual(aggregated.formatted_output, "No results to aggregate")

    def test_processing_time_measurement(self):
        """Test that processing time is measured."""
        tool_result = ToolResult(
            tool_name="timing_test",
            result={"value": 42},
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        self.assertGreaterEqual(processed.processing_time_ms, 0)
        self.assertIsInstance(processed.timestamp, datetime)

    def test_invalid_transformation(self):
        """Test handling of invalid transformations."""
        tool_result = ToolResult(
            tool_name="transform_test",
            result="test",
            is_error=False
        )
        
        processed = self.processor.process_result(tool_result)
        # Try an invalid transformation that causes an error
        transformed = self.processor.transform_result(processed, "invalid_transform")
        
        # Should still return a result, but with error metadata
        self.assertIsNotNone(transformed)


if __name__ == '__main__':
    unittest.main()
