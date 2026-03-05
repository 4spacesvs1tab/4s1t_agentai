"""
Test script for NIP-17 message receiving functionality.
Tests the new get_events_of() polling mechanism.
"""
import asyncio
import logging
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from communication.nip17 import NIP17NostrClient, RelayConfig

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_message_receiving():
    """Test receiving messages using the new get_events_of() method."""
    
    logger.info("=" * 70)
    logger.info("Testing NIP-17 Message Receiving with get_events_of()")
    logger.info("=" * 70)
    
    # Configuration - Update with your test values
    RELAYS = [
        RelayConfig(url="wss://relay.damus.io", priority=1),
        RelayConfig(url="wss://relay.nostr.band", priority=2),
    ]
    
    # Update with your actual keys
    NSEC = "your_nsec_here"  # Replace with your test nsec
    RECIPIENT_NPUB = "your_recipient_npub_here"  # Replace with test recipient
    
    # Test message to send
    TEST_MESSAGE = "Test message from NIP-17 receiving test"
    
    logger.info(f"Configuration:")
    logger.info(f"  Relays: {len(RELAYS)}")
    logger.info(f"  Recipient: {RECIPIENT_NPUB}")
    
    # Create client
    try:
        client = NIP17NostrClient(
            relay_configs=RELAYS,
            private_key=NSEC,
            recipient_npub=RECIPIENT_NPUB
        )
    except Exception as e:
        logger.error(f"Failed to create client: {e}")
        logger.info("\nPlease update the script with your actual nsec and recipient npub")
        return
    
    # Add a simple message handler
    def message_handler(msg):
        logger.info(f"\n{'='*70}")
        logger.info("✓ MESSAGE RECEIVED!")
        logger.info(f"{'='*70}")
        logger.info(f"  Sender: {msg.sender_npub}")
        logger.info(f"  Message Type: {msg.message_type.value}")
        logger.info(f"  Content: {msg.content}")
        logger.info(f"  Timestamp: {msg.timestamp}")
        logger.info(f"  Relay: {msg.relay}")
        logger.info(f"{'='*70}\n")
    
    client.add_message_handler(message_handler)
    
    # Connect to relay
    logger.info("\nConnecting to relay...")
    if not await client.connect_to_primary():
        logger.error("Failed to connect to relay")
        return
    
    # Test 1: Receive messages with default time window (300 seconds)
    logger.info("\n" + "=" * 70)
    logger.info("TEST 1: Receiving messages from last 300 seconds")
    logger.info("=" * 70)
    
    messages = await client.receive_messages(since_seconds=300)
    logger.info(f"Result: {len(messages)} messages received")
    
    # Test 2: Receive all historical messages (0 seconds)
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Receiving all historical messages (0 seconds)")
    logger.info("=" * 70)
    
    messages = await client.receive_messages(since_seconds=0)
    logger.info(f"Result: {len(messages)} messages received")
    
    # Test 3: Test sending a message and then receiving it
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: Send message and then receive it")
    logger.info("=" * 70)
    
    logger.info(f"Sending test message: {TEST_MESSAGE}")
    try:
        event_id = await client.send_encrypted_dm(TEST_MESSAGE)
        logger.info(f"✓ Message sent successfully: {event_id}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
    
    # Wait a moment and try to receive
    await asyncio.sleep(2)
    
    messages = await client.receive_messages(since_seconds=0)
    logger.info(f"Result: {len(messages)} messages received after sending")
    
    if messages:
        logger.info(f"Message from: {messages[0].sender_npub}")
        logger.info(f"Content: {messages[0].content}")
    
    # Disconnect
    logger.info("\nDisconnecting from relay...")
    await client.disconnect_all()
    logger.info("✓ Disconnected")
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(test_message_receiving())
    except KeyboardInterrupt:
        logger.info("\n\nTest interrupted by user")
    except Exception as e:
        logger.error(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
