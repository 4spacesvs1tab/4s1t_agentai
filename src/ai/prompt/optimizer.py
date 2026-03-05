"""
Prompt optimization system for the 4S1T Agent AI framework.

This module provides mechanisms for optimizing prompts based on performance metrics,
feedback, and automated analysis.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Callable
from datetime import datetime
import logging
import re
import statistics

from .template import PromptTemplate, PromptTemplateManager

logger = logging.getLogger(__name__)


@dataclass
class PromptPerformanceMetrics:
    """Metrics for evaluating prompt performance."""
    
    prompt_name: str
    version: str
    execution_count: int = 0
    average_latency_ms: float = 0.0
    success_rate: float = 0.0
    average_relevance_score: float = 0.0
    average_helpfulness_score: float = 0.0
    user_feedback_score: float = 0.0
    last_executed: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationResult:
    """Result of a prompt optimization attempt."""
    
    original_template: PromptTemplate
    optimized_template: PromptTemplate
    improvement_score: float
    metrics_before: PromptPerformanceMetrics
    metrics_after: PromptPerformanceMetrics
    changes_made: List[str]
    confidence_level: float
    created_at: datetime = field(default_factory=datetime.now)


class PromptOptimizer:
    """
    Optimizer for prompt templates in the 4S1T Agent AI framework.
    
    This class provides various optimization strategies for improving prompt effectiveness
    based on performance metrics and feedback.
    """
    
    def __init__(self, template_manager: PromptTemplateManager):
        """
        Initialize the prompt optimizer.
        
        Args:
            template_manager: The template manager to work with
        """
        self.template_manager = template_manager
        self.performance_metrics: Dict[str, Dict[str, PromptPerformanceMetrics]] = {}
        self.optimization_history: List[OptimizationResult] = []
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def record_execution(self, prompt_name: str, version: str, 
                        success: bool, latency_ms: float, 
                        relevance_score: float = 0.0, helpfulness_score: float = 0.0,
                        user_feedback: float = 0.0, metadata: Optional[Dict[str, Any]] = None):
        """
        Record execution metrics for a prompt.
        
        Args:
            prompt_name: Name of the prompt template
            version: Version of the prompt template
            success: Whether the execution was successful
            latency_ms: Execution time in milliseconds
            relevance_score: Relevance score (0-1)
            helpfulness_score: Helpfulness score (0-1)
            user_feedback: User feedback score (0-1)
            metadata: Additional metadata
        """
        try:
            # Initialize metrics if not exists
            if prompt_name not in self.performance_metrics:
                self.performance_metrics[prompt_name] = {}
            
            if version not in self.performance_metrics[prompt_name]:
                self.performance_metrics[prompt_name][version] = PromptPerformanceMetrics(
                    prompt_name=prompt_name,
                    version=version
                )
            
            metrics = self.performance_metrics[prompt_name][version]
            
            # Update metrics
            metrics.execution_count += 1
            metrics.success_rate = (
                (metrics.success_rate * (metrics.execution_count - 1) + (1 if success else 0)) 
                / metrics.execution_count
            )
            metrics.average_latency_ms = (
                (metrics.average_latency_ms * (metrics.execution_count - 1) + latency_ms) 
                / metrics.execution_count
            )
            
            if relevance_score > 0:
                metrics.average_relevance_score = (
                    (metrics.average_relevance_score * (metrics.execution_count - 1) + relevance_score) 
                    / metrics.execution_count
                )
            
            if helpfulness_score > 0:
                metrics.average_helpfulness_score = (
                    (metrics.average_helpfulness_score * (metrics.execution_count - 1) + helpfulness_score) 
                    / metrics.execution_count
                )
            
            if user_feedback > 0:
                metrics.user_feedback_score = (
                    (metrics.user_feedback_score * (metrics.execution_count - 1) + user_feedback) 
                    / metrics.execution_count
                )
            
            metrics.last_executed = datetime.now()
            if metadata:
                metrics.metadata.update(metadata)
                
            self.logger.debug(f"Recorded execution for {prompt_name} v{version}")
        except Exception as e:
            self.logger.error(f"Failed to record execution for {prompt_name} v{version}: {e}")
    
    def get_performance_metrics(self, prompt_name: str, 
                              version: Optional[str] = None) -> Optional[PromptPerformanceMetrics]:
        """
        Get performance metrics for a prompt.
        
        Args:
            prompt_name: Name of the prompt template
            version: Version of the prompt template (latest if None)
            
        Returns:
            PromptPerformanceMetrics: Performance metrics, or None if not found
        """
        if prompt_name not in self.performance_metrics:
            return None
        
        if version:
            return self.performance_metrics[prompt_name].get(version)
        else:
            # Return latest version metrics
            versions = list(self.performance_metrics[prompt_name].keys())
            if versions:
                latest_version = sorted(versions, reverse=True)[0]
                return self.performance_metrics[prompt_name][latest_version]
        
        return None
    
    def calculate_optimization_score(self, metrics: PromptPerformanceMetrics) -> float:
        """
        Calculate an optimization score based on performance metrics.
        
        Args:
            metrics: Performance metrics
            
        Returns:
            float: Optimization score (higher is better)
        """
        # Weighted scoring system
        weights = {
            'success_rate': 0.3,
            'relevance_score': 0.25,
            'helpfulness_score': 0.25,
            'user_feedback': 0.15,
            'latency_inverse': 0.05  # Lower latency is better
        }
        
        # Normalize latency (assuming 1000ms as baseline)
        latency_score = max(0, 1 - (metrics.average_latency_ms / 1000))
        
        score = (
            weights['success_rate'] * metrics.success_rate +
            weights['relevance_score'] * metrics.average_relevance_score +
            weights['helpfulness_score'] * metrics.average_helpfulness_score +
            weights['user_feedback'] * metrics.user_feedback_score +
            weights['latency_inverse'] * latency_score
        )
        
        return score
    
    def suggest_optimizations(self, prompt_name: str, 
                            version: Optional[str] = None) -> List[str]:
        """
        Suggest optimizations for a prompt based on performance metrics.
        
        Args:
            prompt_name: Name of the prompt template
            version: Version of the prompt template (latest if None)
            
        Returns:
            List[str]: List of suggested optimizations
        """
        metrics = self.get_performance_metrics(prompt_name, version)
        if not metrics:
            return ["No performance data available for optimization suggestions"]
        
        suggestions = []
        
        # Success rate issues
        if metrics.success_rate < 0.8:
            suggestions.append("Consider rephrasing the prompt for better clarity")
            suggestions.append("Add more specific instructions or examples")
        
        # Relevance issues
        if metrics.average_relevance_score < 0.7:
            suggestions.append("Focus on making the prompt more specific to the task")
            suggestions.append("Add constraints to narrow down the response scope")
        
        # Helpfulness issues
        if metrics.average_helpfulness_score < 0.7:
            suggestions.append("Include examples of desired output format")
            suggestions.append("Specify the level of detail required in responses")
        
        # Latency issues
        if metrics.average_latency_ms > 500:
            suggestions.append("Simplify the prompt to reduce processing time")
            suggestions.append("Break complex prompts into smaller steps")
        
        # User feedback issues
        if metrics.user_feedback_score < 0.6:
            suggestions.append("Review user feedback for specific pain points")
            suggestions.append("Consider A/B testing different prompt variations")
        
        return suggestions if suggestions else ["Prompt performance is good, no immediate optimizations needed"]
    
    def optimize_prompt(self, prompt_name: str, version: Optional[str] = None,
                       strategy: str = "auto") -> Optional[OptimizationResult]:
        """
        Optimize a prompt template using various strategies.
        
        Args:
            prompt_name: Name of the prompt template
            version: Version of the prompt template (latest if None)
            strategy: Optimization strategy ("auto", "clarity", "brevity", "specificity")
            
        Returns:
            OptimizationResult: Result of optimization, or None if failed
        """
        try:
            # Get the original template
            original_template = self.template_manager.get_template(prompt_name, version)
            if not original_template:
                self.logger.error(f"Template {prompt_name} not found for optimization")
                return None
            
            # Get current performance metrics
            metrics_before = self.get_performance_metrics(prompt_name, original_template.version)
            if not metrics_before:
                self.logger.warning(f"No performance data for {prompt_name}, proceeding with basic optimization")
                metrics_before = PromptPerformanceMetrics(prompt_name, original_template.version)
            
            # Apply optimization strategy
            if strategy == "auto":
                optimized_template, changes = self._auto_optimize(original_template, metrics_before)
            elif strategy == "clarity":
                optimized_template, changes = self._optimize_clarity(original_template)
            elif strategy == "brevity":
                optimized_template, changes = self._optimize_brevity(original_template)
            elif strategy == "specificity":
                optimized_template, changes = self._optimize_specificity(original_template)
            else:
                self.logger.error(f"Unknown optimization strategy: {strategy}")
                return None
            
            # Calculate improvement score
            score_before = self.calculate_optimization_score(metrics_before)
            
            # For now, we'll estimate improvement (in a real system, we'd test the new prompt)
            # This is a simplified estimation
            estimated_improvement = min(0.2, score_before * 0.1)  # Cap at 20% improvement
            score_after = min(1.0, score_before + estimated_improvement)
            
            metrics_after = PromptPerformanceMetrics(
                prompt_name=optimized_template.name,
                version=optimized_template.version,
                execution_count=metrics_before.execution_count,
                average_latency_ms=metrics_before.average_latency_ms * 0.9,  # Assume 10% faster
                success_rate=min(1.0, metrics_before.success_rate * 1.1),  # Assume 10% better
                average_relevance_score=min(1.0, metrics_before.average_relevance_score * 1.05),
                average_helpfulness_score=min(1.0, metrics_before.average_helpfulness_score * 1.05),
                user_feedback_score=min(1.0, metrics_before.user_feedback_score * 1.05)
            )
            
            result = OptimizationResult(
                original_template=original_template,
                optimized_template=optimized_template,
                improvement_score=score_after - score_before,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
                changes_made=changes,
                confidence_level=0.7  # Medium confidence for automated optimization
            )
            
            # Register the optimized template with a new version
            new_version = self._increment_version(original_template.version)
            optimized_template.version = new_version
            optimized_template.updated_at = datetime.now()
            
            if self.template_manager.register_template(optimized_template):
                self.optimization_history.append(result)
                self.logger.info(f"Optimized prompt {prompt_name}: {original_template.version} -> {new_version}")
                return result
            else:
                self.logger.error(f"Failed to register optimized template for {prompt_name}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to optimize prompt {prompt_name}: {e}")
            return None
    
    def _auto_optimize(self, template: PromptTemplate, 
                      metrics: PromptPerformanceMetrics) -> Tuple[PromptTemplate, List[str]]:
        """
        Automatically optimize a prompt based on performance metrics.
        
        Args:
            template: Template to optimize
            metrics: Performance metrics
            
        Returns:
            Tuple[PromptTemplate, List[str]]: Optimized template and changes made
        """
        changes = []
        optimized_template = PromptTemplate(
            name=template.name,
            template=template.template,
            description=template.description,
            version=template.version,
            tags=template.tags.copy(),
            metadata=template.metadata.copy()
        )
        
        # Apply optimizations based on metrics
        if metrics.success_rate < 0.8:
            optimized_template, clarity_changes = self._optimize_clarity(optimized_template)
            changes.extend(clarity_changes)
        
        if metrics.average_latency_ms > 500:
            optimized_template, brevity_changes = self._optimize_brevity(optimized_template)
            changes.extend(brevity_changes)
        
        if metrics.average_relevance_score < 0.7 or metrics.average_helpfulness_score < 0.7:
            optimized_template, specificity_changes = self._optimize_specificity(optimized_template)
            changes.extend(specificity_changes)
        
        # If no specific issues, apply general improvements
        if not changes:
            optimized_template, general_changes = self._general_improvements(optimized_template)
            changes.extend(general_changes)
        
        return optimized_template, changes
    
    def _optimize_clarity(self, template: PromptTemplate) -> Tuple[PromptTemplate, List[str]]:
        """
        Optimize prompt for clarity by restructuring and adding guidance.
        
        Args:
            template: Template to optimize
            
        Returns:
            Tuple[PromptTemplate, List[str]]: Optimized template and changes made
        """
        changes = []
        optimized_template = PromptTemplate(
            name=template.name,
            template=template.template,
            description=template.description,
            version=template.version,
            tags=template.tags.copy(),
            metadata=template.metadata.copy()
        )
        
        # Add structure if missing
        if not re.search(r'^[\*\-\d]+\.', optimized_template.template.strip(), re.MULTILINE):
            # Add step-by-step structure
            lines = optimized_template.template.strip().split('\n')
            if len(lines) > 1:
                structured_template = "Please follow these steps:\n\n"
                for i, line in enumerate(lines, 1):
                    structured_template += f"{i}. {line}\n"
                optimized_template.template = structured_template
                changes.append("Added step-by-step structure for better clarity")
        
        # Add role playing if missing
        if not re.search(r'[Yy]ou are|Act as', optimized_template.template):
            optimized_template.template = f"You are an expert AI assistant. {optimized_template.template}"
            changes.append("Added role-playing instruction for better context")
        
        return optimized_template, changes
    
    def _optimize_brevity(self, template: PromptTemplate) -> Tuple[PromptTemplate, List[str]]:
        """
        Optimize prompt for brevity by removing redundant elements.
        
        Args:
            template: Template to optimize
            
        Returns:
            Tuple[PromptTemplate, List[str]]: Optimized template and changes made
        """
        changes = []
        optimized_template = PromptTemplate(
            name=template.name,
            template=template.template,
            description=template.description,
            version=template.version,
            tags=template.tags.copy(),
            metadata=template.metadata.copy()
        )
        
        # Remove redundant phrases
        redundant_phrases = [
            r'\bplease note that\b',
            r'\bas you know\b',
            r'\bobviously\b',
            r'\bas mentioned before\b',
            r'\bin conclusion\b'
        ]
        
        original_length = len(optimized_template.template)
        for phrase in redundant_phrases:
            optimized_template.template = re.sub(phrase, '', optimized_template.template, flags=re.IGNORECASE)
        
        if len(optimized_template.template) < original_length:
            changes.append(f"Removed redundant phrases, reduced length by {original_length - len(optimized_template.template)} characters")
        
        # Shorten overly long sentences
        sentences = re.split(r'[.!?]+', optimized_template.template)
        shortened = False
        for i, sentence in enumerate(sentences):
            if len(sentence) > 100:  # Arbitrary threshold
                # Try to shorten by removing clauses
                clauses = re.split(r',[^"\']*(?=["\'])|,(?=[^"\']*["\']|$)', sentence)
                if len(clauses) > 2:
                    # Keep first and last clause
                    shortened_sentence = clauses[0] + ", " + clauses[-1]
                    sentences[i] = shortened_sentence
                    shortened = True
        
        if shortened:
            optimized_template.template = '. '.join(sentences) + '.'
            changes.append("Shortened overly long sentences for better processing")
        
        return optimized_template, changes
    
    def _optimize_specificity(self, template: PromptTemplate) -> Tuple[PromptTemplate, List[str]]:
        """
        Optimize prompt for specificity by adding constraints and examples.
        
        Args:
            template: Template to optimize
            
        Returns:
            Tuple[PromptTemplate, List[str]]: Optimized template and changes made
        """
        changes = []
        optimized_template = PromptTemplate(
            name=template.name,
            template=template.template,
            description=template.description,
            version=template.version,
            tags=template.tags.copy(),
            metadata=template.metadata.copy()
        )
        
        # Add format specification if missing
        if not re.search(r'format|structure', optimized_template.template, re.IGNORECASE):
            optimized_template.template += "\n\nRespond in a clear, structured format."
            changes.append("Added format specification for more specific responses")
        
        # Add length constraint if missing
        if not re.search(r'word|sentence|paragraph', optimized_template.template, re.IGNORECASE):
            optimized_template.template += "\n\nKeep your response concise, preferably under 200 words."
            changes.append("Added length constraint for more specific responses")
        
        # Add example if no examples exist
        if not template.examples:
            # This would normally add a real example, but we'll just note the intent
            changes.append("Consider adding concrete examples to guide the response")
        
        return optimized_template, changes
    
    def _general_improvements(self, template: PromptTemplate) -> Tuple[PromptTemplate, List[str]]:
        """
        Apply general improvements to a prompt.
        
        Args:
            template: Template to improve
            
        Returns:
            Tuple[PromptTemplate, List[str]]: Improved template and changes made
        """
        changes = []
        optimized_template = PromptTemplate(
            name=template.name,
            template=template.template,
            description=template.description,
            version=template.version,
            tags=template.tags.copy(),
            metadata=template.metadata.copy()
        )
        
        # Add thinking time instruction
        if not re.search(r'take a deep breath|think step by step|carefully consider', 
                        optimized_template.template, re.IGNORECASE):
            optimized_template.template = f"Take a deep breath and think step by step.\n\n{optimized_template.template}"
            changes.append("Added thinking instruction for better reasoning")
        
        # Add self-evaluation prompt
        if not re.search(r'check|verify|review', optimized_template.template, re.IGNORECASE):
            optimized_template.template += "\n\nBefore responding, briefly check that your answer addresses all parts of the question."
            changes.append("Added self-evaluation instruction for better quality")
        
        return optimized_template, changes
    
    def _increment_version(self, version: str) -> str:
        """
        Increment a semantic version string.
        
        Args:
            version: Version string (e.g., "1.2.3")
            
        Returns:
            str: Incremented version string
        """
        try:
            parts = version.split('.')
            if len(parts) >= 2:
                # Increment patch version
                patch = int(parts[-1]) + 1
                parts[-1] = str(patch)
            else:
                # If only major version, add minor
                parts.append('1')
            return '.'.join(parts)
        except:
            # Fallback to timestamp if version parsing fails
            return datetime.now().strftime("%Y%m%d.%H%M%S")
    
    def get_optimization_history(self, prompt_name: Optional[str] = None) -> List[OptimizationResult]:
        """
        Get optimization history, optionally filtered by prompt name.
        
        Args:
            prompt_name: Filter by prompt name (None for all)
            
        Returns:
            List[OptimizationResult]: List of optimization results
        """
        if prompt_name:
            return [r for r in self.optimization_history if r.original_template.name == prompt_name]
        return self.optimization_history.copy()


# Example usage and testing functions
def create_sample_optimizer() -> Tuple[PromptOptimizer, PromptTemplateManager]:
    """Create a sample optimizer with test data for demonstration."""
    # Create template manager and register a sample template
    manager = PromptTemplateManager()
    
    sample_template = PromptTemplate(
        name="sample-analysis",
        template="Analyze the following text: {text}\n\nProvide insights about {aspect}.",
        description="Sample template for text analysis",
        tags=["analysis", "text-processing"]
    )
    
    manager.register_template(sample_template)
    
    # Create optimizer
    optimizer = PromptOptimizer(manager)
    
    # Record some sample executions
    optimizer.record_execution(
        prompt_name="sample-analysis",
        version="1.0.0",
        success=True,
        latency_ms=450.0,
        relevance_score=0.6,
        helpfulness_score=0.5,
        user_feedback=0.4
    )
    
    optimizer.record_execution(
        prompt_name="sample-analysis",
        version="1.0.0",
        success=False,
        latency_ms=620.0,
        relevance_score=0.3,
        helpfulness_score=0.2,
        user_feedback=0.1
    )
    
    return optimizer, manager


if __name__ == "__main__":
    # Demonstrate optimizer functionality
    optimizer, manager = create_sample_optimizer()
    
    # Get performance metrics
    metrics = optimizer.get_performance_metrics("sample-analysis")
    if metrics:
        print(f"Performance metrics for sample-analysis:")
        print(f"  Success rate: {metrics.success_rate:.2f}")
        print(f"  Average latency: {metrics.average_latency_ms:.2f}ms")
        print(f"  Relevance score: {metrics.average_relevance_score:.2f}")
        
        # Calculate optimization score
        score = optimizer.calculate_optimization_score(metrics)
        print(f"  Optimization score: {score:.3f}")
        
        # Get suggestions
        suggestions = optimizer.suggest_optimizations("sample-analysis")
        print(f"  Suggestions: {suggestions}")
        
        # Try optimization
        result = optimizer.optimize_prompt("sample-analysis")
        if result:
            print(f"  Optimization result:")
            print(f"    Improvement score: {result.improvement_score:.3f}")
            print(f"    Changes made: {result.changes_made}")
            print(f"    New version: {result.optimized_template.version}")
