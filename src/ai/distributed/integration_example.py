"""
Integration example for distributed agent features.

This module shows how to integrate distributed agent coordination and
telemetry features into the 4S1T Agent AI system.
"""

import asyncio
import logging
import sys
import os
from typing import Optional

# Add parent directory to path to enable imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.distributed.coordination import DistributedAgentCoordinator, ClusterConfig
from ai.distributed.telemetry import TelemetryCollector, TelemetryConfig
from ai.stability.agent_stability import AgentStabilityManager, StabilityConfig
from ai.context.manager import ContextManager
from ai.models.base import ModelManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DistributedStableAgentAI:
    """
    Example integration of distributed agent features with stability management.
    
    This class demonstrates how to integrate distributed agent coordination,
    telemetry collection, and stability management into a cohesive system.
    """
    
    def __init__(self, agent_id: str, host: str = "localhost", port: int = 8080):
        """
        Initialize the distributed stable agent.
        
        Args:
            agent_id: Unique identifier for this agent
            host: Host address for this agent
            port: Port for agent communication
        """
        self.agent_id = agent_id
        self.host = host
        self.port = port
        
        # Core components
        self.model_manager = ModelManager()
        self.context_manager = ContextManager()
        
        # Distributed coordination
        self.cluster_config = ClusterConfig(
            cluster_name="4s1t-demo-cluster",
            heartbeat_interval_seconds=5,
            election_timeout_min_ms=150,
            election_timeout_max_ms=300
        )
        
        self.coordinator = DistributedAgentCoordinator(
            agent_id=agent_id,
            host=host,
            port=port,
            config=self.cluster_config
        )
        
        # Stability management
        self.stability_config = StabilityConfig(
            heartbeat_interval_seconds=10,
            max_missed_heartbeats=3,
            auto_recovery_enabled=True,
            alert_on_disconnect=True
        )
        
        self.stability_manager = AgentStabilityManager(
            self.model_manager,
            self.context_manager,
            self.stability_config
        )
        
        # Telemetry collection
        self.telemetry_config = TelemetryConfig(
            collect_interval_seconds=30,
            detailed_collection_interval_seconds=300,
            collect_system_metrics=True,
            collect_memory_metrics=True,
            collect_performance_metrics=True,
            collect_event_metrics=True
        )
        
        self.telemetry_collector = TelemetryCollector(
            agent_id=agent_id,
            config=self.telemetry_config
        )
        
        self.is_running = False
    
    async def initialize(self):
        """Initialize all components and start processes."""
        logger.info(f"Initializing DistributedStableAgentAI ({self.agent_id})...")
        
        try:
            # Initialize distributed coordinator
            await self.coordinator.initialize()
            
            # Initialize stability manager
            # Start monitoring is handled internally by the stability manager
            
            # Initialize telemetry collector
            await self.telemetry_collector.initialize()
            
            # Start stability monitoring
            await self.stability_manager.start_monitoring()
            
            self.is_running = True
            logger.info(f"DistributedStableAgentAI ({self.agent_id}) initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing DistributedStableAgentAI: {e}")
            raise
    
    async def simulate_workload(self, duration_seconds: int = 60):
        """
        Simulate agent workload for demonstration purposes.
        
        Args:
            duration_seconds: Duration to simulate workload
        """
        if not self.is_running:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        
        logger.info(f"Starting workload simulation for {duration_seconds} seconds...")
        
        start_time = asyncio.get_event_loop().time()
        iteration = 0
        
        while (asyncio.get_event_loop().time() - start_time) < duration_seconds:
            try:
                iteration += 1
                
                # Send heartbeat to stability manager
                await self.stability_manager.send_heartbeat()
                
                # Simulate processing work
                await asyncio.sleep(0.5)
                
                # Occasionally record successful responses
                if iteration % 5 == 0:
                    await self.stability_manager.record_response()
                
                # Occasionally add entries to context (to demonstrate compaction)
                if iteration % 3 == 0:
                    conv_id = self.context_manager.create_conversation(
                        metadata={"simulation": True}
                    )
                    self.context_manager.add_entry(
                        conv_id,
                        "user",
                        f"Simulation message #{iteration} - This is a test message to demonstrate context growth."
                    )
                
                # Occasionally add custom telemetry
                if iteration % 10 == 0:
                    await self.telemetry_collector.add_custom_telemetry(
                        entry_type="workload_simulation",
                        data={
                            "iteration": iteration,
                            "timestamp": asyncio.get_event_loop().time(),
                            "context_entries": len(self.context_manager.conversations)
                        },
                        severity="INFO",
                        tags=["simulation", "workload"]
                    )
                
                # Occasionally check cluster status
                if iteration % 15 == 0:
                    cluster_status = self.coordinator.get_cluster_status()
                    logger.info(f"Cluster status: {cluster_status.get('cluster_size', 0)} members")
                    
                    # Add cluster status to telemetry
                    await self.telemetry_collector.add_custom_telemetry(
                        entry_type="cluster_status",
                        data=cluster_status,
                        severity="INFO",
                        tags=["cluster", "status"]
                    )
                
                # Occasionally get telemetry summary
                if iteration % 20 == 0:
                    telemetry_summary = self.telemetry_collector.get_telemetry_summary()
                    logger.debug(f"Telemetry summary: {telemetry_summary.get('custom_telemetry_count', 0)} entries")
            
            except Exception as e:
                logger.error(f"Error during workload simulation iteration {iteration}: {e}")
                # Record error in stability manager
                self.stability_manager.current_status.error_count += 1
                self.stability_manager.current_status.consecutive_errors += 1
    
    async def get_detailed_status(self) -> dict:
        """
        Get detailed status including cluster and telemetry information.
        
        Returns:
            dict: Comprehensive status information
        """
        status = {
            "agent_id": self.agent_id,
            "timestamp": asyncio.get_event_loop().time(),
            "stability": self.stability_manager.get_status_summary(),
            "cluster": self.coordinator.get_cluster_status(),
            "telemetry": self.telemetry_collector.get_telemetry_summary()
        }
        return status
    
    async def shutdown(self):
        """Shutdown all components gracefully."""
        logger.info(f"Shutting down DistributedStableAgentAI ({self.agent_id})...")
        
        try:
            # Shutdown telemetry collector
            await self.telemetry_collector.shutdown()
            
            # Stop stability monitoring
            await self.stability_manager.stop_monitoring()
            
            # Shutdown coordinator
            await self.coordinator.shutdown()
            
            self.is_running = False
            logger.info(f"DistributedStableAgentAI ({self.agent_id}) shut down successfully")
            
        except Exception as e:
            logger.error(f"Error shutting down DistributedStableAgentAI: {e}")


# Example usage
async def main():
    """Demonstrate the distributed stable agent integration."""
    print("🚀 4S1T Agent AI - Distributed Stability Integration Demo")
    print("=" * 60)
    print()
    
    # Create and initialize agent
    agent = DistributedStableAgentAI(
        agent_id="demo-agent-001",
        host="localhost",
        port=8080
    )
    
    try:
        await agent.initialize()
        
        # Get initial status
        status = await agent.get_detailed_status()
        print(f"Initial agent status:")
        print(f"  - Agent ID: {status['agent_id']}")
        print(f"  - Cluster size: {status['cluster'].get('cluster_size', 0)}")
        print(f"  - Custom telemetry entries: {status['telemetry'].get('custom_telemetry_count', 0)}")
        print()
        
        # Simulate workload
        print("Simulating agent workload for 30 seconds...")
        await agent.simulate_workload(duration_seconds=30)
        print("Workload simulation completed.\n")
        
        # Get final status
        status = await agent.get_detailed_status()
        print("Final agent status:")
        print(f"  - Agent state: {status['stability']['state']}")
        print(f"  - Model status: {status['stability']['model_status']}")
        print(f"  - Error count: {status['stability']['error_count']}")
        print(f"  - Cluster size: {status['cluster'].get('cluster_size', 0)}")
        print(f"  - Custom telemetry entries: {status['telemetry'].get('custom_telemetry_count', 0)}")
        
        # Show some telemetry data
        system_metrics = status['telemetry'].get('system_metrics', {})
        if system_metrics:
            latest_system = system_metrics.get('latest', {})
            print(f"  - CPU usage: {latest_system.get('cpu_percent', 0):.1f}%")
            print(f"  - Memory usage: {latest_system.get('memory_percent', 0):.1f}%")
        
        print()
        
        # Shutdown
        await agent.shutdown()
        print("✅ Demo completed successfully!")
        
    except Exception as e:
        print(f"❌ Demo failed with error: {e}")
        # Try to shutdown anyway
        try:
            await agent.shutdown()
        except:
            pass


if __name__ == "__main__":
    asyncio.run(main())
