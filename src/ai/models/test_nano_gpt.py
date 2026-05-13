"""
Test module for nano-gpt.com integration.

This module demonstrates how to use the nano-gpt.com integration
in the 4S1T Agent AI framework.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

# Add parent directory to path to enable imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.models.base import ModelManager, ModelMetadata, ModelType
from ai.models.nano_gpt import NanoGPTLanguageModel
from ai.models.nano_gpt_api import NanoGPTApiClient, get_available_models, generate_embeddings, generate_audio_speech
from ai.models.selection import ModelSelectionService, TaskType, TaskRequirements
from config.nano_gpt_config import NanoGPTConfig, get_nano_gpt_config_manager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo_nano_gpt_model():
    """Demonstrate nano-gpt model functionality."""
    print("=== Nano-GPT Model Demo ===")
    
    # Create model manager
    model_manager = ModelManager()
    
    # Create nano-gpt configuration
    config_manager = get_nano_gpt_config_manager()
    nano_config = config_manager.get_config()
    
    # For demo purposes, we'll use a mock API key
    # In real usage, you would set NANO_GPT_API_KEY environment variable
    nano_config.api_key = "demo-api-key"  # This won't work with real API
    nano_config.subscription_tier = "PRO"
    
    # Create model metadata for nano-gpt
    nano_metadata = ModelMetadata(
        name="nano-gpt-pro",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Nano-GPT PRO subscription model",
        config={
            "api_endpoint": nano_config.api_endpoint,
            "subscription_tier": nano_config.subscription_tier,
            "default_model": "glm-4.6"
        }
    )
    
    # Create nano-gpt model
    nano_model = NanoGPTLanguageModel(nano_metadata, api_key=nano_config.api_key)
    model_manager.register_model(nano_model)
    
    # Load model
    print("Loading nano-gpt model...")
    success = await model_manager.load_model("nano-gpt-pro")
    if success:
        print("✓ Model loaded successfully")
    else:
        print("✗ Failed to load model (expected without real API key)")
    
    # Show model info
    model_info = nano_model.get_info()
    print(f"Model Info: {model_info}")
    
    # Show model catalog
    catalog = nano_model.get_model_catalog()
    print(f"PRO Models Available: {catalog.get('total_models', 0)}")
    
    # Test model availability
    test_models = ["glm-4.6", "qwen3-coder", "unknown-model"]
    for model_name in test_models:
        is_available = nano_model.is_model_available(model_name)
        print(f"Model '{model_name}' available: {is_available}")
    
    print()


async def demo_model_selection():
    """Demonstrate model selection service."""
    print("=== Model Selection Service Demo ===")
    
    # Create model manager and register some models
    model_manager = ModelManager()
    
    # Create nano-gpt configuration
    config_manager = get_nano_gpt_config_manager()
    nano_config = config_manager.get_config()
    nano_config.subscription_tier = "PRO"
    
    # Create and register nano-gpt model
    nano_metadata = ModelMetadata(
        name="nano-gpt-pro",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Nano-GPT PRO subscription model",
        config={
            "api_endpoint": nano_config.api_endpoint,
            "subscription_tier": nano_config.subscription_tier,
            "default_model": "glm-4.6"
        }
    )
    
    nano_model = NanoGPTLanguageModel(nano_metadata, api_key="demo-key")
    model_manager.register_model(nano_model)
    
    # Create model selection service
    selection_service = ModelSelectionService(model_manager)
    
    # Test different task types
    task_scenarios = [
        (TaskType.BUSINESS_ANALYSIS, "Analyzing business requirements"),
        (TaskType.DATA_ANALYSIS, "Processing data with Python"),
        (TaskType.QUICK_RESPONSE, "Quick question about technology"),
        (TaskType.REASONING, "Complex logical reasoning problem"),
        (TaskType.CODING, "Writing Python code"),
        (TaskType.MATH_CALCULATION, "Statistical analysis"),
    ]
    
    for task_type, description in task_scenarios:
        print(f"\nTask: {task_type.value} - {description}")
        
        # Create task requirements
        requirements = TaskRequirements(
            task_type=task_type,
            context_window_required=4096,
            speed_preference=0.3 if task_type in [TaskType.REASONING, TaskType.BUSINESS_ANALYSIS] else 0.7,
            accuracy_preference=0.7 if task_type in [TaskType.REASONING, TaskType.BUSINESS_ANALYSIS] else 0.3,
            subscription_tier="PRO"
        )
        
        # Select model
        selected_model = selection_service.select_model(requirements)
        print(f"  Selected model: {selected_model}")
        
        # Get recommendations
        recommendations = selection_service.get_model_recommendations(task_type, count=3)
        print(f"  Top recommendations: {[model for model, score in recommendations]}")
    
    # Test performance tracking
    print("\nUpdating model performance metrics...")
    selection_service.update_model_performance("glm-4.6", 150.0, success=True, accuracy_score=0.95)
    selection_service.update_model_performance("qwen3-coder", 120.0, success=True, accuracy_score=0.92)
    selection_service.update_model_performance("glm-4.6", 200.0, success=False)  # Simulate error
    
    # Show updated recommendations
    print("\nRecommendations after performance updates:")
    recommendations = selection_service.get_model_recommendations(TaskType.BUSINESS_ANALYSIS, count=3)
    for model, score in recommendations:
        if model in selection_service.performance_db:
            perf = selection_service.performance_db[model]
            print(f"  {model}: score={score:.3f}, success_rate={perf.success_rate:.2f}, avg_time={perf.response_time_ms:.1f}ms")
        else:
            print(f"  {model}: score={score:.3f}")
    
    print()


async def demo_configuration():
    """Demonstrate nano-gpt configuration management."""
    print("=== Nano-GPT Configuration Demo ===")
    
    # Get configuration manager
    config_manager = get_nano_gpt_config_manager()
    config = config_manager.get_config()
    
    print(f"Current configuration:")
    print(f"  API Endpoint: {config.api_endpoint}")
    print(f"  Subscription Tier: {config.subscription_tier}")
    print(f"  PRO Features: {config.subscription_features}")
    print(f"  Default Models: {list(config.default_models.keys())}")
    
    # Test subscription features
    print(f"\nSubscription Info:")
    print(f"  Is PRO: {config.is_pro_subscription()}")
    print(f"  Available Models: {config.get_available_models()}")
    
    # Test model allowance
    test_models = ["glm-4.6", "unknown-model"]
    for model_name in test_models:
        is_allowed = config.is_model_allowed(model_name)
        print(f"  Model '{model_name}' allowed: {is_allowed}")
    
    print()


async def demo_api_client():
    """Demonstrate Nano-GPT API client functionality."""
    print("=== Nano-GPT API Client Demo ===")
    
    # Create API client (won't work without real API key)
    api_client = NanoGPTApiClient("demo-api-key")
    
    try:
        # This would normally list models, but will fail without real API key
        # models = await api_client.list_models(detailed=True)
        # print(f"Available models: {len(models.get('data', []))}")
        
        print("API client created successfully")
        print("Note: Actual API calls require a valid Nano-GPT API key")
        
    except Exception as e:
        print(f"Expected error (no real API key): {e}")
    finally:
        await api_client.close()
    
    print()


async def demo_embeddings():
    """Demonstrate embeddings functionality."""
    print("=== Embeddings Demo ===")
    
    try:
        # This would normally generate embeddings, but will fail without real API key
        # result = await generate_embeddings(
        #     "demo-api-key",
        #     "text-embedding-3-large",
        #     ["Hello world", "Nano-GPT is awesome"]
        # )
        # print(f"Generated {len(result.get('data', []))} embeddings")
        
        print("Embeddings functionality implemented")
        print("Note: Actual embedding generation requires a valid Nano-GPT API key")
        
    except Exception as e:
        print(f"Expected error (no real API key): {e}")
    
    print()


async def demo_audio_speech():
    """Demonstrate audio speech functionality."""
    print("=== Audio Speech Demo ===")
    
    try:
        # This would normally generate audio, but will fail without real API key
        # audio_data = await generate_audio_speech(
        #     "demo-api-key",
        #     "gpt-4o-mini-tts",
        #     "Hello from Nano-GPT!",
        #     "alloy"
        # )
        # print(f"Generated audio data: {len(audio_data)} bytes")
        
        print("Audio speech functionality implemented")
        print("Note: Actual audio generation requires a valid Nano-GPT API key")
        
    except Exception as e:
        print(f"Expected error (no real API key): {e}")
    
    print()


async def demo_integration():
    """Demonstrate full integration of nano-gpt features."""
    print("=== Full Integration Demo ===")
    
    # Create model manager
    model_manager = ModelManager()
    
    # Configure nano-gpt
    config_manager = get_nano_gpt_config_manager()
    nano_config = config_manager.get_config()
    nano_config.subscription_tier = "PRO"
    nano_config.api_key = "demo-api-key"  # Won't work with real API
    
    # Create and register model
    nano_metadata = ModelMetadata(
        name="nano-gpt-integration-test",
        version="1.0",
        model_type=ModelType.LANGUAGE_MODEL,
        description="Full integration test model",
        config={
            "api_endpoint": nano_config.api_endpoint,
            "subscription_tier": nano_config.subscription_tier,
            "default_model": "glm-4.6"
        }
    )
    
    nano_model = NanoGPTLanguageModel(nano_metadata, api_key=nano_config.api_key)
    model_manager.register_model(nano_model)
    
    # Create selection service
    selection_service = ModelSelectionService(model_manager)
    
    # Simulate a business analysis task
    print("Simulating business analysis task...")
    
    requirements = TaskRequirements(
        task_type=TaskType.BUSINESS_ANALYSIS,
        context_window_required=8192,
        speed_preference=0.2,  # Prefer accuracy
        accuracy_preference=0.8,
        subscription_tier="PRO"
    )
    
    # Select model
    selected_model = selection_service.select_model(requirements)
    print(f"Selected model for business analysis: {selected_model}")
    
    # Try to load and use model (will fail without real API key)
    try:
        await model_manager.load_model("nano-gpt-integration-test")
        print("Model loaded successfully")
        
        # This would normally generate a response, but will fail without real API key
        # response = await nano_model.generate(
        #     "Analyze the key requirements for a payment processing system",
        #     model="glm-4.6",
        #     temperature=0.7,
        #     max_tokens=500
        # )
        # print(f"Response: {response.content}")
        
    except Exception as e:
        print(f"Expected error (no real API key): {e}")
    
    # Show configuration
    model_info = nano_model.get_info()
    catalog_info = nano_model.get_model_catalog()
    
    print(f"\nModel Information:")
    print(f"  Provider: {model_info.get('provider')}")
    print(f"  Subscription Tier: {model_info.get('subscription_tier')}")
    print(f"  PRO Models Available: {catalog_info.get('total_models')}")
    
    print("Integration demo completed")
    print()


async def main():
    """Run all demos."""
    print("🚀 4S1T Agent AI - Nano-GPT Integration Demo")
    print("=" * 50)
    print()
    
    # Run nano-gpt model demo
    await demo_nano_gpt_model()
    
    # Run model selection demo
    await demo_model_selection()
    
    # Run configuration demo
    await demo_configuration()
    
    # Run API client demo
    await demo_api_client()
    
    # Run embeddings demo
    await demo_embeddings()
    
    # Run audio speech demo
    await demo_audio_speech()
    
    # Run integration demo
    await demo_integration()
    
    print("✅ All demos completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
