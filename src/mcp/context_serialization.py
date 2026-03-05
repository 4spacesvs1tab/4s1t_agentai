"""
Context serialization for the MCP (Model Context Protocol) implementation.

This module handles serializing and deserializing agent context state for
persistence and transmission.
"""

import json
import pickle
from typing import Any, Dict, Optional, Union
from datetime import datetime
from dataclasses import asdict, is_dataclass
import logging

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime objects."""
    
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def serialize_context(context: Dict[str, Any], format: str = "json") -> Union[str, bytes]:
    """
    Serialize agent context to a string or bytes.
    
    Args:
        context: Dictionary containing context data
        format: Serialization format ("json", "yaml", or "pickle")
        
    Returns:
        Serialized context as string or bytes
        
    Raises:
        ValueError: If format is not supported
    """
    try:
        if format.lower() == "json":
            return json.dumps(context, cls=DateTimeEncoder, indent=2)
        elif format.lower() == "yaml":
            try:
                import yaml
                # Convert datetime objects to strings for YAML serialization
                def convert_datetime(obj):
                    if isinstance(obj, dict):
                        return {k: convert_datetime(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_datetime(item) for item in obj]
                    elif isinstance(obj, datetime):
                        return obj.isoformat()
                    else:
                        return obj
                
                yaml_context = convert_datetime(context)
                return yaml.dump(yaml_context, default_flow_style=False, indent=2)
            except ImportError:
                logger.warning("PyYAML not installed, falling back to JSON")
                return json.dumps(context, cls=DateTimeEncoder, indent=2)
        elif format.lower() == "pickle":
            return pickle.dumps(context)
        else:
            raise ValueError(f"Unsupported serialization format: {format}")
    except Exception as e:
        logger.error(f"Failed to serialize context: {e}")
        raise


def deserialize_context(data: Union[str, bytes], format: str = "json") -> Dict[str, Any]:
    """
    Deserialize agent context from string or bytes.
    
    Args:
        data: Serialized context data
        format: Serialization format ("json", "yaml", or "pickle")
        
    Returns:
        Deserialized context dictionary
        
    Raises:
        ValueError: If format is not supported
    """
    try:
        if format.lower() == "json":
            return json.loads(data)
        elif format.lower() == "yaml":
            try:
                import yaml
                return yaml.safe_load(data)
            except ImportError:
                logger.warning("PyYAML not installed, trying JSON")
                return json.loads(data)
        elif format.lower() == "pickle":
            return pickle.loads(data)
        else:
            raise ValueError(f"Unsupported deserialization format: {format}")
    except Exception as e:
        logger.error(f"Failed to deserialize context: {e}")
        raise


def serialize_resource_contents(contents: Any, format: str = "json") -> Union[str, bytes]:
    """
    Serialize resource contents for transmission.
    
    Args:
        contents: Resource contents to serialize
        format: Serialization format ("json", "yaml", "text", or "binary")
        
    Returns:
        Serialized contents as string or bytes
    """
    try:
        if format.lower() == "json":
            if is_dataclass(contents):
                return json.dumps(asdict(contents), cls=DateTimeEncoder, indent=2)
            else:
                return json.dumps(contents, cls=DateTimeEncoder, indent=2)
        elif format.lower() == "yaml":
            try:
                import yaml
                def convert_datetime(obj):
                    if isinstance(obj, dict):
                        return {k: convert_datetime(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_datetime(item) for item in obj]
                    elif isinstance(obj, datetime):
                        return obj.isoformat()
                    else:
                        return obj
                
                yaml_contents = convert_datetime(contents)
                return yaml.dump(yaml_contents, default_flow_style=False, indent=2)
            except ImportError:
                logger.warning("PyYAML not installed, falling back to JSON")
                if is_dataclass(contents):
                    return json.dumps(asdict(contents), cls=DateTimeEncoder, indent=2)
                else:
                    return json.dumps(contents, cls=DateTimeEncoder, indent=2)
        elif format.lower() == "text":
            if isinstance(contents, str):
                return contents
            else:
                return str(contents)
        elif format.lower() == "binary":
            if isinstance(contents, bytes):
                return contents
            else:
                return str(contents).encode('utf-8')
        else:
            raise ValueError(f"Unsupported serialization format: {format}")
    except Exception as e:
        logger.error(f"Failed to serialize resource contents: {e}")
        raise


def deserialize_resource_contents(data: Union[str, bytes], format: str = "json") -> Any:
    """
    Deserialize resource contents.
    
    Args:
        data: Serialized resource contents
        format: Serialization format ("json", "yaml", "text", or "binary")
        
    Returns:
        Deserialized contents
    """
    try:
        if format.lower() == "json":
            return json.loads(data)
        elif format.lower() == "yaml":
            try:
                import yaml
                return yaml.safe_load(data)
            except ImportError:
                logger.warning("PyYAML not installed, trying JSON")
                return json.loads(data)
        elif format.lower() == "text":
            if isinstance(data, bytes):
                return data.decode('utf-8')
            else:
                return data
        elif format.lower() == "binary":
            return data
        else:
            raise ValueError(f"Unsupported deserialization format: {format}")
    except Exception as e:
        logger.error(f"Failed to deserialize resource contents: {e}")
        raise


def compress_context(context: Dict[str, Any]) -> bytes:
    """
    Compress context for efficient storage/transmission.
    
    Args:
        context: Context dictionary to compress
        
    Returns:
        Compressed context as bytes
    """
    try:
        import zlib
        serialized = serialize_context(context, "json")
        if isinstance(serialized, str):
            serialized = serialized.encode('utf-8')
        return zlib.compress(serialized)
    except Exception as e:
        logger.error(f"Failed to compress context: {e}")
        raise


def decompress_context(compressed_data: bytes) -> Dict[str, Any]:
    """
    Decompress context data.
    
    Args:
        compressed_data: Compressed context data
        
    Returns:
        Decompressed context dictionary
    """
    try:
        import zlib
        decompressed = zlib.decompress(compressed_data)
        return deserialize_context(decompressed.decode('utf-8'), "json")
    except Exception as e:
        logger.error(f"Failed to decompress context: {e}")
        raise


# Example usage
if __name__ == "__main__":
    # Example context
    example_context = {
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
    
    # Test JSON serialization
    json_serialized = serialize_context(example_context, "json")
    print("JSON serialized:")
    print(json_serialized[:200] + "..." if len(json_serialized) > 200 else json_serialized)
    
    json_deserialized = deserialize_context(json_serialized, "json")
    print("\nJSON deserialized:")
    print(list(json_deserialized.keys()))
    
    # Test YAML serialization (if PyYAML is available)
    try:
        yaml_serialized = serialize_context(example_context, "yaml")
        print("\nYAML serialized:")
        print(yaml_serialized[:200] + "..." if len(yaml_serialized) > 200 else yaml_serialized)
        
        yaml_deserialized = deserialize_context(yaml_serialized, "yaml")
        print("\nYAML deserialized:")
        print(list(yaml_deserialized.keys()))
    except Exception as e:
        print(f"\nYAML test skipped: {e}")
    
    # Test compression
    compressed = compress_context(example_context)
    print(f"\nCompressed size: {len(compressed)} bytes")
    
    decompressed = decompress_context(compressed)
    print(f"Decompressed keys: {list(decompressed.keys())}")
