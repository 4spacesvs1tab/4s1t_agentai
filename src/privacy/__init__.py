"""
Privacy module for 4S1T Agent AI.

Components:
  pii_patterns    — PL/EU regex patterns with checksum validators
  pii_scrubber    — PIIScrubber: detect(), scrub(), restore()
  pii_session_state — per-workflow PII approval state
  prompt_obfuscator — PromptObfuscator: randomize system prompts
"""
