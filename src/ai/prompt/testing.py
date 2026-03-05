"""
Prompt testing and versioning system for the 4S1T Agent AI framework.

This module provides functionality for A/B testing prompts, version comparison,
and automated testing of prompt effectiveness.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Callable, Union
from datetime import datetime
import logging
import random
import statistics
from enum import Enum

from .template import PromptTemplate, PromptTemplateManager
from .optimizer import PromptOptimizer, PromptPerformanceMetrics

logger = logging.getLogger(__name__)


class TestStatus(Enum):
    """Enumeration of test statuses."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TestType(Enum):
    """Enumeration of test types."""
    A_B_TEST = "a_b_test"
    VERSION_COMPARISON = "version_comparison"
    VALIDATION = "validation"
    REGRESSION = "regression"


@dataclass
class TestResult:
    """Result of a prompt test."""
    
    test_id: str
    test_type: TestType
    prompt_name: str
    versions_tested: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)
    winner: Optional[str] = None
    confidence_level: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: TestStatus = TestStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABTestConfig:
    """Configuration for A/B testing prompts."""
    
    prompt_name: str
    version_a: str
    version_b: str
    test_cases: List[Dict[str, Any]]
    metric_weights: Dict[str, float] = field(default_factory=lambda: {
        'accuracy': 0.4,
        'relevance': 0.3,
        'helpfulness': 0.2,
        'speed': 0.1
    })
    sample_size: int = 100
    confidence_threshold: float = 0.95
    metadata: Dict[str, Any] = field(default_factory=dict)


class PromptTester:
    """
    Testing system for prompt templates in the 4S1T Agent AI framework.
    
    This class provides functionality for testing prompt effectiveness,
    comparing versions, and validating prompt quality.
    """
    
    def __init__(self, template_manager: PromptTemplateManager, 
                 optimizer: Optional[PromptOptimizer] = None):
        """
        Initialize the prompt tester.
        
        Args:
            template_manager: The template manager to work with
            optimizer: Optional optimizer for recording test results
        """
        self.template_manager = template_manager
        self.optimizer = optimizer
        self.test_results: List[TestResult] = []
        self.active_tests: Dict[str, TestResult] = {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def run_ab_test(self, config: ABTestConfig) -> TestResult:
        """
        Run an A/B test between two prompt versions.
        
        Args:
            config: A/B test configuration
            
        Returns:
            TestResult: Results of the A/B test
        """
        test_id = f"ab_test_{config.prompt_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create test result
        test_result = TestResult(
            test_id=test_id,
            test_type=TestType.A_B_TEST,
            prompt_name=config.prompt_name,
            versions_tested=[config.version_a, config.version_b],
            status=TestStatus.RUNNING
        )
        
        self.active_tests[test_id] = test_result
        self.logger.info(f"Starting A/B test {test_id} for {config.prompt_name}")
        
        try:
            # Get templates
            template_a = self.template_manager.get_template(config.prompt_name, config.version_a)
            template_b = self.template_manager.get_template(config.prompt_name, config.version_b)
            
            if not template_a or not template_b:
                raise ValueError(f"One or both templates not found for {config.prompt_name}")
            
            # Run test cases
            results_a = []
            results_b = []
            
            # Sample test cases if we have more than sample_size
            test_cases = config.test_cases
            if len(test_cases) > config.sample_size:
                test_cases = random.sample(test_cases, config.sample_size)
            
            for i, test_case in enumerate(test_cases):
                variables = test_case.get("variables", {})
                expected_output = test_case.get("expected_output", "")
                
                # Test version A
                try:
                    rendered_a = template_a.render(variables)
                    score_a = self._evaluate_response(rendered_a, expected_output, test_case)
                    results_a.append(score_a)
                except Exception as e:
                    self.logger.warning(f"Error testing version A for case {i}: {e}")
                    results_a.append(0.0)
                
                # Test version B
                try:
                    rendered_b = template_b.render(variables)
                    score_b = self._evaluate_response(rendered_b, expected_output, test_case)
                    results_b.append(score_b)
                except Exception as e:
                    self.logger.warning(f"Error testing version B for case {i}: {e}")
                    results_b.append(0.0)
            
            # Calculate statistics
            if results_a and results_b:
                avg_a = statistics.mean(results_a)
                avg_b = statistics.mean(results_b)
                std_a = statistics.stdev(results_a) if len(results_a) > 1 else 0.0
                std_b = statistics.stdev(results_b) if len(results_b) > 1 else 0.0
                
                # Determine winner
                if avg_a > avg_b:
                    winner = config.version_a
                    improvement = ((avg_a - avg_b) / avg_b * 100) if avg_b > 0 else 0
                else:
                    winner = config.version_b
                    improvement = ((avg_b - avg_a) / avg_a * 100) if avg_a > 0 else 0
                
                # Calculate confidence (simplified)
                pooled_std = ((std_a**2 + std_b**2) / 2)**0.5 if std_a > 0 or std_b > 0 else 0
                confidence = min(1.0, abs(avg_a - avg_b) / (pooled_std + 0.001)) if pooled_std > 0 else 0.95
                
                # Update test result
                test_result.metrics = {
                    "version_a_average": avg_a,
                    "version_b_average": avg_b,
                    "version_a_std": std_a,
                    "version_b_std": std_b,
                    "improvement_percentage": improvement,
                    "statistical_significance": confidence >= config.confidence_threshold
                }
                test_result.winner = winner
                test_result.confidence_level = confidence
            else:
                test_result.status = TestStatus.FAILED
                test_result.metadata["error"] = "No valid test results collected"
            
            test_result.status = TestStatus.COMPLETED
            test_result.completed_at = datetime.now()
            
            # Record in optimizer if available
            if self.optimizer:
                # Record synthetic performance data for both versions
                if results_a:
                    avg_score_a = statistics.mean(results_a)
                    self.optimizer.record_execution(
                        prompt_name=config.prompt_name,
                        version=config.version_a,
                        success=avg_score_a > 0.5,
                        latency_ms=random.uniform(200, 500),  # Simulated
                        relevance_score=avg_score_a,
                        helpfulness_score=avg_score_a,
                        user_feedback=avg_score_a
                    )
                
                if results_b:
                    avg_score_b = statistics.mean(results_b)
                    self.optimizer.record_execution(
                        prompt_name=config.prompt_name,
                        version=config.version_b,
                        success=avg_score_b > 0.5,
                        latency_ms=random.uniform(200, 500),  # Simulated
                        relevance_score=avg_score_b,
                        helpfulness_score=avg_score_b,
                        user_feedback=avg_score_b
                    )
            
            self.logger.info(f"A/B test {test_id} completed. Winner: {winner}")
            
        except Exception as e:
            self.logger.error(f"A/B test {test_id} failed: {e}")
            test_result.status = TestStatus.FAILED
            test_result.metadata["error"] = str(e)
            test_result.completed_at = datetime.now()
        
        # Move from active to completed
        if test_id in self.active_tests:
            del self.active_tests[test_id]
        self.test_results.append(test_result)
        
        return test_result
    
    def _evaluate_response(self, response: str, expected: str, test_case: Dict[str, Any]) -> float:
        """
        Evaluate a prompt response against expected output.
        
        Args:
            response: Generated response
            expected: Expected output
            test_case: Full test case data
            
        Returns:
            float: Evaluation score (0.0 to 1.0)
        """
        # This is a simplified evaluation - in practice, you might use:
        # - Semantic similarity models
        # - Keyword matching
        # - Custom evaluation functions
        # - Human evaluation integration
        
        if not expected:
            # If no expected output, evaluate based on test case criteria
            criteria = test_case.get("evaluation_criteria", {})
            score = 0.0
            
            # Length criterion
            if "max_length" in criteria:
                max_len = criteria["max_length"]
                if len(response) <= max_len:
                    score += 0.3
            
            # Keyword presence
            if "required_keywords" in criteria:
                keywords = criteria["required_keywords"]
                found_keywords = sum(1 for kw in keywords if kw.lower() in response.lower())
                score += 0.4 * (found_keywords / len(keywords)) if keywords else 0
            
            # Structure criterion
            if "structured_response" in criteria and criteria["structured_response"]:
                # Check for structured elements like bullet points, numbered lists
                if any(marker in response for marker in ['•', '-', '*', '1.', '2.', '3.']):
                    score += 0.3
            
            return min(1.0, score)
        else:
            # Simple string similarity (Jaccard similarity)
            response_words = set(response.lower().split())
            expected_words = set(expected.lower().split())
            
            if not response_words and not expected_words:
                return 1.0
            if not response_words or not expected_words:
                return 0.0
                
            intersection = response_words.intersection(expected_words)
            union = response_words.union(expected_words)
            
            return len(intersection) / len(union) if union else 0.0
    
    def run_validation_test(self, prompt_name: str, version: str,
                          test_cases: List[Dict[str, Any]]) -> TestResult:
        """
        Run a validation test on a prompt version.
        
        Args:
            prompt_name: Name of the prompt template
            version: Version to test
            test_cases: List of test cases
            
        Returns:
            TestResult: Results of the validation test
        """
        test_id = f"validation_{prompt_name}_{version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create test result
        test_result = TestResult(
            test_id=test_id,
            test_type=TestType.VALIDATION,
            prompt_name=prompt_name,
            versions_tested=[version],
            status=TestStatus.RUNNING
        )
        
        self.active_tests[test_id] = test_result
        self.logger.info(f"Starting validation test {test_id} for {prompt_name} v{version}")
        
        try:
            # Get template
            template = self.template_manager.get_template(prompt_name, version)
            if not template:
                raise ValueError(f"Template {prompt_name} v{version} not found")
            
            # Run validation
            passed_tests = 0
            total_tests = len(test_cases)
            
            for i, test_case in enumerate(test_cases):
                variables = test_case.get("variables", {})
                expected_output = test_case.get("expected_output", "")
                
                try:
                    # Test rendering
                    rendered = template.render(variables)
                    
                    # Validate output
                    is_valid = self._validate_output(rendered, test_case)
                    if is_valid:
                        passed_tests += 1
                        
                except Exception as e:
                    self.logger.warning(f"Validation test case {i} failed: {e}")
            
            # Calculate pass rate
            pass_rate = passed_tests / total_tests if total_tests > 0 else 0.0
            
            test_result.metrics = {
                "passed_tests": passed_tests,
                "total_tests": total_tests,
                "pass_rate": pass_rate,
                "validation_passed": pass_rate >= 0.8  # 80% threshold
            }
            
            test_result.status = TestStatus.COMPLETED
            test_result.completed_at = datetime.now()
            
            self.logger.info(f"Validation test {test_id} completed. Pass rate: {pass_rate:.2f}")
            
        except Exception as e:
            self.logger.error(f"Validation test {test_id} failed: {e}")
            test_result.status = TestStatus.FAILED
            test_result.metadata["error"] = str(e)
            test_result.completed_at = datetime.now()
        
        # Move from active to completed
        if test_id in self.active_tests:
            del self.active_tests[test_id]
        self.test_results.append(test_result)
        
        return test_result
    
    def _validate_output(self, output: str, test_case: Dict[str, Any]) -> bool:
        """
        Validate prompt output against test case criteria.
        
        Args:
            output: Generated output
            test_case: Test case with validation criteria
            
        Returns:
            bool: True if output is valid, False otherwise
        """
        criteria = test_case.get("validation_criteria", {})
        
        # Length validation
        if "max_length" in criteria:
            if len(output) > criteria["max_length"]:
                return False
        
        if "min_length" in criteria:
            if len(output) < criteria["min_length"]:
                return False
        
        # Keyword validation
        if "required_keywords" in criteria:
            keywords = criteria["required_keywords"]
            if not all(kw.lower() in output.lower() for kw in keywords):
                return False
        
        if "forbidden_keywords" in criteria:
            keywords = criteria["forbidden_keywords"]
            if any(kw.lower() in output.lower() for kw in keywords):
                return False
        
        # Format validation
        if "must_contain_numbers" in criteria and criteria["must_contain_numbers"]:
            if not any(char.isdigit() for char in output):
                return False
        
        if "must_be_structured" in criteria and criteria["must_be_structured"]:
            # Check for structured elements
            structured_markers = ['•', '-', '*', '1.', '2.', '3.', ':', '::']
            if not any(marker in output for marker in structured_markers):
                return False
        
        return True
    
    def compare_versions(self, prompt_name: str, 
                        versions: List[str]) -> TestResult:
        """
        Compare multiple versions of a prompt.
        
        Args:
            prompt_name: Name of the prompt template
            versions: List of versions to compare
            
        Returns:
            TestResult: Comparison results
        """
        test_id = f"comparison_{prompt_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create test result
        test_result = TestResult(
            test_id=test_id,
            test_type=TestType.VERSION_COMPARISON,
            prompt_name=prompt_name,
            versions_tested=versions,
            status=TestStatus.RUNNING
        )
        
        self.active_tests[test_id] = test_result
        self.logger.info(f"Starting version comparison {test_id} for {prompt_name}")
        
        try:
            # Get templates
            templates = []
            for version in versions:
                template = self.template_manager.get_template(prompt_name, version)
                if template:
                    templates.append((version, template))
            
            if len(templates) < 2:
                raise ValueError("Need at least 2 valid templates for comparison")
            
            # Compare template complexity
            complexity_scores = {}
            for version, template in templates:
                # Simple complexity measure: length + variable count
                complexity = len(template.template) + len(template.required_variables)
                complexity_scores[version] = complexity
            
            # Find simplest and most complex
            sorted_versions = sorted(complexity_scores.items(), key=lambda x: x[1])
            simplest = sorted_versions[0][0]
            most_complex = sorted_versions[-1][0]
            
            test_result.metrics = {
                "complexity_scores": complexity_scores,
                "simplest_version": simplest,
                "most_complex_version": most_complex,
                "complexity_range": sorted_versions[-1][1] - sorted_versions[0][1]
            }
            
            # Determine winner (simplest that still works)
            test_result.winner = simplest
            test_result.confidence_level = 0.8  # Medium confidence
            
            test_result.status = TestStatus.COMPLETED
            test_result.completed_at = datetime.now()
            
            self.logger.info(f"Version comparison {test_id} completed.")
            
        except Exception as e:
            self.logger.error(f"Version comparison {test_id} failed: {e}")
            test_result.status = TestStatus.FAILED
            test_result.metadata["error"] = str(e)
            test_result.completed_at = datetime.now()
        
        # Move from active to completed
        if test_id in self.active_tests:
            del self.active_tests[test_id]
        self.test_results.append(test_result)
        
        return test_result
    
    def get_test_results(self, prompt_name: Optional[str] = None,
                        test_type: Optional[TestType] = None) -> List[TestResult]:
        """
        Get test results, optionally filtered by prompt name and test type.
        
        Args:
            prompt_name: Filter by prompt name
            test_type: Filter by test type
            
        Returns:
            List[TestResult]: List of test results
        """
        results = self.test_results.copy()
        
        if prompt_name:
            results = [r for r in results if r.prompt_name == prompt_name]
        
        if test_type:
            results = [r for r in results if r.test_type == test_type]
        
        return results
    
    def get_active_tests(self) -> List[TestResult]:
        """
        Get currently running tests.
        
        Returns:
            List[TestResult]: List of active tests
        """
        return list(self.active_tests.values())


# Example usage and testing functions
def create_sample_tester() -> Tuple[PromptTester, PromptTemplateManager]:
    """Create a sample tester with test data for demonstration."""
    # Create template manager and register sample templates
    manager = PromptTemplateManager()
    
    template_v1 = PromptTemplate(
        name="sample-question",
        template="Question: {question}\nAnswer:",
        version="1.0.0",
        description="Simple Q&A template"
    )
    
    template_v2 = PromptTemplate(
        name="sample-question",
        template="Please answer the following question clearly and concisely.\n\nQuestion: {question}\n\nAnswer:",
        version="2.0.0",
        description="Enhanced Q&A template with instructions"
    )
    
    manager.register_template(template_v1)
    manager.register_template(template_v2)
    
    # Create tester
    tester = PromptTester(manager)
    
    return tester, manager


if __name__ == "__main__":
    # Demonstrate tester functionality
    tester, manager = create_sample_tester()
    
    # Create A/B test config
    test_config = ABTestConfig(
        prompt_name="sample-question",
        version_a="1.0.0",
        version_b="2.0.0",
        test_cases=[
            {
                "variables": {"question": "What is AI?"},
                "expected_output": "Artificial Intelligence",
                "evaluation_criteria": {
                    "required_keywords": ["intelligence", "artificial"],
                    "max_length": 200
                }
            },
            {
                "variables": {"question": "What is machine learning?"},
                "expected_output": "Machine learning is a subset of AI",
                "evaluation_criteria": {
                    "required_keywords": ["machine", "learning", "AI"],
                    "structured_response": True
                }
            }
        ],
        sample_size=2
    )
    
    # Run A/B test
    print("Running A/B test...")
    ab_result = tester.run_ab_test(test_config)
    print(f"A/B Test Result:")
    print(f"  Winner: {ab_result.winner}")
    print(f"  Confidence: {ab_result.confidence_level:.2f}")
    print(f"  Metrics: {ab_result.metrics}")
    
    # Run validation test
    print("\nRunning validation test...")
    validation_result = tester.run_validation_test(
        prompt_name="sample-question",
        version="2.0.0",
        test_cases=[
            {
                "variables": {"question": "Test question"},
                "validation_criteria": {
                    "min_length": 10,
                    "max_length": 500
                }
            }
        ]
    )
    print(f"Validation Result:")
    print(f"  Passed: {validation_result.metrics.get('validation_passed', False)}")
    print(f"  Pass Rate: {validation_result.metrics.get('pass_rate', 0):.2f}")
    
    # Compare versions
    print("\nRunning version comparison...")
    comparison_result = tester.compare_versions("sample-question", ["1.0.0", "2.0.0"])
    print(f"Comparison Result:")
    print(f"  Simplest: {comparison_result.metrics.get('simplest_version')}")
    print(f"  Most Complex: {comparison_result.metrics.get('most_complex_version')}")
