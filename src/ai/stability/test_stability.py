"""
Test module for agent stability features.

This module demonstrates how to use the context window management
and agent stability features in the 4S1T Agent AI framework.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

# Add parent directory to path to enable imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.stability.context_window import ContextWindowManager, ContextWindowConfig
from ai.stability.agent_stability import AgentStabilityManager, StabilityConfig
from ai.context.manager import ContextManager
from ai.models.base import ModelManager, ModelMetadata, ModelType
from ai.models.language_model import MockLanguageModel

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo_context_window_management():
    """Demonstrate context window management features."""
    print("=== Context Window Management Demo ===")
    
    # Create context manager
    context_manager = ContextManager()
    
    # Create context window manager
    config = ContextWindowConfig(
        max_tokens=4096,  # Smaller limit for demo
        auto_compaction_threshold=0.7,  # 70% threshold
        compaction_target_ratio=0.5,  # Compact to 50%
        min_entries_before_compaction=5
    )
    
    window_manager = ContextWindowManager(context_manager, config)
    
    # Create a conversation
    conv_id = context_manager.create_conversation(metadata={"demo": True})
    print(f"Created conversation: {conv_id}")
    
    # Add many entries to simulate a long conversation
    for i in range(20):
        context_manager.add_entry(
            conv_id, 
            "user" if i % 2 == 0 else "assistant",
            f"This is message number {i} with some content to increase token count. "
            f"Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            f"Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
            metadata={"sequence": i, "importance": 5 if i > 15 else 1}  # Mark recent as more important
        )
    
    # Check token usage
    token_usage = await window_manager.estimate_token_usage(conv_id)
    print(f"Token usage: {token_usage.total_tokens}/{config.max_tokens} "
          f"({token_usage.usage_percentage:.1f}%)")
    
    # Check if compaction is needed and perform it
    if token_usage.usage_percentage > config.auto_compaction_threshold * 100:
        print("Auto-compaction triggered...")
        compacted = await window_manager.check_and_compact_context(conv_id)
        if compacted:
            print("Context compacted successfully")
            
            # Check token usage after compaction
            new_usage = await window_manager.estimate_token_usage(conv_id)
            print(f"Token usage after compaction: {new_usage.total_tokens}/{config.max_tokens} "
                  f"({new_usage.usage_percentage:.1f}%)")
            
            # Show compaction history
            history = window_manager.get_compaction_history(conv_id)
            print(f"Compaction history entries: {len(history)}")
    
    print()


async def demo_agent_stability():
    """Demonstrate agent stability management features."""
    print("=== Agent Stability Management Demo ===")
    
    # Create model manager and register a mock model
    model_manager = ModelManager()
    
    mock_metadata = ModelMetadata(
        name="mock-model",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Mock model for testing"
    )
    
    mock_model = MockLanguageModel(mock_metadata)
    model_manager.register_model(mock_model)
    model_manager.set_active_model(ModelType.LANGUAGE_MODEL, "mock-model")
    
    # Create context manager
    context_manager = ContextManager()
    
    # Create stability manager
    stability_config = StabilityConfig(
        response_timeout_seconds=10,
        heartbeat_interval_seconds=5,
        max_missed_heartbeats=2,
        auto_recovery_enabled=True,
        alert_on_disconnect=True
    )
    
    stability_manager = AgentStabilityManager(model_manager, context_manager, stability_config)
    
    # Start monitoring
    await stability_manager.start_monitoring()
    print("Agent stability monitoring started")
    
    # Simulate normal operation
    print("Simulating normal agent operation...")
    for i in range(5):
        await stability_manager.send_heartbeat()
        await asyncio.sleep(1)
        
        if i == 2:
            await stability_manager.record_response()
    
    # Get status
    status = stability_manager.get_status_summary()
    print(f"Agent status: {status['state']}")
    print(f"Model status: {status['model_status']}")
    
    # Simulate disconnection
    print("\nSimulating agent disconnection...")
    await asyncio.sleep(15)  # Miss a few heartbeats
    
    # Check status after disconnection
    status = stability_manager.get_status_summary()
    print(f"Agent status after disconnection: {status['state']}")
    
    # Simulate recovery
    print("\nSimulating agent recovery...")
    await stability_manager.send_heartbeat()
    await asyncio.sleep(1)
    
    status = stability_manager.get_status_summary()
    print(f"Agent status after recovery: {status['state']}")
    
    # Stop monitoring
    await stability_manager.stop_monitoring()
    print("Agent stability monitoring stopped")
    
    print()


async def demo_integration():
    """Demonstrate integration of both features."""
    print("=== Integrated Demo ===")
    
    # Create managers
    context_manager = ContextManager()
    model_manager = ModelManager()
    
    # Register mock model
    mock_metadata = ModelMetadata(
        name="demo-model",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Demo model"
    )
    mock_model = MockLanguageModel(mock_metadata)
    model_manager.register_model(mock_model)
    model_manager.set_active_model(ModelType.LANGUAGE_MODEL, "demo-model")
    
    # Create stability components
    window_config = ContextWindowConfig(
        max_tokens=2048,
        auto_compaction_threshold=0.8,
        compaction_target_ratio=0.6
    )
    
    stability_config = StabilityConfig(
        heartbeat_interval_seconds=3,
        max_missed_heartbeats=2,
        auto_recovery_enabled=True
    )
    
    window_manager = ContextWindowManager(context_manager, window_config)
    stability_manager = AgentStabilityManager(model_manager, context_manager, stability_config)
    
    # Start monitoring
    await stability_manager.start_monitoring()
    
    # Create conversation and add entries
    conv_id = context_manager.create_conversation(metadata={"integration_demo": True})
    
    # Add entries that will trigger compaction
    for i in range(30):
        context_manager.add_entry(
            conv_id,
            "user" if i % 3 == 0 else "assistant",
            f"Message {i}: This is a long message to consume tokens. "
            f"The quick brown fox jumps over the lazy dog. "
            f"Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            f"Ut enim ad minim veniam, quis nostrud exercitation.",
            metadata={"msg_id": i}
        )
        
        # Send heartbeat periodically
        if i % 5 == 0:
            await stability_manager.send_heartbeat()
    
    # Check context size
    token_usage = await window_manager.estimate_token_usage(conv_id)
    print(f"Context token usage: {token_usage.total_tokens}/{window_config.max_tokens} "
          f"({token_usage.usage_percentage:.1f}%)")
    
    # This should trigger auto-compaction
    compacted = await window_manager.check_and_compact_context(conv_id)
    if compacted:
        new_usage = await window_manager.estimate_token_usage(conv_id)
        print(f"After compaction: {new_usage.total_tokens}/{window_config.max_tokens} "
              f"({new_usage.usage_percentage:.1f}%)")
    
    # Record successful response
    await stability_manager.record_response()
    
    # Get final status
    status = stability_manager.get_status_summary()
    print(f"Final agent status: {status['state']}")
    print(f"Context entries: {status['context_entries']}")
    
    # Stop monitoring
    await stability_manager.stop_monitoring()
    
    print("Integration demo completed")
    print()


async def main():
    """Run all demos."""
    print("🚀 4S1T Agent AI - Stability Features Demo")
    print("=" * 50)
    print()
    
    # Run context window management demo
    await demo_context_window_management()
    
    # Run agent stability demo
    await demo_agent_stability()
    
    # Run integration demo
    await demo_integration()
    
    print("✅ All demos completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
