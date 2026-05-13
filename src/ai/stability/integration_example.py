"""
Integration example for agent stability features.

This module shows how to integrate context window management and
agent stability features into the main 4S1T Agent AI application.
"""

import asyncio
import logging
import sys
import os
from typing import Optional

# Add parent directory to path to enable imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.stability.context_window import ContextWindowManager, ContextWindowConfig
from ai.stability.agent_stability import AgentStabilityManager, StabilityConfig
from ai.context.manager import ContextManager
from ai.models.base import ModelManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StableAgentAI:
    """
    Example integration of stability features into the 4S1T Agent AI.
    
    This class demonstrates how to integrate context window management
    and agent stability features into the main application.
    """
    
    def __init__(self):
        """Initialize the stable agent with all components."""
        # Core components
        self.model_manager = ModelManager()
        self.context_manager = ContextManager()
        
        # Stability components
        self.context_window_config = ContextWindowConfig(
            max_tokens=8192,  # Adjust based on your model
            auto_compaction_threshold=0.8,
            compaction_target_ratio=0.6,
            compaction_strategy="priority_based"
        )
        
        self.stability_config = StabilityConfig(
            heartbeat_interval_seconds=10,
            max_missed_heartbeats=3,
            auto_recovery_enabled=True,
            alert_on_disconnect=True
        )
        
        self.context_window_manager = ContextWindowManager(
            self.context_manager, 
            self.context_window_config
        )
        
        self.stability_manager = AgentStabilityManager(
            self.model_manager,
            self.context_manager,
            self.stability_config
        )
        
        self.current_conversation_id: Optional[str] = None
        self.is_running = False
    
    async def initialize(self):
        """Initialize the agent and start stability monitoring."""
        logger.info("Initializing StableAgentAI...")
        
        # Start stability monitoring
        await self.stability_manager.start_monitoring()
        
        # Create initial conversation
        self.current_conversation_id = self.context_manager.create_conversation(
            metadata={"agent_version": "stable_v1.0"}
        )
        
        self.is_running = True
        logger.info("StableAgentAI initialized successfully")
    
    async def process_message(self, user_message: str) -> str:
        """
        Process a user message with stability features.
        
        Args:
            user_message: The user's input message
            
        Returns:
            str: The agent's response
        """
        if not self.is_running:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        
        try:
            # Send heartbeat to indicate we're processing
            await self.stability_manager.send_heartbeat()
            
            # Add user message to context
            self.context_manager.add_entry(
                self.current_conversation_id,
                "user",
                user_message
            )
            
            # Check and compact context if needed
            await self.context_window_manager.check_and_compact_context(
                self.current_conversation_id
            )
            
            # Simulate AI processing (in real implementation, this would call the model)
            await asyncio.sleep(0.1)  # Simulate processing time
            
            # Generate response (mock implementation)
            response = self._generate_response(user_message)
            
            # Add response to context
            self.context_manager.add_entry(
                self.current_conversation_id,
                "assistant",
                response
            )
            
            # Record successful response
            await self.stability_manager.record_response()
            
            return response
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Record error
            self.stability_manager.current_status.error_count += 1
            self.stability_manager.current_status.consecutive_errors += 1
            raise
    
    def _generate_response(self, user_message: str) -> str:
        """
        Generate a response to the user message (mock implementation).
        
        In a real implementation, this would call an AI model.
        
        Args:
            user_message: The user's input message
            
        Returns:
            str: Generated response
        """
        # Simple response generation (in reality, this would use an AI model)
        responses = [
            f"I understand you're asking about '{user_message[:20]}...'. Let me help with that.",
            f"That's an interesting question about '{user_message[:15]}...'. Here's what I think:",
            f"Regarding '{user_message[:15]}...', I would suggest considering several factors.",
            f"Your query about '{user_message[:20]}...' touches on important concepts.",
            f"After analyzing '{user_message[:15]}...', I've concluded that..."
        ]
        
        import random
        return random.choice(responses)
    
    async def get_status(self) -> dict:
        """
        Get the current agent status.
        
        Returns:
            dict: Status information
        """
        return self.stability_manager.get_status_summary()
    
    async def shutdown(self):
        """Shutdown the agent gracefully."""
        logger.info("Shutting down StableAgentAI...")
        
        # Stop stability monitoring
        await self.stability_manager.stop_monitoring()
        
        self.is_running = False
        logger.info("StableAgentAI shut down successfully")


# Example usage
async def main():
    """Demonstrate the stable agent integration."""
    print("🚀 4S1T Agent AI - Stability Integration Demo")
    print("=" * 50)
    print()
    
    # Create and initialize agent
    agent = StableAgentAI()
    await agent.initialize()
    
    # Get initial status
    status = await agent.get_status()
    print(f"Initial agent status: {status['state']}")
    
    # Process some messages
    messages = [
        "Hello, what can you help me with?",
        "Can you explain artificial intelligence?",
        "How does machine learning work?",
        "What are neural networks?",
        "Tell me about deep learning.",
        "How do transformers work in AI?",
        "What is natural language processing?",
        "Can you explain computer vision?",
        "What are the ethical considerations in AI?",
        "How can I get started with AI development?"
    ]
    
    print("\nProcessing conversation...")
    for i, message in enumerate(messages):
        try:
            response = await agent.process_message(message)
            print(f"\nUser: {message}")
            print(f"Agent: {response}")
            
            # Show status every few messages
            if (i + 1) % 3 == 0:
                status = await agent.get_status()
                print(f"\n[Status] State: {status['state']}, "
                      f"Context entries: {status['context_entries']}, "
                      f"Errors: {status['consecutive_errors']}")
                      
        except Exception as e:
            print(f"Error processing message: {e}")
    
    # Get final status
    status = await agent.get_status()
    print(f"\nFinal agent status: {status['state']}")
    print(f"Total context entries: {status['context_entries']}")
    print(f"Total errors: {status['error_count']}")
    
    # Shutdown
    await agent.shutdown()
    print("\n✅ Demo completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
