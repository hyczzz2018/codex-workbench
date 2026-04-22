from __future__ import annotations

import os

from app.services.adapters.base import CoderAdapter
from app.services.adapters.claude_code import ClaudeCodeAdapter
from app.services.adapters.codex import CodexAdapter
from app.services.adapters.mock import MockCoderAdapter


def get_coder_adapter() -> CoderAdapter:
    provider = os.getenv("CODER_PROVIDER", "mock").strip().lower()
    if provider == "codex":
        return CodexAdapter()
    if provider == "claude_code":
        return ClaudeCodeAdapter()
    return MockCoderAdapter()
