"""
Distributed Agent Coordination for 4S1T Agent AI Framework.

This module provides coordination capabilities for managing multiple AI agents
in distributed environments, including leader election, consensus, and 
cross-agent communication.
"""

import asyncio
import uuid
from typing import Dict, List, Optional, Set, Callable, Awaitable, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json

from ai.stability.agent_stability import AgentStatus, AgentState
from components.events.event_bus import Event, get_event_bus, publish, subscribe
from components.health.monitor import HealthStatus, HealthCheckResult

from utils.logger import setup_logger
logger = setup_logger(__name__)


class AgentRole(Enum):
    """Roles that agents can have in a distributed system."""
    LEADER = "leader"
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    OBSERVER = "observer"


class ConsensusState(Enum):
    """States for consensus protocol."""
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass
class DistributedAgentInfo:
    """Information about a distributed agent."""
    agent_id: str
    host: str
    port: int
    role: AgentRole
    status: AgentState
    last_heartbeat: datetime
    health_status: HealthStatus
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClusterConfig:
    """Configuration for distributed agent cluster."""
    
    # Cluster settings
    cluster_name: str = "4s1t-agent-cluster"
    heartbeat_interval_seconds: int = 5
    election_timeout_min_ms: int = 150
    election_timeout_max_ms: int = 300
    leader_heartbeat_interval_ms: int = 50
    
    # Failure detection
    max_missed_heartbeats: int = 3
    failure_detection_window_seconds: int = 30
    
    # Communication
    gossip_interval_seconds: int = 10
    sync_batch_size: int = 100
    
    # Consensus
    quorum_size: int = 2  # Minimum for 3-node cluster


class DistributedAgentCoordinator:
    """
    Coordinates multiple AI agents in a distributed environment.
    
    Implements a simplified Raft-like consensus protocol for leader election
    and state synchronization among distributed agents.
    """
    
    def __init__(self, agent_id: str, host: str, port: int, config: ClusterConfig = None):
        """
        Initialize the distributed agent coordinator.
        
        Args:
            agent_id: Unique identifier for this agent
            host: Host address for this agent
            port: Port for agent communication
            config: Cluster configuration
        """
        self.agent_id = agent_id
        self.host = host
        self.port = port
        self.config = config or ClusterConfig()
        
        self.role = AgentRole.FOLLOWER
        self.consensus_state = ConsensusState.FOLLOWER
        self.current_term = 0
        self.voted_for = None
        self.leader_id = None
        self.last_heartbeat_received = datetime.now()
        
        # Cluster state
        self.cluster_members: Dict[str, DistributedAgentInfo] = {}
        self.known_agents: Set[str] = {agent_id}
        
        # Election state
        self.election_timer: Optional[asyncio.TimerHandle] = None
        self.heartbeat_timer: Optional[asyncio.TimerHandle] = None
        
        # Event handling
        self.event_bus = get_event_bus()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        
        self.logger = logger
        
        # Initialize self in cluster members
        self.cluster_members[agent_id] = DistributedAgentInfo(
            agent_id=agent_id,
            host=host,
            port=port,
            role=AgentRole.FOLLOWER,
            status=AgentState.IDLE,
            last_heartbeat=datetime.now(),
            health_status=HealthStatus.HEALTHY,
            capabilities=["coordination"],
            metadata={"initialized": datetime.now().isoformat()}
        )
    
    async def initialize(self):
        """Initialize the coordinator and start coordination processes."""
        try:
            # Subscribe to relevant events
            await self._setup_event_subscriptions()
            
            # Start coordination processes
            self.active_tasks["heartbeat"] = asyncio.create_task(self._heartbeat_process())
            self.active_tasks["election"] = asyncio.create_task(self._election_process())
            self.active_tasks["gossip"] = asyncio.create_task(self._gossip_process())
            
            self.logger.info(f"Distributed coordinator initialized for agent {self.agent_id}")
            
        except Exception as e:
            self.logger.error(f"Error initializing distributed coordinator: {e}")
            raise
    
    async def _setup_event_subscriptions(self):
        """Set up event subscriptions for distributed coordination."""
        try:
            await subscribe("agent.status_update", self._on_agent_status_update)
            await subscribe("agent.heartbeat", self._on_heartbeat)
            await subscribe("agent.alert", self._on_agent_alert)
            await subscribe("health.check_result", self._on_health_check)
            await subscribe("cluster.member_join", self._on_member_join)
            await subscribe("cluster.member_leave", self._on_member_leave)
        except Exception as e:
            self.logger.error(f"Failed to set up event subscriptions: {e}")
    
    async def _on_agent_status_update(self, event: Event):
        """Handle agent status update events."""
        try:
            agent_info = event.payload
            
            # Update our own status in cluster members
            if agent_info.get("agent_id") == self.agent_id:
                if self.agent_id in self.cluster_members:
                    member = self.cluster_members[self.agent_id]
                    member.status = AgentState(agent_info.get("state", "idle"))
                    member.last_heartbeat = datetime.now()
                    
                    # If we're the leader, broadcast status update
                    if self.consensus_state == ConsensusState.LEADER:
                        await self._broadcast_cluster_update()
            
            self.logger.debug(f"Agent status update processed: {agent_info.get('agent_id')}")
            
        except Exception as e:
            self.logger.error(f"Error handling agent status update: {e}")
    
    async def _on_heartbeat(self, event: Event):
        """Handle heartbeat events from other agents."""
        try:
            sender_id = event.payload.get("sender_id")
            term = event.payload.get("term", 0)
            
            # Update last heartbeat received time
            self.last_heartbeat_received = datetime.now()
            
            # If we receive a heartbeat from a leader with higher term, step down
            if (self.consensus_state == ConsensusState.CANDIDATE or 
                self.consensus_state == ConsensusState.LEADER) and term > self.current_term:
                await self._become_follower(term)
            
            # Update cluster member info
            if sender_id and sender_id in self.cluster_members:
                self.cluster_members[sender_id].last_heartbeat = datetime.now()
            
            self.logger.debug(f"Heartbeat received from {sender_id} (term {term})")
            
        except Exception as e:
            self.logger.error(f"Error handling heartbeat: {e}")
    
    async def _on_agent_alert(self, event: Event):
        """Handle agent alert events."""
        try:
            alert_info = event.payload
            severity = alert_info.get("severity", "info")
            
            # If this is a critical alert from our agent, consider stepping down if leader
            if (alert_info.get("agent_id") == self.agent_id and 
                severity == "critical" and 
                self.consensus_state == ConsensusState.LEADER):
                self.logger.warning("Critical alert from local agent, considering leadership step-down")
                # In a real implementation, we might trigger a new election
            
            self.logger.debug(f"Agent alert processed: {alert_info.get('message')}")
            
        except Exception as e:
            self.logger.error(f"Error handling agent alert: {e}")
    
    async def _on_health_check(self, event: Event):
        """Handle health check results."""
        try:
            health_result = event.payload
            component = health_result.get("component")
            status = health_result.get("status")
            
            # Update our health status in cluster members
            if self.agent_id in self.cluster_members:
                health_status_map = {
                    "healthy": HealthStatus.HEALTHY,
                    "degraded": HealthStatus.DEGRADED,
                    "unhealthy": HealthStatus.UNHEALTHY
                }
                self.cluster_members[self.agent_id].health_status = health_status_map.get(
                    status, HealthStatus.UNKNOWN
                )
            
            self.logger.debug(f"Health check result processed: {component} is {status}")
            
        except Exception as e:
            self.logger.error(f"Error handling health check: {e}")
    
    async def _on_member_join(self, event: Event):
        """Handle cluster member join events."""
        try:
            member_info = event.payload
            agent_id = member_info.get("agent_id")
            
            if agent_id and agent_id not in self.cluster_members:
                self.cluster_members[agent_id] = DistributedAgentInfo(
                    agent_id=agent_id,
                    host=member_info.get("host", ""),
                    port=member_info.get("port", 0),
                    role=AgentRole.FOLLOWER,
                    status=AgentState.IDLE,
                    last_heartbeat=datetime.now(),
                    health_status=HealthStatus.UNKNOWN,
                    capabilities=member_info.get("capabilities", []),
                    metadata=member_info.get("metadata", {})
                )
                self.known_agents.add(agent_id)
                
                self.logger.info(f"New cluster member joined: {agent_id}")
                
                # If we're the leader, broadcast updated cluster state
                if self.consensus_state == ConsensusState.LEADER:
                    await self._broadcast_cluster_update()
            
        except Exception as e:
            self.logger.error(f"Error handling member join: {e}")
    
    async def _on_member_leave(self, event: Event):
        """Handle cluster member leave events."""
        try:
            member_info = event.payload
            agent_id = member_info.get("agent_id")
            
            if agent_id and agent_id in self.cluster_members:
                del self.cluster_members[agent_id]
                self.known_agents.discard(agent_id)
                
                self.logger.info(f"Cluster member left: {agent_id}")
                
                # If the leaving member was the leader, trigger new election
                if agent_id == self.leader_id:
                    self.leader_id = None
                    await self._start_election()
            
        except Exception as e:
            self.logger.error(f"Error handling member leave: {e}")
    
    async def _heartbeat_process(self):
        """Process for sending and receiving heartbeats."""
        while True:
            try:
                if self.consensus_state == ConsensusState.LEADER:
                    await self._send_heartbeat()
                
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in heartbeat process: {e}")
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
    
    async def _send_heartbeat(self):
        """Send heartbeat to all cluster members."""
        try:
            heartbeat_event = Event(
                event_type="cluster.heartbeat",
                payload={
                    "sender_id": self.agent_id,
                    "term": self.current_term,
                    "leader_id": self.agent_id,
                    "timestamp": datetime.now().isoformat()
                },
                source="distributed_coordinator",
                priority="HIGH"
            )
            
            await publish(heartbeat_event)
            self.logger.debug("Heartbeat sent to cluster")
            
        except Exception as e:
            self.logger.error(f"Error sending heartbeat: {e}")
    
    async def _election_process(self):
        """Process for handling leader elections."""
        while True:
            try:
                # Check if we should start an election (follower/candidate timeout)
                time_since_last_heartbeat = (
                    datetime.now() - self.last_heartbeat_received
                ).total_seconds()
                
                election_timeout = self.config.election_timeout_max_ms / 1000.0
                
                if (self.consensus_state in [ConsensusState.FOLLOWER, ConsensusState.CANDIDATE] and
                    time_since_last_heartbeat > election_timeout):
                    await self._start_election()
                
                await asyncio.sleep(1)  # Check every second
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in election process: {e}")
                await asyncio.sleep(1)
    
    async def _start_election(self):
        """Start a leader election."""
        try:
            self.logger.info("Starting leader election")
            
            # Increment term and become candidate
            self.current_term += 1
            await self._become_candidate()
            
            # Vote for ourselves
            votes_received = 1  # Our own vote
            votes_needed = len(self.cluster_members) // 2 + 1
            
            # Request votes from other agents
            vote_request_event = Event(
                event_type="cluster.request_vote",
                payload={
                    "candidate_id": self.agent_id,
                    "term": self.current_term,
                    "timestamp": datetime.now().isoformat()
                },
                source="distributed_coordinator",
                priority="HIGH"
            )
            
            await publish(vote_request_event)
            
            # Wait for votes (simplified - in real implementation would have timeout)
            await asyncio.sleep(2)
            
            # Check if we have enough votes
            if votes_received >= votes_needed:
                await self._become_leader()
            else:
                # Become follower again
                await self._become_follower(self.current_term)
                
        except Exception as e:
            self.logger.error(f"Error starting election: {e}")
    
    async def _become_candidate(self):
        """Transition to candidate state."""
        try:
            self.consensus_state = ConsensusState.CANDIDATE
            self.role = AgentRole.CANDIDATE
            self.voted_for = self.agent_id  # Vote for ourselves
            
            # Update our entry in cluster members
            if self.agent_id in self.cluster_members:
                self.cluster_members[self.agent_id].role = AgentRole.CANDIDATE
            
            self.logger.info(f"Became candidate for term {self.current_term}")
            
        except Exception as e:
            self.logger.error(f"Error becoming candidate: {e}")
    
    async def _become_leader(self):
        """Transition to leader state."""
        try:
            self.consensus_state = ConsensusState.LEADER
            self.role = AgentRole.LEADER
            self.leader_id = self.agent_id
            
            # Update our entry in cluster members
            if self.agent_id in self.cluster_members:
                self.cluster_members[self.agent_id].role = AgentRole.LEADER
            
            # Start sending heartbeats
            self.heartbeat_timer = asyncio.get_event_loop().call_later(
                self.config.leader_heartbeat_interval_ms / 1000.0,
                lambda: asyncio.create_task(self._send_heartbeat())
            )
            
            self.logger.info(f"Became leader for term {self.current_term}")
            
            # Broadcast cluster update
            await self._broadcast_cluster_update()
            
        except Exception as e:
            self.logger.error(f"Error becoming leader: {e}")
    
    async def _become_follower(self, term: int):
        """Transition to follower state."""
        try:
            self.current_term = term
            self.consensus_state = ConsensusState.FOLLOWER
            self.role = AgentRole.FOLLOWER
            self.voted_for = None
            
            # Update our entry in cluster members
            if self.agent_id in self.cluster_members:
                self.cluster_members[self.agent_id].role = AgentRole.FOLLOWER
            
            self.logger.info(f"Became follower for term {term}")
            
        except Exception as e:
            self.logger.error(f"Error becoming follower: {e}")
    
    async def _gossip_process(self):
        """Process for exchanging cluster state information."""
        while True:
            try:
                # Periodically exchange cluster membership information
                await self._exchange_cluster_info()
                
                await asyncio.sleep(self.config.gossip_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in gossip process: {e}")
                await asyncio.sleep(self.config.gossip_interval_seconds)
    
    async def _exchange_cluster_info(self):
        """Exchange cluster membership information with other agents."""
        try:
            # Send our cluster view to others
            gossip_event = Event(
                event_type="cluster.gossip",
                payload={
                    "sender_id": self.agent_id,
                    "cluster_members": {
                        agent_id: {
                            "agent_id": info.agent_id,
                            "host": info.host,
                            "port": info.port,
                            "role": info.role.value,
                            "status": info.status.value,
                            "last_heartbeat": info.last_heartbeat.isoformat(),
                            "health_status": info.health_status.value,
                            "capabilities": info.capabilities
                        }
                        for agent_id, info in self.cluster_members.items()
                    },
                    "timestamp": datetime.now().isoformat()
                },
                source="distributed_coordinator"
            )
            
            await publish(gossip_event)
            
        except Exception as e:
            self.logger.error(f"Error exchanging cluster info: {e}")
    
    async def _broadcast_cluster_update(self):
        """Broadcast current cluster state to all members."""
        try:
            update_event = Event(
                event_type="cluster.update",
                payload={
                    "leader_id": self.agent_id,
                    "term": self.current_term,
                    "cluster_members": {
                        agent_id: {
                            "agent_id": info.agent_id,
                            "host": info.host,
                            "port": info.port,
                            "role": info.role.value,
                            "status": info.status.value,
                            "last_heartbeat": info.last_heartbeat.isoformat(),
                            "health_status": info.health_status.value,
                            "capabilities": info.capabilities
                        }
                        for agent_id, info in self.cluster_members.items()
                    },
                    "timestamp": datetime.now().isoformat()
                },
                source="distributed_coordinator",
                priority="HIGH"
            )
            
            await publish(update_event)
            
        except Exception as e:
            self.logger.error(f"Error broadcasting cluster update: {e}")
    
    def get_cluster_status(self) -> Dict[str, Any]:
        """
        Get current cluster status.
        
        Returns:
            Dict with cluster status information
        """
        try:
            # Count agents by role
            role_counts = {}
            health_counts = {}
            
            for member in self.cluster_members.values():
                role = member.role.value
                health = member.health_status.value
                
                role_counts[role] = role_counts.get(role, 0) + 1
                health_counts[health] = health_counts.get(health, 0) + 1
            
            return {
                "agent_id": self.agent_id,
                "cluster_name": self.config.cluster_name,
                "consensus_state": self.consensus_state.value,
                "current_term": self.current_term,
                "leader_id": self.leader_id,
                "cluster_size": len(self.cluster_members),
                "role_distribution": role_counts,
                "health_distribution": health_counts,
                "members": {
                    agent_id: {
                        "agent_id": info.agent_id,
                        "host": info.host,
                        "port": info.port,
                        "role": info.role.value,
                        "status": info.status.value,
                        "last_heartbeat": info.last_heartbeat.isoformat(),
                        "health_status": info.health_status.value,
                        "capabilities": info.capabilities
                    }
                    for agent_id, info in self.cluster_members.items()
                },
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting cluster status: {e}")
            return {}
    
    def get_leader_info(self) -> Optional[DistributedAgentInfo]:
        """
        Get information about the current leader.
        
        Returns:
            Leader agent info or None if no leader
        """
        if self.leader_id and self.leader_id in self.cluster_members:
            return self.cluster_members[self.leader_id]
        return None
    
    async def shutdown(self):
        """Shutdown the distributed coordinator."""
        try:
            # Cancel all active tasks
            for task_name, task in self.active_tasks.items():
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self.active_tasks.clear()
            
            # Send leave event
            leave_event = Event(
                event_type="cluster.member_leave",
                payload={
                    "agent_id": self.agent_id,
                    "timestamp": datetime.now().isoformat()
                },
                source="distributed_coordinator"
            )
            await publish(leave_event)
            
            self.logger.info(f"Distributed coordinator shutdown for agent {self.agent_id}")
            
        except Exception as e:
            self.logger.error(f"Error shutting down distributed coordinator: {e}")
