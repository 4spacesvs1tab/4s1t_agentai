"""
Tests for the prompt optimization system.
"""

import sys
import os
from datetime import datetime

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.prompt.template import PromptTemplate, PromptTemplateManager
from ai.prompt.optimizer import PromptOptimizer, PromptPerformanceMetrics


def test_performance_recording():
    """Test recording prompt execution performance."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Record a successful execution
    optimizer.record_execution(
        prompt_name="test-prompt",
        version="1.0.0",
        success=True,
        latency_ms=250.0,
        relevance_score=0.8,
        helpfulness_score=0.9,
        user_feedback=0.85
    )
    
    # Record a failed execution
    optimizer.record_execution(
        prompt_name="test-prompt",
        version="1.0.0",
        success=False,
        latency_ms=300.0,
        relevance_score=0.2,
        helpfulness_score=0.1,
        user_feedback=0.0
    )
    
    # Check metrics
    metrics = optimizer.get_performance_metrics("test-prompt")
    assert metrics is not None
    assert metrics.execution_count == 2
    assert metrics.success_rate == 0.5  # 1 success, 1 failure
    assert metrics.average_latency_ms == 275.0  # Average of 250 and 300
    assert metrics.average_relevance_score == 0.5  # Average of 0.8 and 0.2
    
    print("✓ Performance recording test passed")


def test_optimization_score_calculation():
    """Test optimization score calculation."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Create metrics with good performance
    good_metrics = PromptPerformanceMetrics(
        prompt_name="good-prompt",
        version="1.0.0",
        execution_count=10,
        average_latency_ms=200.0,
        success_rate=0.95,
        average_relevance_score=0.9,
        average_helpfulness_score=0.85,
        user_feedback_score=0.9
    )
    
    # Create metrics with poor performance
    poor_metrics = PromptPerformanceMetrics(
        prompt_name="poor-prompt",
        version="1.0.0",
        execution_count=10,
        average_latency_ms=800.0,
        success_rate=0.3,
        average_relevance_score=0.4,
        average_helpfulness_score=0.35,
        user_feedback_score=0.2
    )
    
    # Calculate scores
    good_score = optimizer.calculate_optimization_score(good_metrics)
    poor_score = optimizer.calculate_optimization_score(poor_metrics)
    
    # Good performance should have higher score
    assert good_score > poor_score
    assert 0 <= good_score <= 1
    assert 0 <= poor_score <= 1
    
    print("✓ Optimization score calculation test passed")


def test_optimization_suggestions():
    """Test optimization suggestion generation."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Create a template
    template = PromptTemplate(
        name="suggest-test",
        template="Analyze {text}",
        version="1.0.0"
    )
    manager.register_template(template)
    
    # Record poor performance
    optimizer.record_execution(
        prompt_name="suggest-test",
        version="1.0.0",
        success=True,
        latency_ms=600.0,
        relevance_score=0.5,
        helpfulness_score=0.4,
        user_feedback=0.3
    )
    
    # Get suggestions
    suggestions = optimizer.suggest_optimizations("suggest-test")
    assert len(suggestions) > 0
    assert isinstance(suggestions, list)
    assert all(isinstance(s, str) for s in suggestions)
    
    # At least one suggestion should mention improvement areas
    assert any("clarity" in s.lower() or "simplify" in s.lower() or 
               "specific" in s.lower() for s in suggestions)
    
    print("✓ Optimization suggestions test passed")


def test_prompt_optimization():
    """Test prompt optimization functionality."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Create and register a template
    original_template = PromptTemplate(
        name="optimize-test",
        template="Tell me about {topic}",
        version="1.0.0",
        description="Simple prompt for optimization testing"
    )
    manager.register_template(original_template)
    
    # Record some performance data
    optimizer.record_execution(
        prompt_name="optimize-test",
        version="1.0.0",
        success=True,
        latency_ms=400.0,
        relevance_score=0.6,
        helpfulness_score=0.5,
        user_feedback=0.4
    )
    
    # Try to optimize
    result = optimizer.optimize_prompt("optimize-test")
    
    # Check that optimization was successful
    assert result is not None
    assert result.original_template.name == "optimize-test"
    assert result.optimized_template.version != "1.0.0"  # Should have new version
    assert len(result.changes_made) > 0
    
    # Check that the optimized template was registered
    optimized_template = manager.get_template("optimize-test", result.optimized_template.version)
    assert optimized_template is not None
    
    print("✓ Prompt optimization test passed")


def test_version_increment():
    """Test version incrementing functionality."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Test normal version incrementing
    assert optimizer._increment_version("1.0.0") == "1.0.1"
    assert optimizer._increment_version("2.5.3") == "2.5.4"
    assert optimizer._increment_version("1.0") == "1.1"
    
    print("✓ Version increment test passed")


def test_optimization_history():
    """Test optimization history tracking."""
    manager = PromptTemplateManager()
    optimizer = PromptOptimizer(manager)
    
    # Create templates
    template1 = PromptTemplate(name="history-test-1", template="Test {input}", version="1.0.0")
    template2 = PromptTemplate(name="history-test-2", template="Test {output}", version="1.0.0")
    
    manager.register_template(template1)
    manager.register_template(template2)
    
    # Optimize both templates
    result1 = optimizer.optimize_prompt("history-test-1")
    result2 = optimizer.optimize_prompt("history-test-2")
    
    # Check history
    history = optimizer.get_optimization_history()
    assert len(history) == 2
    assert result1 in history
    assert result2 in history
    
    # Check filtered history
    filtered_history = optimizer.get_optimization_history("history-test-1")
    assert len(filtered_history) == 1
    assert filtered_history[0].original_template.name == "history-test-1"
    
    print("✓ Optimization history test passed")


if __name__ == "__main__":
    # Run all tests
    test_performance_recording()
    test_optimization_score_calculation()
    test_optimization_suggestions()
    test_prompt_optimization()
    test_version_increment()
    test_optimization_history()
    print("\n🎉 All prompt optimizer tests passed!")
