"""
Component registry for the 4S1T Agent AI framework.

Provides a central registry for discovering and managing system components.
"""
import logging
from typing import Dict, Any, Type, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class ComponentMetadata:
    """Metadata for a registered component."""
    name: str
    type: str
    version: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    config: Dict[str, Any] = field(default_factory=dict)


class ComponentRegistry:
    """Central registry for system components."""
    
    _instance: Optional['ComponentRegistry'] = None
    
    def __init__(self):
        """Initialize the component registry."""
        if ComponentRegistry._instance is not None:
            raise RuntimeError("Use ComponentRegistry.get_instance() to get the singleton instance")
            
        self._components: Dict[str, Any] = {}
        self._metadata: Dict[str, ComponentMetadata] = {}
        self._type_index: Dict[str, List[str]] = {}
        self._tag_index: Dict[str, List[str]] = {}
        
        ComponentRegistry._instance = self
    
    @classmethod
    def get_instance(cls) -> 'ComponentRegistry':
        """
        Get the singleton instance of the component registry.
        
        Returns:
            ComponentRegistry instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def register_component(
        self, 
        name: str, 
        component: Any,
        metadata: ComponentMetadata
    ) -> None:
        """
        Register a component with the registry.
        
        Args:
            name: Unique name for the component
            component: The component instance
            metadata: Metadata about the component
        """
        if name in self._components:
            logger.warning(f"Component {name} already registered, overwriting")
        
        # Register the component
        self._components[name] = component
        self._metadata[name] = metadata
        
        # Update indices
        if metadata.type not in self._type_index:
            self._type_index[metadata.type] = []
        self._type_index[metadata.type].append(name)
        
        for tag in metadata.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            self._tag_index[tag].append(name)
        
        logger.info(f"Registered component: {name} ({metadata.type})")
    
    def get_component(self, name: str) -> Optional[Any]:
        """
        Get a registered component by name.
        
        Args:
            name: Name of the component
            
        Returns:
            Component instance or None if not found
        """
        return self._components.get(name)
    
    def get_components_by_type(self, component_type: str) -> List[Any]:
        """
        Get all components of a specific type.
        
        Args:
            component_type: Type of components to retrieve
            
        Returns:
            List of component instances
        """
        names = self._type_index.get(component_type, [])
        return [self._components[name] for name in names]
    
    def get_components_by_tag(self, tag: str) -> List[Any]:
        """
        Get all components with a specific tag.
        
        Args:
            tag: Tag to filter by
            
        Returns:
            List of component instances
        """
        names = self._tag_index.get(tag, [])
        return [self._components[name] for name in names]
    
    def get_metadata(self, name: str) -> Optional[ComponentMetadata]:
        """
        Get metadata for a component.
        
        Args:
            name: Name of the component
            
        Returns:
            Component metadata or None if not found
        """
        return self._metadata.get(name)
    
    def list_components(self) -> List[str]:
        """
        List all registered component names.
        
        Returns:
            List of component names
        """
        return list(self._components.keys())
    
    def unregister_component(self, name: str) -> bool:
        """
        Unregister a component.
        
        Args:
            name: Name of the component to unregister
            
        Returns:
            True if component was unregistered, False if not found
        """
        if name not in self._components:
            return False
            
        # Remove from main storage
        component = self._components.pop(name)
        metadata = self._metadata.pop(name)
        
        # Update indices
        if metadata.type in self._type_index:
            if name in self._type_index[metadata.type]:
                self._type_index[metadata.type].remove(name)
                if not self._type_index[metadata.type]:
                    del self._type_index[metadata.type]
        
        for tag in metadata.tags:
            if tag in self._tag_index and name in self._tag_index[tag]:
                self._tag_index[tag].remove(name)
                if not self._tag_index[tag]:
                    del self._tag_index[tag]
        
        logger.info(f"Unregistered component: {name}")
        return True


# Convenience functions
def get_registry() -> ComponentRegistry:
    """Get the global component registry instance."""
    return ComponentRegistry.get_instance()


def register_component(name: str, component: Any, metadata: ComponentMetadata) -> None:
    """Register a component with the global registry."""
    registry = get_registry()
    registry.register_component(name, component, metadata)


def get_component(name: str) -> Optional[Any]:
    """Get a component from the global registry."""
    registry = get_registry()
    return registry.get_component(name)
