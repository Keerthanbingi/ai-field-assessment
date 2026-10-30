"""Repository setup smoke tests."""

from importlib import import_module

import pytest


@pytest.mark.parametrize("module_name", ["ai_field", "fastapi", "httpx", "mcp", "pydantic"])
def test_required_module_imports(module_name: str) -> None:
    """Required runtime modules are installed and importable."""
    assert import_module(module_name) is not None
