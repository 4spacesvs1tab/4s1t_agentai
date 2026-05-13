"""
Approval Gateway Service
Handles approval workflow for code execution and sensitive operations.

Integrates with:
- Authy for push notifications
- Main service for token generation
- Executor service for code execution

Features:
- Risk assessment of code
- Authy push approval
- Signed token generation
- Approval state management
"""

import asyncio
import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
from uuid import uuid4

from services.authy_service import get_authy_service, AuthyService
from services.executor.security import generate_approval_token, ApprovalTokenGenerator

logger = logging.getLogger(__name__)


class ApprovalStatus(Enum):
    """Approval request status."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


class RiskLevel(Enum):
    """Risk assessment levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ApprovalRequest:
    """Represents an approval request."""
    id: str
    user_id: str
    operation_type: str
    operation_data: Dict[str, Any]
    risk_level: RiskLevel
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    approved_at: Optional[datetime] = None
    approval_token: Optional[str] = None
    authy_request_id: Optional[str] = None
    denial_reason: Optional[str] = None
    code_hash: str = field(default="")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'operation_type': self.operation_type,
            'operation_data': self.operation_data,
            'risk_level': self.risk_level.value,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat(),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'code_hash': self.code_hash,
        }


class CodeRiskAnalyzer:
    """Analyze code for security risks."""
    
    # Risk patterns
    CRITICAL_PATTERNS = [
        # Network access
        r'import\s+socket',
        r'urllib\.',
        r'requests\.',
        r'httpx\.',
        r'aiohttp\.',
        r'\.connect\s*\(',
        r'socket\.',
        
        # System access
        r'os\.system',
        r'subprocess\.',
        r'platform\.',
        
        # Dangerous builtins
        r'exec\s*\(',
        r'eval\s*\(',
        r'compile\s*\(',
        r'__import__',
        
        # File operations (in critical)
        r'open\s*\([^)]*[\'"]w',
        r'\.write\s*\(',
        r'shutil\.',
        r'os\.remove',
        r'os\.rmdir',
    ]
    
    HIGH_PATTERNS = [
        # File system
        r'open\s*\(',
        r'os\.path\.',
        r'pathlib\.',
        r'glob\.',
        r'\.read\s*\(',
        r'__file__',
        
        # Import statements
        r'import\s+os',
        r'import\s+sys',
        r'import\s+subprocess',
        
        # Self-reference
        r'__main__',
        r'sys\.modules',
        
        # Crypto
        r'base64\.',
        r'hashlib\.',
    ]
    
    MEDIUM_PATTERNS = [
        # Data handling
        r'json\.',
        r'pickle\.',
        r'yaml\.',
        
        # String manipulation for code
        r'\.format\s*\(',
        r'f["\'].*\.*\}',
        r'\.join\s*\(',
        
        # Dynamic attributes
        r'getattr\s*\(',
        r'setattr\s*\(',
        r'hasattr\s*\(',
    ]
    
    SAFE_PATTERNS = [
        # Basic math
        r'^\s*(import\s+math|from\s+math\s+import)',
        r'^\s*([\w\s]+=\s*[\d\s\+\-\*\/\(\)\.]+)$',
        
        # Print statements
        r'^\s*print\s*\(',
        
        # Basic functions
        r'^\s*def\s+\w+\s*\(',
        r'^\s*return\s+',
    ]
    
    def analyze(self, code: str) -> RiskLevel:
        """
        Analyze code and determine risk level.
        
        Args:
            code: Python code to analyze
            
        Returns:
            RiskLevel assessment
        """
        code_lower = code.lower()
        
        # Check critical patterns
        for pattern in self.CRITICAL_PATTERNS:
            if re.search(pattern, code_lower, re.IGNORECASE):
                logger.warning(f"CRITICAL pattern detected: {pattern}")
                return RiskLevel.CRITICAL
        
        # Check high risk patterns
        for pattern in self.HIGH_PATTERNS:
            if re.search(pattern, code_lower, re.IGNORECASE):
                logger.warning(f"HIGH risk pattern detected: {pattern}")
                return RiskLevel.HIGH
        
        # Check medium risk patterns
        medium_matches = 0
        for pattern in self.MEDIUM_PATTERNS:
            if re.search(pattern, code_lower, re.IGNORECASE):
                medium_matches += 1
                if medium_matches >= 2:  # Multiple medium patterns = high risk
                    logger.warning(f"Multiple MEDIUM risk patterns detected")
                    return RiskLevel.HIGH
        
        if medium_matches > 0:
            return RiskLevel.MEDIUM
        
        # Default to low risk
        return RiskLevel.LOW
    
    def get_violations(self, code: str) -> List[str]:
        """Get list of pattern violations found in code."""
        violations = []
        code_lower = code.lower()
        
        for pattern in self.CRITICAL_PATTERNS + self.HIGH_PATTERNS:
            if re.search(pattern, code_lower, re.IGNORECASE):
                violations.append(pattern)
        
        return violations


class ApprovalGateway:
    """
    Approval Gateway for code execution and sensitive operations.
    
    Manages approval workflow:
    1. Receives request
    2. Analyzes risk
    3. Sends Authy push
    4. Waits for approval/denial
    5. Generates signed token on approval
    """
    
    def __init__(
        self,
        private_key: str,
        approval_timeout_minutes: int = 5,
        max_pending_per_user: int = 3,
        db_path: Optional[str] = None,
    ):
        """
        Initialize approval gateway.

        Args:
            private_key: Private key for signing approval tokens
            approval_timeout_minutes: Default approval timeout
            max_pending_per_user: Maximum pending approvals per user
            db_path: Path to the SQLite database (for Authy ID lookup).
                     If None, Authy pushes are skipped with a warning.
        """
        self.token_generator = ApprovalTokenGenerator(private_key)
        self.authy_service: AuthyService = get_authy_service()
        self.risk_analyzer = CodeRiskAnalyzer()
        self._db_path = db_path

        self.approval_timeout = timedelta(minutes=approval_timeout_minutes)
        self.max_pending = max_pending_per_user

        # In-memory approval store (consider Redis for production)
        self._approvals: Dict[str, ApprovalRequest] = {}
        self._user_pending: Dict[str, List[str]] = {}
    
    async def request_code_execution_approval(
        self,
        user_id: str,
        code: str,
        context: str = ""
    ) -> ApprovalRequest:
        """
        Request approval for code execution.
        
        Args:
            user_id: User requesting execution
            code: Python code to execute
            context: Optional context description
            
        Returns:
            ApprovalRequest object
            
        Raises:
            ApprovalLimitError: If user has too many pending approvals
        """
        # Check pending limit
        if len(self._user_pending.get(user_id, [])) >= self.max_pending:
            raise ApprovalLimitError(
                f"Maximum pending approvals ({self.max_pending}) reached. "
                f"Please approve or deny existing requests first."
            )
        
        # Analyze risk
        risk_level = self.risk_analyzer.analyze(code)
        violations = self.risk_analyzer.get_violations(code)
        
        # Create approval request
        now = datetime.now(timezone.utc)
        expires = now + self.approval_timeout
        
        approval_id = str(uuid4())
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]
        
        request = ApprovalRequest(
            id=approval_id,
            user_id=user_id,
            operation_type="code_execution",
            operation_data={
                'code_preview': code[:200] + "..." if len(code) > 200 else code,
                'code_length': len(code),
                'risk_violations': violations,
                'context': context,
            },
            risk_level=risk_level,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires,
            code_hash=code_hash
        )
        
        # Store approval
        self._approvals[approval_id] = request
        self._user_pending.setdefault(user_id, []).append(approval_id)
        
        # Send Authy push notification
        try:
            # Get user's Authy ID from database
            authy_id = await self._get_user_authy_id(user_id)
            
            if authy_id:
                details = {
                    'message': f"Code execution requested (Risk: {risk_level.value.upper()})",
                    'details': {
                        'Code Length': str(len(code)),
                        'Risk Level': risk_level.value,
                        'Preview': code[:100] + "..." if len(code) > 100 else code,
                    }
                }
                
                push_response = self.authy_service.send_push_notification(
                    authy_id,
                    details
                )
                
                request.authy_request_id = push_response.get('approval_request_uuid')
                logger.info(f"Authy push sent: {request.authy_request_id}")
            else:
                logger.warning(f"No Authy ID found for user {user_id}")
                
        except Exception as e:
            logger.error(f"Failed to send Authy push: {e}")
            # Continue - approval can still be granted via other means
        
        logger.info(
            f"Approval request created: {approval_id} "
            f"User: {user_id} Risk: {risk_level.value}"
        )
        
        return request
    
    async def check_approval_status(self, approval_id: str) -> ApprovalRequest:
        """
        Check the status of an approval request.
        
        Args:
            approval_id: Approval request ID
            
        Returns:
            ApprovalRequest with current status
            
        Raises:
            ApprovalNotFoundError: If approval ID not found
        """
        if approval_id not in self._approvals:
            raise ApprovalNotFoundError(f"Approval request not found: {approval_id}")
        
        request = self._approvals[approval_id]
        
        # Check if expired
        if request.status == ApprovalStatus.PENDING:
            if datetime.now(timezone.utc) > request.expires_at:
                request.status = ApprovalStatus.EXPIRED
                self._cleanup_user_pending(request.user_id, approval_id)
                logger.info(f"Approval expired: {approval_id}")
        
        # Check Authy status if pending
        if request.status == ApprovalStatus.PENDING and request.authy_request_id:
            try:
                status = self.authy_service.check_push_status(request.authy_request_id)
                authy_status = status.get('status', '').lower()
                
                if authy_status == 'approved':
                    await self.approve(approval_id, "Authy push approved")
                elif authy_status == 'denied':
                    await self.deny(approval_id, "Authy push denied")
                    
            except Exception as e:
                logger.error(f"Failed to check Authy status: {e}")
        
        return request
    
    async def approve(
        self,
        approval_id: str,
        reason: str = ""
    ) -> ApprovalRequest:
        """
        Approve a pending request.
        
        Args:
            approval_id: Approval request ID
            reason: Optional approval reason
            
        Returns:
            Updated ApprovalRequest
        """
        request = await self.check_approval_status(approval_id)
        
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalError(f"Cannot approve request with status: {request.status}")
        
        # Generate approval token
        token = self.token_generator.generate_token(
            user_id=request.user_id,
            code=request.operation_data.get('code_preview', ''),
            risk_level=request.risk_level.value,
            expiry_minutes=5
        )
        
        # Update request
        request.status = ApprovalStatus.APPROVED
        request.approved_at = datetime.now(timezone.utc)
        request.approval_token = token
        
        # Cleanup
        self._cleanup_user_pending(request.user_id, approval_id)
        
        logger.info(f"Approval granted: {approval_id} User: {request.user_id}")
        
        return request
    
    async def deny(
        self,
        approval_id: str,
        reason: str = ""
    ) -> ApprovalRequest:
        """
        Deny a pending request.
        
        Args:
            approval_id: Approval request ID
            reason: Denial reason
            
        Returns:
            Updated ApprovalRequest
        """
        request = await self.check_approval_status(approval_id)
        
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalError(f"Cannot deny request with status: {request.status}")
        
        request.status = ApprovalStatus.DENIED
        request.denial_reason = reason
        
        # Cleanup
        self._cleanup_user_pending(request.user_id, approval_id)
        
        logger.info(f"Approval denied: {approval_id} User: {request.user_id} Reason: {reason}")
        
        return request
    
    def get_user_pending(self, user_id: str) -> List[ApprovalRequest]:
        """Get all pending approvals for a user."""
        approval_ids = self._user_pending.get(user_id, [])
        return [
            self._approvals[aid] for aid in approval_ids
            if aid in self._approvals
        ]
    
    def _cleanup_user_pending(self, user_id: str, approval_id: str):
        """Remove approval ID from user's pending list."""
        if user_id in self._user_pending:
            if approval_id in self._user_pending[user_id]:
                self._user_pending[user_id].remove(approval_id)
    
    async def _get_user_authy_id(self, user_id: str) -> Optional[str]:
        """
        Get user's Authy ID from the user_mfa table.

        Returns None (and logs a warning) if the DB path is unconfigured,
        the user has no Authy ID, or the lookup fails.
        """
        if not self._db_path:
            logger.warning(
                "db_path not configured in ApprovalGateway — "
                "cannot look up Authy ID for user %s", user_id
            )
            return None

        try:
            # Use a short-lived connection; no long-lived state needed.
            conn = sqlite3.connect(self._db_path, timeout=5)
            try:
                cursor = conn.execute(
                    "SELECT authy_id FROM user_mfa WHERE user_id = ? LIMIT 1",
                    (user_id,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()

            if not row or not row[0]:
                logger.info("No Authy ID registered for user %s", user_id)
                return None

            return str(row[0])

        except sqlite3.Error as exc:
            logger.error(
                "Database error looking up Authy ID for user %s: %s",
                user_id, exc
            )
            return None
    
    def cleanup_expired(self):
        """Clean up expired approvals."""
        now = datetime.now(timezone.utc)
        expired = [
            aid for aid, req in self._approvals.items()
            if req.status == ApprovalStatus.PENDING and now > req.expires_at
        ]
        
        for aid in expired:
            self._approvals[aid].status = ApprovalStatus.EXPIRED
            self._cleanup_user_pending(self._approvals[aid].user_id, aid)
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired approvals")


# Custom exceptions
class ApprovalError(Exception):
    """Base approval error."""
    pass


class ApprovalLimitError(ApprovalError):
    """Too many pending approvals."""
    pass


class ApprovalNotFoundError(ApprovalError):
    """Approval request not found."""
    pass


# Global gateway instance
_approval_gateway: Optional[ApprovalGateway] = None


def get_approval_gateway(
    private_key: str = "",
    db_path: Optional[str] = None,
) -> ApprovalGateway:
    """Get singleton approval gateway instance."""
    global _approval_gateway
    if _approval_gateway is None:
        if not private_key:
            raise ValueError("Private key required for approval gateway initialization")
        _approval_gateway = ApprovalGateway(private_key, db_path=db_path)
    return _approval_gateway
