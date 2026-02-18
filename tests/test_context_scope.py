"""Tests for scoped context management using __vars__ key."""

from pathlib import Path

import pytest

from yaml_config_tags import config_load


def test_context_scope_basic():
    """Test basic scoped context with __vars__."""
    config_file = Path(__file__).parent / "data" / "context_scope.yaml"

    # Load with base context
    data = config_load(config_file, context={"vars": {"base_url": "https://default.com"}})

    # Global value uses original context
    assert data["global_value"] == "https://default.com"

    # Scoped values use the __vars__ overrides
    assert data["api_config"]["endpoint"] == "https://api.example.com/v2/users"
    assert data["api_config"]["auth_url"] == "https://api.example.com/v2/auth"

    # Nested scope with additional variables
    assert data["api_config"]["settings"]["connection_timeout"] == "30"
    # Nested scope inherits parent scope
    assert data["api_config"]["settings"]["read_url"] == "https://api.example.com/v2/read"

    # After exiting scope, original context is restored
    assert data["fallback_url"] == "https://default.com"

    # __vars__ key should not appear in final output
    assert "__vars__" not in data["api_config"]
    assert "__vars__" not in data["api_config"]["settings"]


def test_disable_context_scope():
    """Test that setting jinja_vars_key to empty string disables interception."""
    config_file = Path(__file__).parent / "data" / "context_scope.yaml"

    # Load with empty jinja_vars_key to disable
    data = config_load(
        config_file,
        context={"vars": {"base_url": "https://default.com"}},
        jinja_vars_key="",
    )

    # __vars__ should appear as regular key when disabled
    assert "__vars__" in data["api_config"]
    assert data["api_config"]["__vars__"]["base_url"] == "https://api.example.com"


def test_context_scope_error_non_dict():
    """Test that __vars__ with non-dict value raises error."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
test_obj:
  __vars__: "this is not a dict"
  key: value
""")
        temp_path = f.name

    try:
        with pytest.raises(Exception) as exc_info:
            config_load(temp_path, context={})

        assert "__vars__ must be a dict" in str(exc_info.value)
    finally:
        Path(temp_path).unlink()


def test_custom_vars_key():
    """Test using a custom key name instead of __vars__."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
api:
  _context:
    version: "v3"
  url: !jinja "{{ vars.base }}/{{ vars.version }}"
""")
        temp_path = f.name

    try:
        data = config_load(
            temp_path,
            context={"vars": {"base": "https://example.com"}},
            jinja_vars_key="_context",
        )

        assert data["api"]["url"] == "https://example.com/v3"
        assert "_context" not in data["api"]
    finally:
        Path(temp_path).unlink()
