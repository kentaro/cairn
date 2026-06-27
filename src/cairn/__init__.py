"""cairn — run agent CLIs against a local MLX model on Apple Silicon.

A *cairn* is a stack of stones that marks a path. This tool is a small local
waypoint: it stands up an MLX-backed inference server that speaks both the
OpenAI and Anthropic wire protocols, then launches agent CLIs (Claude Code,
Codex, …) pointed at it.
"""

__version__ = "0.1.0"
