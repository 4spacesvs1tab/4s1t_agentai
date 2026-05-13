"""
Tests for the prompt testing system.
"""

import sys
import os
from datetime import datetime

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.prompt.template import PromptTemplate, PromptTemplateManager
from ai.prompt.testing import PromptTester, ABTestConfig, TestType, TestStatus


def test_ab_testing():
    """Test A/B testing functionality."""
    # Create template manager and register sample templates
    manager = PromptTemplateManager()
    
    template_v1 = PromptTemplate(
        name="ab-test-prompt",
        template="Question: {question}\nAnswer:",
        version="1.0.0"
    )
    
    template_v2 = PromptTemplate(
        name="ab-test-prompt",
        template="Please answer clearly.\n\nQuestion: {question}\nAnswer:",
        version="2.0.0"
    )
    
    manager.register_template(template_v1)
    manager.register_template(template_v2)
    
    # Create tester
    tester = PromptTester(manager)
    
    # Create A/B test config
    test_config = ABTestConfig(
        prompt_name="ab-test-prompt",
        version_a="1.0.0",
        version_b="2.0.0",
        test_cases=[
            {
                "variables": {"question": "What is 2+2?"},
                "expected_output": "4",
                "evaluation_criteria": {
                    "required_keywords": ["4", "four"],
                    "max_length": 100
                }
            },
            {
                "variables": {"question": "What is AI?"},
                "expected_output": "Artificial Intelligence",
                "evaluation_criteria": {
                    "required_keywords": ["intelligence", "artificial"],
                    "max_length": 200
                }
            }
        ],
        sample_size=2
    )
    
    # Run A/B test
    result = tester.run_ab_test(test_config)
    
    # Check result
    assert result is not None
    assert result.test_type == TestType.A_B_TEST
    assert result.status == TestStatus.COMPLETED
    assert len(result.versions_tested) == 2
    assert "version_a_average" in result.metrics
    assert "version_b_average" in result.metrics
    
    print("✓ A/B testing test passed")


def test_validation_testing():
    """Test validation testing functionality."""
    # Create template manager and register sample template
    manager = PromptTemplateManager()
    
    template = PromptTemplate(
        name="validation-test-prompt",
        template="Answer: {question}",
        version="1.0.0"
    )
    
    manager.register_template(template)
    
    # Create tester
    tester = PromptTester(manager)
    
    # Run validation test
    result = tester.run_validation_test(
        prompt_name="validation-test-prompt",
        version="1.0.0",
        test_cases=[
            {
                "variables": {"question": "Test question"},
                "validation_criteria": {
                    "min_length": 5,
                    "max_length": 100
                }
            }
        ]
    )
    
    # Check result
    assert result is not None
    assert result.test_type == TestType.VALIDATION
    assert result.status == TestStatus.COMPLETED
    assert result.versions_tested == ["1.0.0"]
    assert "passed_tests" in result.metrics
    assert "total_tests" in result.metrics
    
    print("✓ Validation testing test passed")


def test_version_comparison():
    """Test version comparison functionality."""
    # Create template manager and register sample templates
    manager = PromptTemplateManager()
    
    template_v1 = PromptTemplate(
        name="compare-test-prompt",
        template="Q: {question}",
        version="1.0.0"
    )
    
    template_v2 = PromptTemplate(
        name="compare-test-prompt",
        template="Please answer the following question:\n\nQuestion: {question}\n\nAnswer:",
        version="2.0.0"
    )
    
    manager.register_template(template_v1)
    manager.register_template(template_v2)
    
    # Create tester
    tester = PromptTester(manager)
    
    # Run version comparison
    result = tester.compare_versions("compare-test-prompt", ["1.0.0", "2.0.0"])
    
    # Check result
    assert result is not None
    assert result.test_type == TestType.VERSION_COMPARISON
    assert result.status == TestStatus.COMPLETED
    assert len(result.versions_tested) == 2
    assert "complexity_scores" in result.metrics
    assert "simplest_version" in result.metrics
    
    print("✓ Version comparison test passed")


def test_test_result_filtering():
    """Test filtering of test results."""
    # Create template manager and register sample templates
    manager = PromptTemplateManager()
    
    template = PromptTemplate(
        name="filter-test-prompt",
        template="Question: {question}",
        version="1.0.0"
    )
    
    manager.register_template(template)
    
    # Create tester
    tester = PromptTester(manager)
    
    # Run a few tests
    tester.run_validation_test(
        prompt_name="filter-test-prompt",
        version="1.0.0",
        test_cases=[{"variables": {"question": "Test"}}]
    )
    
    # Get all results
    all_results = tester.get_test_results()
    assert len(all_results) >= 1
    
    # Filter by prompt name
    filtered_results = tester.get_test_results(prompt_name="filter-test-prompt")
    assert len(filtered_results) >= 1
    assert all(r.prompt_name == "filter-test-prompt" for r in filtered_results)
    
    # Filter by test type
    validation_results = tester.get_test_results(test_type=TestType.VALIDATION)
    assert all(r.test_type == TestType.VALIDATION for r in validation_results)
    
    print("✓ Test result filtering test passed")


def test_active_tests():
    """Test active tests tracking."""
    # Create template manager and register sample template
    manager = PromptTemplateManager()
    
    template = PromptTemplate(
        name="active-test-prompt",
        template="Question: {question}",
        version="1.0.0"
    )
    
    manager.register_template(template)
    
    # Create tester
    tester = PromptTester(manager)
    
    # Check active tests (should be empty initially)
    active_tests = tester.get_active_tests()
    assert len(active_tests) == 0
    
    print("✓ Active tests tracking test passed")


def test_error_handling():
    """Test error handling in testing system."""
    # Create template manager
    manager = PromptTemplateManager()
    tester = PromptTester(manager)
    
    # Try to run test on non-existent template
    result = tester.run_validation_test(
        prompt_name="non-existent-prompt",
        version="1.0.0",
        test_cases=[{"variables": {"question": "Test"}}]
    )
    
    # Should have failed
    assert result.status == TestStatus.FAILED
    assert "error" in result.metadata
    
    print("✓ Error handling test passed")


if __name__ == "__main__":
    # Run all tests
    test_ab_testing()
    test_validation_testing()
    test_version_comparison()
    test_test_result_filtering()
    test_active_tests()
    test_error_handling()
    print("\n🎉 All prompt testing tests passed!")
