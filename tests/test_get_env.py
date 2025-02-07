"""This module contains tests for the yaml-config library."""

from yaml_config_tags import config_get_env


def test_get_env() -> None:
    """Test extracting env vars."""
    result = config_get_env("tests/data/env.yaml")

    expect = {"ENV_VAR0", "ENV_VAR1", "ENV_VAR2"}
    assert all(item in result for item in expect)
    assert all(item in expect for item in result)


def test_jinja_not_rendered() -> None:
    """Test that extracts does not render jinja."""
    result = config_get_env("tests/data/obj.yaml")

    assert len(result) == 0
