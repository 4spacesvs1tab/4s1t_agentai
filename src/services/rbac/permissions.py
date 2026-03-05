"""
Role-Based Access Control (RBAC) permissions system for the 4S1T Agent AI framework.

Defines roles, permissions, and access control logic.
"""
from enum import Enum
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
import logging

from database.connection import get_database_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)


class Permission(Enum):
    """Available permissions in the system."""
    # User management
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    USER_DELETE = "user:delete"
    
    # System management
    SYSTEM_READ = "system:read"
    SYSTEM_WRITE = "system:write"
    SYSTEM_CONFIG = "system:config"
    
    # Component management
    COMPONENT_READ = "component:read"
    COMPONENT_WRITE = "component:write"
    COMPONENT_DELETE = "component:delete"
    
    # Event management
    EVENT_READ = "event:read"
    EVENT_WRITE = "event:write"
    
    # Health monitoring
    HEALTH_READ = "health:read"
    HEALTH_WRITE = "health:write"
    
    # Configuration management
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    
    # Data access
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"
    
    # API access
    API_ACCESS = "api:access"
    
    # Administration
    ADMIN_ALL = "admin:*"


class Role(Enum):
    """Predefined roles in the system."""
    USER = "user"
    POWER_USER = "power_user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


# Role to permissions mapping
ROLE_PERMISSIONS = {
    Role.USER: {
        Permission.USER_READ,
        Permission.SYSTEM_READ,
        Permission.COMPONENT_READ,
        Permission.EVENT_READ,
        Permission.HEALTH_READ,
        Permission.DATA_READ,
        Permission.API_ACCESS
    },
    Role.POWER_USER: {
        Permission.USER_READ,
        Permission.USER_WRITE,
        Permission.SYSTEM_READ,
        Permission.SYSTEM_WRITE,
        Permission.COMPONENT_READ,
        Permission.COMPONENT_WRITE,
        Permission.EVENT_READ,
        Permission.EVENT_WRITE,
        Permission.HEALTH_READ,
        Permission.HEALTH_WRITE,
        Permission.DATA_READ,
        Permission.DATA_WRITE,
        Permission.API_ACCESS
    },
    Role.ADMIN: {
        Permission.USER_READ,
        Permission.USER_WRITE,
        Permission.USER_DELETE,
        Permission.SYSTEM_READ,
        Permission.SYSTEM_WRITE,
        Permission.SYSTEM_CONFIG,
        Permission.COMPONENT_READ,
        Permission.COMPONENT_WRITE,
        Permission.COMPONENT_DELETE,
        Permission.EVENT_READ,
        Permission.EVENT_WRITE,
        Permission.HEALTH_READ,
        Permission.HEALTH_WRITE,
        Permission.CONFIG_READ,
        Permission.CONFIG_WRITE,
        Permission.DATA_READ,
        Permission.DATA_WRITE,
        Permission.DATA_DELETE,
        Permission.API_ACCESS
    },
    Role.SUPER_ADMIN: {
        Permission.ADMIN_ALL
    }
}


@dataclass
class CustomRole:
    """Custom role definition."""
    name: str
    permissions: Set[Permission] = field(default_factory=set)
    description: str = ""
    created_by: Optional[str] = None


class RBACService:
    """Service for handling role-based access control."""
    
    def __init__(self):
        """Initialize RBAC service."""
        self.db = get_database_connection()
        self._initialize_database()
        self._custom_roles: Dict[str, CustomRole] = {}
        logger.info("RBAC service initialized")
    
    def _initialize_database(self):
        """Initialize RBAC-related database tables."""
        try:
            # Create roles table
            create_roles_table = """
                CREATE TABLE IF NOT EXISTS rbac_roles (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    permissions TEXT,
                    created_by TEXT,
                    created_at TEXT NOT NULL
                )
            """
            self.db.execute_command(create_roles_table)
            
            # Create user roles table
            create_user_roles_table = """
                CREATE TABLE IF NOT EXISTS user_roles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    assigned_by TEXT,
                    assigned_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            self.db.execute_command(create_user_roles_table)
            
            logger.info("RBAC database tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize RBAC database tables: {e}")
            raise
    
    def get_role_permissions(self, role: Role) -> Set[Permission]:
        """
        Get permissions for a predefined role.
        
        Args:
            role: Role enum value
            
        Returns:
            Set of permissions for the role
        """
        return ROLE_PERMISSIONS.get(role, set())
    
    def create_custom_role(self, name: str, permissions: List[Permission], description: str = "", created_by: str = None) -> bool:
        """
        Create a custom role.
        
        Args:
            name: Role name
            permissions: List of permissions for the role
            description: Role description
            created_by: User who created the role
            
        Returns:
            True if role created successfully, False otherwise
        """
        try:
            # Check if role already exists
            query = "SELECT id FROM rbac_roles WHERE name = ?"
            existing = self.db.execute_query(query, (name,))
            
            if existing:
                logger.warning(f"Role {name} already exists")
                return False
            
            # Create custom role object
            permission_set = set(permissions)
            custom_role = CustomRole(
                name=name,
                permissions=permission_set,
                description=description,
                created_by=created_by
            )
            
            # Store in memory
            self._custom_roles[name] = custom_role
            
            # Store in database
            role_id = self._generate_id()
            permissions_str = ",".join([p.value for p in permission_set])
            insert_query = """
                INSERT INTO rbac_roles 
                (id, name, description, permissions, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """
            self.db.execute_command(
                insert_query, 
                (role_id, name, description, permissions_str, created_by)
            )
            
            logger.info(f"Custom role {name} created successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to create custom role {name}: {e}")
            return False
    
    def get_custom_role(self, name: str) -> Optional[CustomRole]:
        """
        Get a custom role by name.
        
        Args:
            name: Role name
            
        Returns:
            CustomRole object or None if not found
        """
        # Check memory cache first
        if name in self._custom_roles:
            return self._custom_roles[name]
        
        # Load from database
        try:
            query = "SELECT * FROM rbac_roles WHERE name = ?"
            result = self.db.execute_query(query, (name,))
            
            if not result:
                return None
            
            row = result[0]
            permissions = set()
            if row["permissions"]:
                for perm_str in row["permissions"].split(","):
                    try:
                        permissions.add(Permission(perm_str))
                    except ValueError:
                        logger.warning(f"Unknown permission: {perm_str}")
            
            custom_role = CustomRole(
                name=row["name"],
                permissions=permissions,
                description=row["description"],
                created_by=row["created_by"]
            )
            
            # Cache in memory
            self._custom_roles[name] = custom_role
            
            return custom_role
        except Exception as e:
            logger.error(f"Failed to get custom role {name}: {e}")
            return None
    
    def assign_role_to_user(self, user_id: str, role_name: str, assigned_by: str = None) -> bool:
        """
        Assign a role to a user.
        
        Args:
            user_id: User ID
            role_name: Role name
            assigned_by: User who assigned the role
            
        Returns:
            True if role assigned successfully, False otherwise
        """
        try:
            # Check if user-role assignment already exists
            query = "SELECT id FROM user_roles WHERE user_id = ? AND role_name = ?"
            existing = self.db.execute_query(query, (user_id, role_name))
            
            if existing:
                logger.warning(f"Role {role_name} already assigned to user {user_id}")
                return False
            
            # Create assignment
            assignment_id = self._generate_id()
            insert_query = """
                INSERT INTO user_roles 
                (id, user_id, role_name, assigned_by, assigned_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """
            self.db.execute_command(
                insert_query, 
                (assignment_id, user_id, role_name, assigned_by)
            )
            
            logger.info(f"Role {role_name} assigned to user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to assign role {role_name} to user {user_id}: {e}")
            return False
    
    def remove_role_from_user(self, user_id: str, role_name: str) -> bool:
        """
        Remove a role from a user.
        
        Args:
            user_id: User ID
            role_name: Role name
            
        Returns:
            True if role removed successfully, False otherwise
        """
        try:
            delete_query = "DELETE FROM user_roles WHERE user_id = ? AND role_name = ?"
            self.db.execute_command(delete_query, (user_id, role_name))
            
            logger.info(f"Role {role_name} removed from user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove role {role_name} from user {user_id}: {e}")
            return False
    
    def get_user_roles(self, user_id: str) -> List[str]:
        """
        Get all roles assigned to a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of role names
        """
        try:
            query = "SELECT role_name FROM user_roles WHERE user_id = ?"
            results = self.db.execute_query(query, (user_id,))
            return [row["role_name"] for row in results]
        except Exception as e:
            logger.error(f"Failed to get roles for user {user_id}: {e}")
            return []
    
    def get_user_permissions(self, user_id: str) -> Set[Permission]:
        """
        Get all permissions for a user based on their roles.
        
        Args:
            user_id: User ID
            
        Returns:
            Set of permissions
        """
        try:
            # Get user's roles
            user_roles = self.get_user_roles(user_id)
            
            # Collect permissions from all roles
            permissions = set()
            
            for role_name in user_roles:
                # Check if it's a predefined role
                try:
                    role_enum = Role(role_name)
                    role_perms = self.get_role_permissions(role_enum)
                    permissions.update(role_perms)
                except ValueError:
                    # It's a custom role
                    custom_role = self.get_custom_role(role_name)
                    if custom_role:
                        permissions.update(custom_role.permissions)
            
            # Handle admin wildcard permission
            if Permission.ADMIN_ALL in permissions:
                # Grant all permissions
                permissions = set(Permission)
            
            return permissions
        except Exception as e:
            logger.error(f"Failed to get permissions for user {user_id}: {e}")
            return set()
    
    def check_permission(self, user_id: str, permission: Permission) -> bool:
        """
        Check if a user has a specific permission.
        
        Args:
            user_id: User ID
            permission: Permission to check
            
        Returns:
            True if user has permission, False otherwise
        """
        try:
            # Get user's permissions
            user_permissions = self.get_user_permissions(user_id)
            
            # Handle admin wildcard permission
            if Permission.ADMIN_ALL in user_permissions:
                return True
            
            # Check if user has the specific permission
            return permission in user_permissions
        except Exception as e:
            logger.error(f"Failed to check permission {permission.value} for user {user_id}: {e}")
            return False
    
    def check_multiple_permissions(self, user_id: str, permissions: List[Permission], require_all: bool = True) -> bool:
        """
        Check if a user has multiple permissions.
        
        Args:
            user_id: User ID
            permissions: List of permissions to check
            require_all: If True, user must have all permissions. If False, user must have at least one.
            
        Returns:
            True if permission check passes, False otherwise
        """
        try:
            # Get user's permissions
            user_permissions = self.get_user_permissions(user_id)
            
            # Handle admin wildcard permission
            if Permission.ADMIN_ALL in user_permissions:
                return True
            
            if require_all:
                # User must have all permissions
                return all(perm in user_permissions for perm in permissions)
            else:
                # User must have at least one permission
                return any(perm in user_permissions for perm in permissions)
        except Exception as e:
            logger.error(f"Failed to check multiple permissions for user {user_id}: {e}")
            return False
    
    def _generate_id(self) -> str:
        """
        Generate a unique ID.
        
        Returns:
            Unique ID string
        """
        import secrets
        return secrets.token_hex(16)


# Global RBAC service instance
rbac_service: Optional[RBACService] = None


def get_rbac_service() -> RBACService:
    """
    Get singleton RBAC service instance.
    
    Returns:
        RBACService instance
    """
    global rbac_service
    if rbac_service is None:
        rbac_service = RBACService()
    return rbac_service
