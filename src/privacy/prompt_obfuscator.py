"""
System prompt obfuscator for 4S1T Agent AI.

Breaks static system-prompt fingerprinting by:
  1. Accepting a list of semantic variants per persona and selecting one at random.
  2. Appending a randomly chosen benign style instruction.
  3. Adding 0-2 random blank lines at the start to vary token offsets.

None of these changes affect agent behaviour — they only vary surface form
so that identical-agent calls do not produce identical prompts at the provider.
"""
from __future__ import annotations

import random
from typing import Sequence

# ---------------------------------------------------------------------------
# Generic style instruction pool
# These are appended as a final sentence to the chosen variant.
# All are neutral and do not change agent behaviour.
# ---------------------------------------------------------------------------
_STYLE_POOL: tuple[str, ...] = (
    "Be direct.",
    "Keep responses focused.",
    "Use plain language.",
    "Be concise and precise.",
    "Stay on topic.",
    "Structure your response clearly.",
    "Prioritise accuracy.",
    "Avoid unnecessary elaboration.",
    "Use specific details.",
    "Keep your answer practical.",
)


class PromptObfuscator:
    """
    Randomises a system prompt each time it is called.

    Usage::

        obfuscator = PromptObfuscator()
        system_prompt = obfuscator.randomize(persona.system_prompt_variants)
    """

    def randomize(self, variants: Sequence[str]) -> str:
        """
        Select a random variant and apply surface-level randomisation.

        Args:
            variants: List of semantically equivalent system prompt texts.
                      At least one entry required; if empty returns empty string.

        Returns:
            Randomised system prompt string.
        """
        if not variants:
            return ""

        base = random.choice(variants)

        # Add 0-2 leading blank lines for token-offset variation
        leading = "\n" * random.randint(0, 2)

        # Append one random style instruction
        style = random.choice(_STYLE_POOL)

        return f"{leading}{base}\n\n{style}"
