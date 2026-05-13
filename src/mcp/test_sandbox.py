"""
Tests for the MCP tool execution sandbox.
"""

import asyncio
import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.sandbox import ToolSandbox, SandboxConfig, ExecutionResult


async def test_sandbox_basic_execution():
    """Test basic tool execution in the sandbox."""
    print("Testing basic sandbox execution...")
    
    # Create sandbox
    sandbox = ToolSandbox()
    
    # Create a simple test function
    async def test_tool(arguments):
        await asyncio.sleep(0.1)  # Simulate some work
        return {"result": f"Processed {arguments}"}
    
    # Execute the tool
    result = await sandbox.execute_tool(test_tool, {"input": "test data"})
    
    # Check results
    assert isinstance(result, ExecutionResult)
    assert result.success == True
    assert result.output == {"result": "Processed {'input': 'test data'}"}
    assert result.error is None
    assert result.execution_time > 0
    assert result.timed_out == False
    
    print("  ✓ Basic sandbox execution successful")
    print(f"  Execution time: {result.execution_time:.3f}s")


async def test_sandbox_timeout():
    """Test tool execution timeout in the sandbox."""
    print("\nTesting sandbox timeout...")
    
    # Create sandbox with short timeout
    config = SandboxConfig(timeout_seconds=1)
    sandbox = ToolSandbox(config)
    
    # Create a function that takes longer than the timeout
    async def slow_tool(arguments):
        await asyncio.sleep(2)  # Longer than 1-second timeout
        return {"result": "This should not be reached"}
    
    # Execute the tool
    result = await sandbox.execute_tool(slow_tool, {"input": "slow test"})
    
    # Check results
    assert isinstance(result, ExecutionResult)
    assert result.success == False
    assert result.output is None
    assert result.error is not None
    assert result.timed_out == True
    assert result.execution_time > 0
    
    print("  ✓ Sandbox timeout handling successful")
    print(f"  Error message: {result.error}")
    print(f"  Execution time: {result.execution_time:.3f}s")


async def test_sandbox_concurrent_limit():
    """Test concurrent execution limit in the sandbox."""
    print("\nTesting sandbox concurrent execution limit...")
    
    # Create sandbox with low concurrent limit
    config = SandboxConfig(max_concurrent_executions=2)
    sandbox = ToolSandbox(config)
    
    # Create a function that takes some time
    async def medium_tool(arguments):
        await asyncio.sleep(0.5)
        return {"result": f"Processed {arguments}"}
    
    # Start multiple concurrent executions
    tasks = []
    for i in range(5):
        task = sandbox.execute_tool(medium_tool, {"input": f"test{i}"})
        tasks.append(task)
    
    # Wait for all to complete
    results = await asyncio.gather(*tasks)
    
    # Check that we got results for all executions
    assert len(results) == 5
    
    # Count successful vs failed executions
    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    
    print(f"  Successful executions: {successful}")
    print(f"  Failed executions: {failed}")
    print("  ✓ Concurrent execution limit test completed")


async def test_sandbox_external_command():
    """Test external command execution in the sandbox."""
    print("\nTesting sandbox external command execution...")
    
    # Create sandbox
    sandbox = ToolSandbox()
    
    # Execute a simple command
    result = sandbox.execute_external_command("echo 'Hello from sandbox'")
    
    # Check results
    assert isinstance(result, ExecutionResult)
    assert result.success == True
    assert "Hello from sandbox" in result.output
    assert result.error is None or result.error == ""
    
    print("  ✓ External command execution successful")
    print(f"  Output: {result.output.strip()}")


async def test_sandbox_status():
    """Test sandbox status reporting."""
    print("\nTesting sandbox status...")
    
    # Create sandbox
    sandbox = ToolSandbox()
    
    # Get initial status
    status = sandbox.get_sandbox_status()
    
    # Check status fields
    assert "active_executions" in status
    assert "max_concurrent" in status
    assert "timeout_seconds" in status
    assert "memory_limit_mb" in status
    assert "allow_network" in status
    
    assert status["active_executions"] == 0
    assert status["max_concurrent"] == 10  # Default value
    
    print("  ✓ Sandbox status reporting successful")
    print(f"  Status: {status}")


async def run_all_tests():
    """Run all sandbox tests."""
    await test_sandbox_basic_execution()
    await test_sandbox_timeout()
    await test_sandbox_concurrent_limit()
    await test_sandbox_external_command()
    await test_sandbox_status()
    print("\n🎉 All Sandbox tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
