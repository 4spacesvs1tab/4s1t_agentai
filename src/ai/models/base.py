"""
Base classes and interfaces for AI model management in the 4S1T Agent AI framework.

This module defines the abstract base classes and core interfaces for managing
different types of AI models within the framework.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ModelStatus(Enum):
    """Enumeration of possible model statuses."""
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"
    UNLOADING = "unloading"


class ModelType(Enum):
    """Enumeration of supported model types."""
    LANGUAGE_MODEL = "language_model"
    EMBEDDING_MODEL = "embedding_model"
    IMAGE_MODEL = "image_model"
    AUDIO_MODEL = "audio_model"
    CUSTOM = "custom"


@dataclass
class ModelMetadata:
    """Metadata for an AI model."""
    name: str
    version: str
    model_type: ModelType
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    last_loaded: Optional[datetime] = None
    load_count: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class ModelResponse:
    """Standard response format from AI models."""
    content: Union[str, Dict[str, Any], List[Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)
    model_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    latency_ms: float = 0.0


class BaseModel(ABC):
    """
    Abstract base class for all AI models in the 4S1T Agent AI framework.
    
    This class defines the common interface that all AI models must implement,
    regardless of their specific type or implementation.
    """
    
    def __init__(self, metadata: ModelMetadata):
        """
        Initialize the base model.
        
        Args:
            metadata: Metadata describing the model
        """
        self.metadata = metadata
        self.status = ModelStatus.UNLOADED
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    @abstractmethod
    async def load(self) -> bool:
        """
        Load the model into memory.
        
        Returns:
            bool: True if loading was successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def unload(self) -> bool:
        """
        Unload the model from memory.
        
        Returns:
            bool: True if unloading was successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def generate(self, prompt: Union[str, Dict[str, Any]], **kwargs) -> ModelResponse:
        """
        Generate a response from the model.
        
        Args:
            prompt: The input prompt or structured input
            **kwargs: Additional arguments for generation
            
        Returns:
            ModelResponse: The model's response
        """
        pass
    
    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """
        Get information about the model.
        
        Returns:
            Dict[str, Any]: Dictionary containing model information
        """
        pass
    
    def is_loaded(self) -> bool:
        """
        Check if the model is currently loaded.
        
        Returns:
            bool: True if model is loaded, False otherwise
        """
        return self.status == ModelStatus.LOADED
    
    def update_status(self, status: ModelStatus) -> None:
        """
        Update the model's status.
        
        Args:
            status: New status for the model
        """
        self.status = status
        if status == ModelStatus.LOADED:
            self.metadata.last_loaded = datetime.now()
            self.metadata.load_count += 1


class ModelManager:
    """
    Manager for AI models in the 4S1T Agent AI framework.
    
    This class handles the lifecycle of AI models, including loading, unloading,
    versioning, and switching between different models.
    """
    
    def __init__(self):
        """Initialize the model manager."""
        self.models: Dict[str, BaseModel] = {}
        self.active_models: Dict[ModelType, str] = {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def register_model(self, model: BaseModel) -> bool:
        """
        Register a model with the manager.
        
        Args:
            model: The model to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            model_name = model.metadata.name
            if model_name in self.models:
                self.logger.warning(f"Model {model_name} already registered, overwriting")
            
            self.models[model_name] = model
            self.logger.info(f"Registered model: {model_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register model: {e}")
            return False
    
    def unregister_model(self, model_name: str) -> bool:
        """
        Unregister a model from the manager.
        
        Args:
            model_name: Name of the model to unregister
            
        Returns:
            bool: True if unregistration was successful, False otherwise
        """
        try:
            if model_name not in self.models:
                self.logger.warning(f"Model {model_name} not found for unregistration")
                return False
            
            # Remove from active models if it's active
            for model_type, active_name in list(self.active_models.items()):
                if active_name == model_name:
                    del self.active_models[model_type]
            
            del self.models[model_name]
            self.logger.info(f"Unregistered model: {model_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unregister model {model_name}: {e}")
            return False
    
    async def load_model(self, model_name: str) -> bool:
        """
        Load a registered model.
        
        Args:
            model_name: Name of the model to load
            
        Returns:
            bool: True if loading was successful, False otherwise
        """
        try:
            if model_name not in self.models:
                self.logger.error(f"Model {model_name} not registered")
                return False
            
            model = self.models[model_name]
            if model.is_loaded():
                self.logger.info(f"Model {model_name} already loaded")
                return True
            
            model.update_status(ModelStatus.LOADING)
            success = await model.load()
            
            if success:
                model.update_status(ModelStatus.LOADED)
                self.logger.info(f"Successfully loaded model: {model_name}")
            else:
                model.update_status(ModelStatus.ERROR)
                self.logger.error(f"Failed to load model: {model_name}")
            
            return success
        except Exception as e:
            self.logger.error(f"Exception while loading model {model_name}: {e}")
            if model_name in self.models:
                self.models[model_name].update_status(ModelStatus.ERROR)
            return False
    
    async def unload_model(self, model_name: str) -> bool:
        """
        Unload a loaded model.
        
        Args:
            model_name: Name of the model to unload
            
        Returns:
            bool: True if unloading was successful, False otherwise
        """
        try:
            if model_name not in self.models:
                self.logger.error(f"Model {model_name} not registered")
                return False
            
            model = self.models[model_name]
            if not model.is_loaded():
                self.logger.info(f"Model {model_name} not already loaded")
                return True
            
            model.update_status(ModelStatus.UNLOADING)
            success = await model.unload()
            
            if success:
                model.update_status(ModelStatus.UNLOADED)
                self.logger.info(f"Successfully unloaded model: {model_name}")
                
                # Remove from active models if it was active
                for model_type, active_name in list(self.active_models.items()):
                    if active_name == model_name:
                        del self.active_models[model_type]
            else:
                model.update_status(ModelStatus.ERROR)
                self.logger.error(f"Failed to unload model: {model_name}")
            
            return success
        except Exception as e:
            self.logger.error(f"Exception while unloading model {model_name}: {e}")
            if model_name in self.models:
                self.models[model_name].update_status(ModelStatus.ERROR)
            return False
    
    def set_active_model(self, model_type: ModelType, model_name: str) -> bool:
        """
        Set a model as active for a specific model type.
        
        Args:
            model_type: Type of model
            model_name: Name of the model to set as active
            
        Returns:
            bool: True if setting active was successful, False otherwise
        """
        try:
            if model_name not in self.models:
                self.logger.error(f"Model {model_name} not registered")
                return False
            
            self.active_models[model_type] = model_name
            self.logger.info(f"Set active model for {model_type.value}: {model_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to set active model {model_name}: {e}")
            return False
    
    def get_active_model(self, model_type: ModelType) -> Optional[BaseModel]:
        """
        Get the active model for a specific model type.
        
        Args:
            model_type: Type of model
            
        Returns:
            BaseModel: The active model, or None if none is active
        """
        try:
            if model_type not in self.active_models:
                self.logger.debug(f"No active model for {model_type.value}")
                return None
            
            model_name = self.active_models[model_type]
            if model_name not in self.models:
                self.logger.error(f"Active model {model_name} not found in registry")
                return None
            
            return self.models[model_name]
        except Exception as e:
            self.logger.error(f"Error getting active model for {model_type.value}: {e}")
            return None
    
    def get_model(self, model_name: str) -> Optional[BaseModel]:
        """
        Get a registered model by name.
        
        Args:
            model_name: Name of the model
            
        Returns:
            BaseModel: The model, or None if not found
        """
        return self.models.get(model_name)
    
    def list_models(self) -> List[Dict[str, Any]]:
        """
        List all registered models with their status.
        
        Returns:
            List[Dict[str, Any]]: List of model information dictionaries
        """
        model_list = []
        for name, model in self.models.items():
            model_info = {
                "name": name,
                "type": model.metadata.model_type.value,
                "version": model.metadata.version,
                "status": model.status.value,
                "description": model.metadata.description,
                "tags": model.metadata.tags
            }
            model_list.append(model_info)
        return model_list
