"""
Integration test for NIP-17 message receiving with Chat Agent.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from communication.nip17.chat_agent import NIP17ChatAgent, create_chat_agent

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_chat_agent_message_receiving():
    """Test that chat agent can receive messages using the new polling mechanism."""
    
    logger.info("=" * 70)
    logger.info("Testing NIP-17 Chat Agent Message Receiving")
    logger.info("=" * 70)
    
    # Create chat agent
    logger.info("\nCreating NIP-17 Chat Agent...")
    try:
        agent = create_chat_agent()
    except Exception as e:
        logger.error(f"Failed to create chat agent: {e}")
        logger.info("\nPlease ensure NIP-17 configuration is set up in .env or config files")
        return
    
    # Start the agent
    logger.info("\nStarting NIP-17 Chat Agent...")
    if not await agent.start():
        logger.error("Failed to start chat agent")
        return
    
    logger.info(f"✓ Chat Agent started successfully")
    logger.info(f"  Agent npub: {agent.client.npub}")
    logger.info(f"  Connected to: {agent.client.active_relay}")
    
    # Wait a moment to ensure connection is stable
    await asyncio.sleep(2)
    
    # Test receiving messages
    logger.info("\n" + "=" * 70)
    logger.info("TEST: Receiving messages")
    logger.info("=" * 70)
    
    messages = await agent.receive_messages(since_seconds=300)
    logger.info(f"Result: {len(messages)} messages received")
    
    if messages:
        logger.info("\nMessages received:")
        for i, msg in enumerate(messages, 1):
            logger.info(f"\n  Message {i}:")
            logger.info(f"    Sender: {msg.sender_npub}")
            logger.info(f"    Type: {msg.message_type.value}")
            logger.info(f"    Content: {msg.content}")
            logger.info(f"    Timestamp: {msg.timestamp}")
            logger.info(f"    Relay: {msg.relay}")
    
    # Test with all historical messages
    logger.info("\n" + "=" * 70)
    logger.info("TEST: Receiving all historical messages (since_seconds=0)")
    logger.info("=" * 70)
    
    messages = await agent.receive_messages(since_seconds=0)
    logger.info(f"Result: {len(messages)} messages received")
    
    # Stop the agent
    logger.info("\nStopping NIP-17 Chat Agent...")
    await agent.stop()
    logger.info("✓ Chat Agent stopped")
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(test_chat_agent_message_receiving())
    except KeyboardInterrupt:
        logger.info("\n\nTest interrupted by user")
    except Exception as e:
        logger.error(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
