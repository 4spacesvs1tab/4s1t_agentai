"""
Tests for the MCP audit logging functionality.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.audit_logging import MCPAuditLogger, ResourceAccessLogEntry, ToolExecutionLogEntry


async def test_audit_logger_initialization():
    """Test audit logger initialization."""
    print("Testing Audit Logger Initialization...")
    
    # Create audit logger with test log file
    log_file = "test_audit.log"
    audit_logger = MCPAuditLogger(log_file)
    
    # Check that logger was created
    assert audit_logger is not None
    assert audit_logger.log_file_path == log_file
    
    print("  ✓ Audit logger initialization successful")


async def test_resource_access_logging():
    """Test resource access logging."""
    print("\nTesting Resource Access Logging...")
    
    # Create audit logger with test log file
    log_file = "test_resource_access.log"
    audit_logger = MCPAuditLogger(log_file)
    
    # Create a resource access log entry
    entry = ResourceAccessLogEntry(
        timestamp=datetime.now().isoformat(),
        client_id="test_client_123",
        resource_uri="file:///test/resource.txt",
        operation="GET",
        success=True,
        execution_time_ms=15.5
    )
    
    # Log the entry
    audit_logger.log_resource_access(entry)
    
    # Retrieve log entries
    entries = audit_logger.get_log_entries(10)
    
    # Check that we have entries
    assert len(entries) > 0
    
    # Check that our entry is in the logs
    found = False
    for log_entry in entries:
        if "RESOURCE_ACCESS" in log_entry and "test_client_123" in log_entry:
            found = True
            break
    
    assert found, "Resource access log entry not found in log file"
    
    print("  ✓ Resource access logging successful")


async def test_tool_execution_logging():
    """Test tool execution logging."""
    print("\nTesting Tool Execution Logging...")
    
    # Create audit logger with test log file
    log_file = "test_tool_execution.log"
    audit_logger = MCPAuditLogger(log_file)
    
    # Create a tool execution log entry
    entry = ToolExecutionLogEntry(
        timestamp=datetime.now().isoformat(),
        client_id="test_client_456",
        tool_name="calculator",
        success=True,
        execution_time_ms=25.3,
        input_args={"operation": "add", "a": 5, "b": 3},
        output_result={"result": 8}
    )
    
    # Log the entry
    audit_logger.log_tool_execution(entry)
    
    # Retrieve log entries
    entries = audit_logger.get_log_entries(10)
    
    # Check that we have entries
    assert len(entries) > 0
    
    # Check that our entry is in the logs
    found = False
    for log_entry in entries:
        if "TOOL_EXECUTION" in log_entry and "calculator" in log_entry:
            found = True
            break
    
    assert found, "Tool execution log entry not found in log file"
    
    print("  ✓ Tool execution logging successful")


async def test_log_retrieval():
    """Test log retrieval functionality."""
    print("\nTesting Log Retrieval...")
    
    # Create audit logger with test log file
    log_file = "test_log_retrieval.log"
    audit_logger = MCPAuditLogger(log_file)
    
    # Add several log entries
    for i in range(5):
        resource_entry = ResourceAccessLogEntry(
            timestamp=datetime.now().isoformat(),
            client_id=f"client_{i}",
            resource_uri=f"file:///resource_{i}.txt",
            operation="GET",
            success=True,
            execution_time_ms=10.0 + i
        )
        audit_logger.log_resource_access(resource_entry)
    
    # Retrieve log entries with limit
    entries = audit_logger.get_log_entries(3)
    
    # Check that we got the limited number of entries
    assert len(entries) <= 3
    
    # Check that entries contain expected content
    assert len(entries) > 0
    assert "RESOURCE_ACCESS" in entries[0]
    
    print("  ✓ Log retrieval with limit successful")
    
    # Test retrieving all entries
    all_entries = audit_logger.get_log_entries(100)
    assert len(all_entries) >= 5
    
    print("  ✓ Log retrieval of all entries successful")


async def run_all_tests():
    """Run all audit logging tests."""
    await test_audit_logger_initialization()
    await test_resource_access_logging()
    await test_tool_execution_logging()
    await test_log_retrieval()
    print("\n🎉 All Audit Logging tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
