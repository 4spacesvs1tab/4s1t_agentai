"""
Tests for context serialization functionality.
"""

import unittest
import json
import sys
import os
from datetime import datetime

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.context_serialization import (
    serialize_context, deserialize_context, 
    serialize_resource_contents, deserialize_resource_contents,
    compress_context, decompress_context
)


class TestContextSerialization(unittest.TestCase):
    """Test cases for context serialization functions."""

    def setUp(self):
        """Set up test data."""
        self.test_context = {
            "timestamp": datetime.now(),
            "agent_state": {
                "current_task": "processing_user_request",
                "memory_usage": 0.75,
                "tools_available": ["calculator", "web_search"],
                "recent_interactions": [
                    {"type": "user_input", "content": "Hello"},
                    {"type": "tool_call", "tool": "calculator", "args": {"operation": "add", "a": 1, "b": 2}}
                ]
            },
            "resource_cache": {
                "file:///example.txt": {
                    "content": "Example content",
                    "last_accessed": datetime.now()
                }
            }
        }

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        # Test serialization
        serialized = serialize_context(self.test_context, "json")
        self.assertIsInstance(serialized, str)
        
        # Test deserialization
        deserialized = deserialize_context(serialized, "json")
        self.assertIsInstance(deserialized, dict)
        self.assertIn("agent_state", deserialized)
        self.assertIn("resource_cache", deserialized)

    def test_yaml_serialization(self):
        """Test YAML serialization and deserialization."""
        try:
            # Test serialization
            serialized = serialize_context(self.test_context, "yaml")
            self.assertIsInstance(serialized, str)
            
            # Test deserialization
            deserialized = deserialize_context(serialized, "yaml")
            self.assertIsInstance(deserialized, dict)
            self.assertIn("agent_state", deserialized)
            self.assertIn("resource_cache", deserialized)
        except ImportError:
            # PyYAML not available, skip test
            self.skipTest("PyYAML not installed")

    def test_pickle_serialization(self):
        """Test pickle serialization and deserialization."""
        # Test serialization
        serialized = serialize_context(self.test_context, "pickle")
        self.assertIsInstance(serialized, bytes)
        
        # Test deserialization
        deserialized = deserialize_context(serialized, "pickle")
        self.assertIsInstance(deserialized, dict)
        self.assertIn("agent_state", deserialized)

    def test_invalid_format_serialization(self):
        """Test serialization with invalid format."""
        with self.assertRaises(ValueError):
            serialize_context(self.test_context, "invalid_format")

    def test_invalid_format_deserialization(self):
        """Test deserialization with invalid format."""
        with self.assertRaises(ValueError):
            deserialize_context("{}", "invalid_format")

    def test_resource_contents_serialization(self):
        """Test resource contents serialization."""
        # Test JSON format
        data = {"key": "value", "number": 42}
        serialized = serialize_resource_contents(data, "json")
        deserialized = deserialize_resource_contents(serialized, "json")
        self.assertEqual(data, deserialized)
        
        # Test YAML format
        try:
            serialized = serialize_resource_contents(data, "yaml")
            deserialized = deserialize_resource_contents(serialized, "yaml")
            self.assertEqual(data, deserialized)
        except ImportError:
            # PyYAML not available, skip test
            pass
        
        # Test text format
        text = "Hello, World!"
        serialized = serialize_resource_contents(text, "text")
        deserialized = deserialize_resource_contents(serialized, "text")
        self.assertEqual(text, deserialized)
        
        # Test binary format
        binary_data = b"Binary content"
        serialized = serialize_resource_contents(binary_data, "binary")
        deserialized = deserialize_resource_contents(serialized, "binary")
        self.assertEqual(binary_data, deserialized)

    def test_compression(self):
        """Test context compression and decompression."""
        # Test compression
        compressed = compress_context(self.test_context)
        self.assertIsInstance(compressed, bytes)
        # Use our serializer instead of json.dumps directly
        serialized = serialize_context(self.test_context, "json")
        if isinstance(serialized, str):
            serialized = serialized.encode('utf-8')
        self.assertLess(len(compressed), len(serialized))
        
        # Test decompression
        decompressed = decompress_context(compressed)
        self.assertIsInstance(decompressed, dict)
        self.assertIn("agent_state", decompressed)

    def test_datetime_serialization(self):
        """Test datetime serialization in JSON."""
        context_with_datetime = {
            "created_at": datetime.now(),
            "updated_at": datetime(2023, 1, 1, 12, 0, 0)
        }
        
        serialized = serialize_context(context_with_datetime, "json")
        self.assertIsInstance(serialized, str)
        self.assertIn("created_at", serialized)
        self.assertIn("updated_at", serialized)
        
        # Should be able to deserialize without error
        deserialized = deserialize_context(serialized, "json")
        self.assertIsInstance(deserialized, dict)

    def test_fallback_to_json_when_yaml_not_available(self):
        """Test that YAML serialization falls back to JSON when PyYAML is not available."""
        # Temporarily remove yaml from sys.modules to simulate it not being available
        import sys
        original_yaml = sys.modules.get('yaml')
        if 'yaml' in sys.modules:
            sys.modules['yaml'] = None
        
        try:
            # This should fall back to JSON and not raise an error
            serialized = serialize_context(self.test_context, "yaml")
            self.assertIsInstance(serialized, str)
            
            # Deserialization should also work
            deserialized = deserialize_context(serialized, "yaml")
            self.assertIsInstance(deserialized, dict)
        finally:
            # Restore yaml module
            if original_yaml is not None:
                sys.modules['yaml'] = original_yaml


if __name__ == '__main__':
    unittest.main()
