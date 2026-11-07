"""This module contains tests for the yaml-config library."""

from pathlib import Path
from typing import Never

from yaml_config_tags import TagDefinition, config_get_env


def test_get_env_custom_tags(tmp_path: Path) -> None:
    """ENV scanning decodes custom collections without executing constructors."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "!mapping\nitems: !sequence [!env CUSTOM_ONE, !env [CUSTOM_TWO, 0]]\nplain: !scalar 42\n",
        encoding="utf-8",
    )

    def forbidden(value: object) -> Never:
        raise AssertionError(f"constructor executed with {value!r}")

    tags = [
        TagDefinition("!mapping", forbidden, kind="mapping"),
        TagDefinition("!sequence", forbidden, kind="sequence"),
        TagDefinition("!scalar", forbidden),
    ]

    assert config_get_env(path, tags=tags) == {"CUSTOM_ONE", "CUSTOM_TWO"}


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
