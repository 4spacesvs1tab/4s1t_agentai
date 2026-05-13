"""
Model selection service for the 4S1T Agent AI framework.

This module provides intelligent model selection based on task requirements,
subscription awareness, and performance optimization.
"""

from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

from .base import ModelManager, ModelType
from .nano_gpt import NanoGPTLanguageModel

from utils.logger import setup_logger
logger = setup_logger(__name__)


class TaskType(Enum):
    """Types of tasks that require AI model selection."""
    BUSINESS_ANALYSIS = "business_analysis"
    DATA_ANALYSIS = "data_analysis"
    QUICK_RESPONSE = "quick_response"
    REASONING = "reasoning"
    CODING = "coding"
    MATH_CALCULATION = "math_calculation"
    GENERAL_QUERY = "general_query"
    CHAT_CONVERSATION = "chat_conversation"


class ModelCategory(Enum):
    """Categories of AI models based on their strengths."""
    REASONING = "reasoning"
    GENERAL = "general"
    FAST_RESPONSE = "fast_response"
    CODING = "coding"
    MATH = "math"
    UNCENSORED = "uncensored"
    ROLEPLAYING = "roleplaying"


@dataclass
class ModelPerformance:
    """Performance metrics for a specific model."""
    model_name: str
    response_time_ms: float
    accuracy_score: float
    usage_count: int
    last_used: datetime
    success_rate: float = 1.0
    error_count: int = 0


@dataclass
class TaskRequirements:
    """Requirements for a specific task."""
    task_type: TaskType
    context_window_required: int = 4096
    speed_preference: float = 0.5  # 0.0 = accuracy preferred, 1.0 = speed preferred
    accuracy_preference: float = 0.5  # 0.0 = speed preferred, 1.0 = accuracy preferred
    subscription_tier: str = "PRO"
    whitelist_models: List[str] = field(default_factory=list)
    blacklist_models: List[str] = field(default_factory=list)
    preferred_models: List[str] = field(default_factory=list)


class ModelSelectionService:
    """
    Service for intelligent model selection based on task requirements
    and subscription awareness.
    """
    
    def __init__(self, model_manager: ModelManager):
        """
        Initialize the model selection service.
        
        Args:
            model_manager: Model manager instance
        """
        self.model_manager = model_manager
        self.performance_db: Dict[str, ModelPerformance] = {}
        self.logger = logger
        
        # Default model mappings for PRO subscription
        self.default_models = {
            TaskType.BUSINESS_ANALYSIS: "glm-4.6",
            TaskType.DATA_ANALYSIS: "qwen3-coder",
            TaskType.QUICK_RESPONSE: "kimi-k2-0905",
            TaskType.REASONING: "deepseek-r1",
            TaskType.CODING: "qwen3-coder",
            TaskType.MATH_CALCULATION: "math-models",
            TaskType.GENERAL_QUERY: "deepseek-v3.2",
            TaskType.CHAT_CONVERSATION: "glm-4.5"
        }
        
        # Model categorization for PRO subscription
        self.model_categories = {
            # Reasoning Models
            "glm-4.6": ModelCategory.REASONING,
            "glm-4.5": ModelCategory.REASONING,
            "deepseek-r1": ModelCategory.REASONING,
            
            # General Purpose Models
            "deepseek-v3.2": ModelCategory.GENERAL,
            "deepseek-v3.1": ModelCategory.GENERAL,
            
            # Fast Response Models
            "kimi-k2-0905": ModelCategory.FAST_RESPONSE,
            "kimi-k2-0711": ModelCategory.FAST_RESPONSE,
            
            # Coding Models
            "qwen3-coder": ModelCategory.CODING,
            "coding-specialists": ModelCategory.CODING,
            
            # Math Models
            "math-models": ModelCategory.MATH,
            
            # Specialty Models
            "venice": ModelCategory.UNCENSORED,
            "roleplaying": ModelCategory.ROLEPLAYING
        }
        
        # Context window sizes for models
        self.model_context_windows = {
            model_name: 128000 for model_name in self.model_categories.keys()
        }
    
    def select_model(self, requirements: TaskRequirements) -> Optional[str]:
        """
        Select the most appropriate model based on task requirements.
        
        Args:
            requirements: Task requirements for model selection
            
        Returns:
            str: Name of selected model, or None if no suitable model found
        """
        try:
            # Get available models
            available_models = self._get_available_models(requirements)
            
            if not available_models:
                self.logger.warning("No available models found for selection")
                return None
            
            # Apply whitelist/blacklist filtering
            filtered_models = self._apply_model_filters(available_models, requirements)
            
            if not filtered_models:
                self.logger.warning("No models passed whitelist/blacklist filtering")
                return None
            
            # Score models based on requirements
            scored_models = self._score_models(filtered_models, requirements)
            
            if not scored_models:
                self.logger.warning("No models scored successfully")
                return None
            
            # Select best model
            best_model = max(scored_models, key=lambda x: x[1])
            selected_model = best_model[0]
            
            self.logger.info(f"Selected model '{selected_model}' for task type {requirements.task_type.value}")
            return selected_model
            
        except Exception as e:
            self.logger.error(f"Error selecting model: {e}")
            # Fallback to default model
            return self._get_default_model(requirements)
    
    def select_model_with_fallback(self, requirements: TaskRequirements, 
                                 max_fallbacks: int = 3) -> Optional[Tuple[str, List[str]]]:
        """
        Select model with fallback options.
        
        Args:
            requirements: Task requirements for model selection
            max_fallbacks: Maximum number of fallback models to return
            
        Returns:
            Tuple[str, List[str]]: (selected_model, fallback_models) or (None, [])
        """
        try:
            # Get available models
            available_models = self._get_available_models(requirements)
            
            if not available_models:
                self.logger.warning("No available models found for selection")
                return None, []
            
            # Apply whitelist/blacklist filtering
            filtered_models = self._apply_model_filters(available_models, requirements)
            
            if not filtered_models:
                self.logger.warning("No models passed whitelist/blacklist filtering")
                return None, []
            
            # Score models based on requirements
            scored_models = self._score_models(filtered_models, requirements)
            
            if not scored_models:
                self.logger.warning("No models scored successfully")
                return None, []
            
            # Sort by score descending
            scored_models.sort(key=lambda x: x[1], reverse=True)
            
            # Select primary model
            primary_model = scored_models[0][0] if scored_models else None
            
            # Get fallback models (next best options)
            fallback_models = [model for model, score in scored_models[1:max_fallbacks+1]]
            
            self.logger.info(f"Selected model '{primary_model}' with {len(fallback_models)} fallbacks")
            return primary_model, fallback_models
            
        except Exception as e:
            self.logger.error(f"Error selecting model with fallbacks: {e}")
            # Fallback to default model
            default_model = self._get_default_model(requirements)
            return default_model, []
    
    def _get_available_models(self, requirements: TaskRequirements) -> List[str]:
        """
        Get list of available models based on subscription tier.
        
        Args:
            requirements: Task requirements
            
        Returns:
            List[str]: List of available model names
        """
        available_models = []
        
        # Check registered models
        for model_name, model in self.model_manager.models.items():
            # For Nano-GPT models, check subscription awareness
            if isinstance(model, NanoGPTLanguageModel):
                if model.subscription_tier == "PRO" or requirements.subscription_tier == "FREE":
                    if model.is_model_available(model_name) or requirements.subscription_tier == "FREE":
                        available_models.append(model_name)
            else:
                # For other models, just check if they're loaded
                if model.is_loaded():
                    available_models.append(model_name)
        
        # If no models are loaded, return PRO subscription models for reference
        if not available_models and requirements.subscription_tier == "PRO":
            available_models = list(self.model_categories.keys())
        
        return available_models
    
    def _apply_model_filters(self, models: List[str], requirements: TaskRequirements) -> List[str]:
        """
        Apply whitelist and blacklist filters to model list.
        
        Args:
            models: List of model names
            requirements: Task requirements
            
        Returns:
            List[str]: Filtered list of model names
        """
        filtered_models = models[:]
        
        # Apply whitelist
        if requirements.whitelist_models:
            filtered_models = [model for model in filtered_models if model in requirements.whitelist_models]
        
        # Apply blacklist
        if requirements.blacklist_models:
            filtered_models = [model for model in filtered_models if model not in requirements.blacklist_models]
        
        return filtered_models
    
    def _score_models(self, models: List[str], requirements: TaskRequirements) -> List[Tuple[str, float]]:
        """
        Score models based on requirements.
        
        Args:
            models: List of model names
            requirements: Task requirements
            
        Returns:
            List[Tuple[str, float]]: List of (model_name, score) tuples
        """
        scored_models = []
        
        for model_name in models:
            try:
                score = self._calculate_model_score(model_name, requirements)
                scored_models.append((model_name, score))
            except Exception as e:
                self.logger.warning(f"Error scoring model {model_name}: {e}")
                continue
        
        return scored_models
    
    def _calculate_model_score(self, model_name: str, requirements: TaskRequirements) -> float:
        """
        Calculate score for a specific model based on requirements.
        
        Args:
            model_name: Name of the model
            requirements: Task requirements
            
        Returns:
            float: Model score (0.0 to 1.0)
        """
        score_components = []
        
        # 1. Task type matching (40% weight)
        task_match_score = self._calculate_task_match_score(model_name, requirements.task_type)
        score_components.append(task_match_score * 0.4)
        
        # 2. Context window suitability (20% weight)
        context_score = self._calculate_context_suitability(model_name, requirements.context_window_required)
        score_components.append(context_score * 0.2)
        
        # 3. Performance metrics (20% weight)
        performance_score = self._calculate_performance_score(model_name)
        score_components.append(performance_score * 0.2)
        
        # 4. Preference alignment (10% weight)
        preference_score = self._calculate_preference_alignment(model_name, requirements)
        score_components.append(preference_score * 0.1)
        
        # 5. Preferred models boost (10% weight)
        preferred_boost = 0.1 if model_name in requirements.preferred_models else 0.0
        score_components.append(preferred_boost * 0.1)
        
        # Calculate final score
        final_score = sum(score_components)
        return min(1.0, max(0.0, final_score))  # Clamp between 0.0 and 1.0
    
    def _calculate_task_match_score(self, model_name: str, task_type: TaskType) -> float:
        """
        Calculate score based on task type matching.
        
        Args:
            model_name: Name of the model
            task_type: Required task type
            
        Returns:
            float: Task match score (0.0 to 1.0)
        """
        # Get expected model for this task type
        expected_model = self.default_models.get(task_type)
        if expected_model and model_name == expected_model:
            return 1.0
        
        # Check if model category matches task type
        model_category = self.model_categories.get(model_name)
        if not model_category:
            return 0.1  # Low score for unknown models
        
        # Define category-task mappings
        category_task_mapping = {
            ModelCategory.REASONING: [TaskType.BUSINESS_ANALYSIS, TaskType.REASONING],
            ModelCategory.GENERAL: [TaskType.GENERAL_QUERY],
            ModelCategory.FAST_RESPONSE: [TaskType.QUICK_RESPONSE, TaskType.CHAT_CONVERSATION],
            ModelCategory.CODING: [TaskType.DATA_ANALYSIS, TaskType.CODING],
            ModelCategory.MATH: [TaskType.MATH_CALCULATION],
            ModelCategory.UNCENSORED: [],
            ModelCategory.ROLEPLAYING: []
        }
        
        expected_tasks = category_task_mapping.get(model_category, [])
        if task_type in expected_tasks:
            return 0.8
        elif model_category in [ModelCategory.GENERAL, ModelCategory.FAST_RESPONSE]:
            # General models can handle most tasks reasonably well
            return 0.5
        else:
            return 0.3
    
    def _calculate_context_suitability(self, model_name: str, required_context: int) -> float:
        """
        Calculate score based on context window suitability.
        
        Args:
            model_name: Name of the model
            required_context: Required context window size
            
        Returns:
            float: Context suitability score (0.0 to 1.0)
        """
        model_context = self.model_context_windows.get(model_name, 4096)
        
        if model_context >= required_context:
            return 1.0
        elif model_context >= required_context * 0.8:
            return 0.8
        elif model_context >= required_context * 0.5:
            return 0.5
        else:
            return 0.1
    
    def _calculate_performance_score(self, model_name: str) -> float:
        """
        Calculate score based on historical performance.
        
        Args:
            model_name: Name of the model
            
        Returns:
            float: Performance score (0.0 to 1.0)
        """
        if model_name not in self.performance_db:
            return 0.7  # Neutral score for unknown models
        
        performance = self.performance_db[model_name]
        
        # Weighted average of success rate and response time
        success_component = performance.success_rate
        # Normalize response time (faster is better)
        response_time_normalized = max(0.0, min(1.0, 1000.0 / max(1.0, performance.response_time_ms)))
        
        return (success_component * 0.6 + response_time_normalized * 0.4)
    
    def _calculate_preference_alignment(self, model_name: str, requirements: TaskRequirements) -> float:
        """
        Calculate score based on speed/accuracy preferences.
        
        Args:
            model_name: Name of the model
            requirements: Task requirements
            
        Returns:
            float: Preference alignment score (0.0 to 1.0)
        """
        model_category = self.model_categories.get(model_name)
        if not model_category:
            return 0.5  # Neutral score
        
        # Fast models are better for speed preference
        fast_categories = [ModelCategory.FAST_RESPONSE]
        # Accurate models are better for accuracy preference
        accurate_categories = [ModelCategory.REASONING, ModelCategory.MATH]
        
        if requirements.speed_preference > requirements.accuracy_preference:
            # Speed is more important
            if model_category in fast_categories:
                return 0.9
            elif model_category in accurate_categories:
                return 0.3
            else:
                return 0.6
        elif requirements.accuracy_preference > requirements.speed_preference:
            # Accuracy is more important
            if model_category in accurate_categories:
                return 0.9
            elif model_category in fast_categories:
                return 0.3
            else:
                return 0.6
        else:
            # Balanced preference
            return 0.7
    
    def _get_default_model(self, requirements: TaskRequirements) -> Optional[str]:
        """
        Get default model for requirements.
        
        Args:
            requirements: Task requirements
            
        Returns:
            str: Default model name, or None if not found
        """
        return self.default_models.get(requirements.task_type)
    
    def update_model_performance(self, model_name: str, response_time_ms: float, 
                               success: bool = True, accuracy_score: float = 1.0):
        """
        Update performance metrics for a model.
        
        Args:
            model_name: Name of the model
            response_time_ms: Response time in milliseconds
            success: Whether the request was successful
            accuracy_score: Accuracy score (0.0 to 1.0)
        """
        try:
            if model_name not in self.performance_db:
                self.performance_db[model_name] = ModelPerformance(
                    model_name=model_name,
                    response_time_ms=response_time_ms,
                    accuracy_score=accuracy_score,
                    usage_count=1,
                    last_used=datetime.now(),
                    success_rate=1.0 if success else 0.0,
                    error_count=0 if success else 1
                )
            else:
                performance = self.performance_db[model_name]
                total_usages = performance.usage_count + 1
                total_success = performance.success_rate * performance.usage_count + (1.0 if success else 0.0)
                total_errors = performance.error_count + (0 if success else 1)
                
                performance.response_time_ms = (
                    (performance.response_time_ms * performance.usage_count + response_time_ms) / total_usages
                )
                performance.accuracy_score = (
                    (performance.accuracy_score * performance.usage_count + accuracy_score) / total_usages
                )
                performance.usage_count = total_usages
                performance.last_used = datetime.now()
                performance.success_rate = total_success / total_usages
                performance.error_count = total_errors
                
        except Exception as e:
            self.logger.error(f"Error updating model performance for {model_name}: {e}")
    
    def get_model_recommendations(self, task_type: TaskType, count: int = 3) -> List[Tuple[str, float]]:
        """
        Get model recommendations for a specific task type.
        
        Args:
            task_type: Task type to recommend models for
            count: Number of recommendations to return
            
        Returns:
            List[Tuple[str, float]]: List of (model_name, score) tuples
        """
        requirements = TaskRequirements(
            task_type=task_type,
            subscription_tier="PRO"  # Assume PRO for recommendations
        )
        
        available_models = self._get_available_models(requirements)
        scored_models = self._score_models(available_models, requirements)
        
        # Sort by score descending and return top models
        scored_models.sort(key=lambda x: x[1], reverse=True)
        return scored_models[:count]


# Global model selection service instance
model_selection_service: Optional[ModelSelectionService] = None


def get_model_selection_service(model_manager: ModelManager = None) -> ModelSelectionService:
    """
    Get singleton model selection service instance.
    
    Args:
        model_manager: Model manager instance (required for first call)
        
    Returns:
        ModelSelectionService instance
    """
    global model_selection_service
    if model_selection_service is None:
        if model_manager is None:
            raise ValueError("Model manager required for first initialization")
        model_selection_service = ModelSelectionService(model_manager)
    return model_selection_service
