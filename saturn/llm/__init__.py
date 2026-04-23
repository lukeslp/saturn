"""LLM insight pass — opt-in multi-provider narration of deterministic findings.

The deterministic stats pass runs without touching this package. Nothing in
saturn's default output depends on a model; these modules are only loaded
when `--llm` is passed on the CLI.
"""
