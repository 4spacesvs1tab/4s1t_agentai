"""
R500-optimized prompt tests for agent orchestration.

Tests designed for constrained hardware (Lenovo R500, Core2Duo, 8GB RAM):
- Minimize parallel agents (2-3 max)
- Use smaller models where possible
- Keep task chains short (3-5 waves max)
- Enable context compression to avoid memory bloat
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestR500OptimizedPrompts(unittest.IsolatedAsyncioTestCase):
    """
    Test prompts optimized for R500 hardware constraints.
    
    These prompts are designed to:
    - Use minimal resources (smaller models, fewer parallel agents)
    - Complete quickly to avoid memory pressure
    - Test core orchestration functionality
    """

    def setUp(self):
        """Set up test fixtures (without actually running agents)."""
        self.prompts = {
            "simple": {
                "task": "Research Python asyncio history and create a one-paragraph summary.",
                "expected_agents": 1,  # Will likely use single-agent bypass
                "max_waves": 1,
            },
            "two_parallel": {
                "task": """Compare two Ollama models for Mac Mini M1 16GB:
1. deepseek-r1:8b - best for reasoning
2. qwen3-coder - best for coding

Analyze their RAM usage, context windows, and suitability for R500 with 8GB.

IMPORTANT: Use two parallel research_agent calls to analyze both models separately.
""",
                "expected_agents": 2,  # Two research agents in parallel
                "max_waves": 1,
            },
            "three_waves": {
                "task": """Perform a comprehensive analysis of AI agent frameworks:

Wave 1 (Parallel): 
  - research_agent: Find current frameworks (LangChain, LlamaIndex, AutoGen)
  - research_agent: Find their GitHub stats (stars, commits, contributors)

Wave 2:
  - synthesis_agent: Create comparative report

Wave 3:
  - data_agent: Create a comparison table in Python

IMPORTANT: Use concise responses to minimize memory usage.
""",
                "expected_agents": 4,  # 2 in wave 0, 1 in wave 1, 1 in wave 2
                "max_waves": 3,
            },
            "decomposition_failure": {
                "task": """Do something complex that's hard to decompose: 
"Make me a sandwich" with as many steps as possible.
""",
                "expected_fallback": True,  # Should fall back to single-agent bypass
            },
        }

    async def test_prompt_parsing(self):
        """Verify all test prompts are well-formed."""
        for name, prompt_data in self.prompts.items():
            self.assertIn("task", prompt_data, f"Prompt {name} missing 'task'")
            self.assertIsInstance(prompt_data["task"], str, f"Prompt {name} task must be string")
            self.assertGreater(len(prompt_data["task"]), 10, f"Prompt {name} task too short")

    async def test_r500_constraints(self):
        """Verify prompts respect R500 hardware constraints."""
        for name, prompt_data in self.prompts.items():
            # Check expected_agents is reasonable (R500 can handle 2-3)
            if "expected_agents" in prompt_data:
                agents = prompt_data["expected_agents"]
                self.assertLessEqual(agents, 4, f"Prompt {name} has too many agents ({agents}) for R500")
            
            # Check max_waves is reasonable
            if "max_waves" in prompt_data:
                waves = prompt_data["max_waves"]
                self.assertLessEqual(waves, 5, f"Prompt {name} has too many waves ({waves}) for R500")

    async def test_simple_prompt_structure(self):
        """Verify simple prompt is single-agent eligible."""
        simple = self.prompts["simple"]
        self.assertIn("Research", simple["task"])
        self.assertIn("one-paragraph", simple["task"])
        # Simple prompts should complete quickly with minimal tool calls

    async def test_parallel_prompt_structure(self):
        """Verify parallel prompt explicitly specifies parallel execution."""
        parallel = self.prompts["two_parallel"]
        self.assertIn("two", parallel["task"].lower())
        self.assertIn("Compare", parallel["task"])
        self.assertIn("parallel", parallel["task"].lower())

    async def test_multiline_prompt_structure(self):
        """Verify multi-line prompt has clear wave structure."""
        multiline = self.prompts["three_waves"]
        self.assertIn("Wave 1", multiline["task"])
        self.assertIn("Wave 2", multiline["task"])
        self.assertIn("Wave 3", multiline["task"])
        self.assertIn("research_agent", multiline["task"])
        self.assertIn("synthesis_agent", multiline["task"])
        self.assertIn("data_agent", multiline["task"])
        self.assertIn("concise", multiline["task"].lower())
        self.assertIn("minimize", multiline["task"].lower())


if __name__ == "__main__":
    unittest.main()
