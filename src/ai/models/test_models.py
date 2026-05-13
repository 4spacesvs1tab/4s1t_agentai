"""
Tests for the AI model management system.
"""

import asyncio
import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
from datetime import datetime

from ai.models.base import ModelManager, ModelMetadata, ModelType
from ai.models.language_model import MockLanguageModel


@pytest.mark.asyncio
async def test_model_registration():
    """Test model registration and unregistration."""
    manager = ModelManager()
    
    # Create a mock model
    metadata = ModelMetadata(
        name="test-model",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Test model for testing"
    )
    model = MockLanguageModel(metadata)
    
    # Register the model
    assert manager.register_model(model) == True
    
    # Check that model is registered
    assert "test-model" in manager.models
    assert manager.get_model("test-model") == model
    
    # List models
    model_list = manager.list_models()
    assert len(model_list) == 1
    assert model_list[0]["name"] == "test-model"
    
    # Unregister the model
    assert manager.unregister_model("test-model") == True
    assert "test-model" not in manager.models


@pytest.mark.asyncio
async def test_model_loading():
    """Test model loading and unloading."""
    manager = ModelManager()
    
    # Create a mock model
    metadata = ModelMetadata(
        name="test-model-2",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL
    )
    model = MockLanguageModel(metadata)
    
    # Register the model
    assert manager.register_model(model) == True
    
    # Load the model
    assert await manager.load_model("test-model-2") == True
    assert model.is_loaded() == True
    
    # Unload the model
    assert await manager.unload_model("test-model-2") == True
    assert model.is_loaded() == False


@pytest.mark.asyncio
async def test_active_model_management():
    """Test active model management."""
    manager = ModelManager()
    
    # Create a mock model
    metadata = ModelMetadata(
        name="active-test-model",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL
    )
    model = MockLanguageModel(metadata)
    
    # Register and load the model
    assert manager.register_model(model) == True
    assert await manager.load_model("active-test-model") == True
    
    # Set as active model
    assert manager.set_active_model(ModelType.LANGUAGE_MODEL, "active-test-model") == True
    
    # Get active model
    active_model = manager.get_active_model(ModelType.LANGUAGE_MODEL)
    assert active_model == model
    
    # Unregister model (should remove from active)
    assert manager.unregister_model("active-test-model") == True
    assert manager.get_active_model(ModelType.LANGUAGE_MODEL) is None


@pytest.mark.asyncio
async def test_model_generation():
    """Test model response generation."""
    manager = ModelManager()
    
    # Create a mock model
    metadata = ModelMetadata(
        name="generation-test-model",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL
    )
    model = MockLanguageModel(metadata)
    
    # Register and load the model
    assert manager.register_model(model) == True
    assert await manager.load_model("generation-test-model") == True
    
    # Generate a response
    response = await model.generate("Hello, how are you?", temperature=0.7)
    
    # Check response structure
    assert response.content is not None
    assert response.model_name == "generation-test-model"
    assert response.latency_ms >= 0
    assert "metadata" in response.__dict__
    
    # Check that content is a string
    assert isinstance(response.content, str)
    assert len(response.content) > 0


if __name__ == "__main__":
    # Run tests manually if needed
    async def run_tests():
        await test_model_registration()
        await test_model_loading()
        await test_active_model_management()
        await test_model_generation()
        print("All tests passed!")
    
    asyncio.run(run_tests())
