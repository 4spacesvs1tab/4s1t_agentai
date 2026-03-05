"""
Test module for distributed agent features.

This module demonstrates how to use the distributed agent coordination
and telemetry features in the 4S1T Agent AI framework.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

# Add parent directory to path to enable imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.distributed.coordination import DistributedAgentCoordinator, ClusterConfig, AgentRole
from ai.distributed.telemetry import TelemetryCollector, TelemetryConfig, TelemetryEntry
from components.events.event_bus import Event, publish

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo_distributed_coordination():
    """Demonstrate distributed agent coordination features."""
    print("=== Distributed Agent Coordination Demo ===")
    
    # Create cluster configuration
    cluster_config = ClusterConfig(
        cluster_name="test-cluster",
        heartbeat_interval_seconds=2,
        election_timeout_min_ms=100,
        election_timeout_max_ms=200,
        max_missed_heartbeats=2
    )
    
    # Create coordinator
    coordinator = DistributedAgentCoordinator(
        agent_id="test-agent-001",
        host="localhost",
        port=8080,
        config=cluster_config
    )
    
    try:
        # Initialize coordinator
        await coordinator.initialize()
        print(f"Coordinator initialized for agent: {coordinator.agent_id}")
        
        # Simulate joining another agent
        join_event = Event(
            event_type="cluster.member_join",
            payload={
                "agent_id": "test-agent-002",
                "host": "localhost",
                "port": 8081,
                "capabilities": ["processing", "storage"],
                "metadata": {"version": "1.0"}
            },
            source="test_suite"
        )
        await publish(join_event)
        
        # Wait a moment for event processing
        await asyncio.sleep(1)
        
        # Check cluster status
        cluster_status = coordinator.get_cluster_status()
        print(f"Cluster size: {cluster_status.get('cluster_size', 0)}")
        print(f"Members: {list(cluster_status.get('members', {}).keys())}")
        
        # Simulate heartbeat from other agent
        heartbeat_event = Event(
            event_type="cluster.heartbeat",
            payload={
                "sender_id": "test-agent-002",
                "term": 1,
                "leader_id": "test-agent-002"
            },
            source="test_suite"
        )
        await publish(heartbeat_event)
        
        # Wait for processing
        await asyncio.sleep(1)
        
        # Check if we recognized the leader
        leader_info = coordinator.get_leader_info()
        if leader_info:
            print(f"Current leader: {leader_info.agent_id}")
        else:
            print("No leader currently identified")
        
        # Simulate some time passing to test election
        print("Simulating time passage to test election timeout...")
        coordinator.last_heartbeat_received = datetime.now() - \
            coordinator.config.election_timeout_max_ms / 1000.0
        
        # Wait for election process to potentially trigger
        await asyncio.sleep(3)
        
        print(f"Consensus state: {coordinator.consensus_state.value}")
        print(f"Current term: {coordinator.current_term}")
        
    finally:
        # Shutdown coordinator
        await coordinator.shutdown()
        print("Coordinator shutdown completed")
    
    print()


async def demo_telemetry_collection():
    """Demonstrate telemetry collection features."""
    print("=== Telemetry Collection Demo ===")
    
    # Create telemetry configuration
    telemetry_config = TelemetryConfig(
        collect_interval_seconds=1,
        detailed_collection_interval_seconds=5,
        collect_system_metrics=True,
        collect_memory_metrics=True,
        collect_event_metrics=True,
        event_sampling_rate=1.0  # Sample all events for demo
    )
    
    # Create telemetry collector
    collector = TelemetryCollector(
        agent_id="telemetry-test-agent",
        config=telemetry_config
    )
    
    try:
        # Initialize collector
        await collector.initialize()
        print(f"Telemetry collector initialized for agent: {collector.agent_id}")
        
        # Simulate some events
        print("Simulating events...")
        
        # Agent status update
        status_event = Event(
            event_type="agent.status_update",
            payload={
                "agent_id": "telemetry-test-agent",
                "state": "processing",
                "model_status": "loaded",
                "context_entries": 15,
                "error_count": 0
            },
            source="test_suite"
        )
        await publish(status_event)
        
        # Health check
        health_event = Event(
            event_type="health.check_result",
            payload={
                "component": "model",
                "status": "healthy",
                "message": "Model operating normally"
            },
            source="test_suite"
        )
        await publish(health_event)
        
        # Agent alert
        alert_event = Event(
            event_type="agent.alert",
            payload={
                "message": "High memory usage detected",
                "severity": "warning",
                "agent_id": "telemetry-test-agent"
            },
            source="test_suite"
        )
        await publish(alert_event)
        
        # Wait for collection cycles
        print("Waiting for telemetry collection...")
        await asyncio.sleep(3)
        
        # Add some custom telemetry
        await collector.add_custom_telemetry(
            entry_type="test_metric",
            data={
                "test_value": 42,
                "description": "Sample test metric"
            },
            severity="INFO",
            tags=["test", "demo"]
        )
        
        # Get telemetry summary
        summary = collector.get_telemetry_summary()
        print(f"Telemetry summary:")
        print(f"  - Custom telemetry entries: {summary.get('custom_telemetry_count', 0)}")
        
        system_metrics = summary.get('system_metrics', {})
        if system_metrics:
            latest_system = system_metrics.get('latest', {})
            print(f"  - CPU usage: {latest_system.get('cpu_percent', 0):.1f}%")
            print(f"  - Memory usage: {latest_system.get('memory_percent', 0):.1f}%")
        
        # Get specific telemetry entries
        custom_entries = collector.get_custom_telemetry(entry_type="test_metric")
        print(f"  - Test metric entries: {len(custom_entries)}")
        
        event_metrics = collector.get_event_metrics_summary()
        if event_metrics:
            latest_events = event_metrics.get('latest', {})
            print(f"  - Events processed: {latest_events.get('events_processed', 0)}")
            print(f"  - Event types tracked: {len(latest_events.get('event_types', {}))}")
        
    finally:
        # Shutdown collector
        await collector.shutdown()
        print("Telemetry collector shutdown completed")
    
    print()


async def demo_integration():
    """Demonstrate integration of coordination and telemetry."""
    print("=== Integration Demo ===")
    
    # Create both components
    cluster_config = ClusterConfig(
        cluster_name="integration-test-cluster",
        heartbeat_interval_seconds=1
    )
    
    telemetry_config = TelemetryConfig(
        collect_interval_seconds=2,
        event_sampling_rate=1.0
    )
    
    coordinator = DistributedAgentCoordinator(
        agent_id="integration-agent",
        host="localhost",
        port=9090,
        config=cluster_config
    )
    
    collector = TelemetryCollector(
        agent_id="integration-agent",
        config=telemetry_config
    )
    
    try:
        # Initialize both components
        await coordinator.initialize()
        await collector.initialize()
        print("Both coordinator and collector initialized")
        
        # Add telemetry about cluster initialization
        await collector.add_custom_telemetry(
            entry_type="cluster_init",
            data={
                "agent_id": coordinator.agent_id,
                "cluster_name": coordinator.config.cluster_name,
                "role": coordinator.role.value
            },
            severity="INFO",
            tags=["cluster", "initialization"]
        )
        
        # Simulate cluster activity
        member_join_event = Event(
            event_type="cluster.member_join",
            payload={
                "agent_id": "peer-agent-001",
                "host": "localhost",
                "port": 9091,
                "capabilities": ["processing"]
            },
            source="integration_test"
        )
        await publish(member_join_event)
        
        # Wait for processing
        await asyncio.sleep(2)
        
        # Check cluster status and add to telemetry
        cluster_status = coordinator.get_cluster_status()
        await collector.add_custom_telemetry(
            entry_type="cluster_status",
            data=cluster_status,
            severity="INFO",
            tags=["cluster", "status"]
        )
        
        print(f"Cluster status recorded in telemetry:")
        print(f"  - Cluster size: {cluster_status.get('cluster_size', 0)}")
        print(f"  - Current role: {cluster_status.get('consensus_state')}")
        
        # Get comprehensive telemetry summary
        telemetry_summary = collector.get_telemetry_summary()
        print(f"Comprehensive telemetry summary:")
        print(f"  - Total custom entries: {telemetry_summary.get('custom_telemetry_count', 0)}")
        print(f"  - System metrics history: {telemetry_summary.get('system_metrics', {}).get('history_length', 0)}")
        
    finally:
        # Shutdown both components
        await collector.shutdown()
        await coordinator.shutdown()
        print("Both components shutdown completed")
    
    print()


async def main():
    """Run all demos."""
    print("🚀 4S1T Agent AI - Distributed Features Demo")
    print("=" * 50)
    print()
    
    # Run distributed coordination demo
    await demo_distributed_coordination()
    
    # Run telemetry collection demo
    await demo_telemetry_collection()
    
    # Run integration demo
    await demo_integration()
    
    print("✅ All demos completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
