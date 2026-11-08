"""This module contains tests for the yaml-config library."""

from pathlib import Path
from typing import Never

from pytest import raises
from yaml.constructor import ConstructorError

from yaml_config_tags import TagDefinition, config_get_env, config_get_tag_refs


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


def test_get_tag_refs(tmp_path: Path) -> None:
    """Custom tags report raw text by default, without executing constructors."""
    path = tmp_path / "refs.yaml"
    path.write_text(
        "a: !secret db/password\n"
        "b: !secrets [api/key, 123, !env NESTED, [x]]\n"
        "c: !secret yes\n"
        "d: !record {name: pkg, version: 1}\n"
        "e: !custom ignored\n"
        "f: !env ENV_ONE\n",
        encoding="utf-8",
    )

    def forbidden(value: object) -> Never:
        raise AssertionError(f"constructor executed with {value!r}")

    tags = [
        TagDefinition("!secret", forbidden),
        TagDefinition("!secrets", forbidden, kind="sequence"),
        TagDefinition("!record", forbidden, kind="mapping", extract=lambda v: [v["name"]]),
        TagDefinition("!custom", forbidden, extract=forbidden),
    ]

    assert config_get_tag_refs(path, tags=tags, select=["!secret", "!secrets", "!record"]) == {
        "!secret": {"db/password", "yes"},
        "!secrets": {"api/key", "123"},
        "!record": {"pkg"},
    }
    assert config_get_env(path, tags=tags) == {"ENV_ONE", "NESTED"}


def test_get_tag_refs_includes(tmp_path: Path) -> None:
    """Includes are scanned recursively only when requested, and cycles stop."""
    (tmp_path / "main.yaml").write_text(
        "a: !secret one\nb: !include sub/*.yaml\ne: !env MAIN\n", encoding="utf-8"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "child.yaml").write_text(
        "c: !secret two\nd: !include ../main.yaml\nf: !env CHILD\n", encoding="utf-8"
    )
    tags = [TagDefinition("!secret", str)]

    path = tmp_path / "main.yaml"
    assert config_get_tag_refs(path, tags=tags) == {"!secret": {"one"}}
    assert config_get_tag_refs(path, tags=tags, follow_includes=True) == {"!secret": {"one", "two"}}
    assert config_get_env(path, tags=tags) == {"MAIN"}
    assert config_get_env(path, tags=tags, follow_includes=True) == {"MAIN", "CHILD"}


def test_get_tag_refs_errors(tmp_path: Path) -> None:
    """Extract failures, bare string results and unknown selections are reported."""
    path = tmp_path / "refs.yaml"
    path.write_text("a: !secret db/password\n", encoding="utf-8")

    with raises(ConstructorError, match="failed to extract !secret"):
        config_get_tag_refs(path, tags=[TagDefinition("!secret", str, extract=lambda v: v)])
    with raises(ValueError, match="undefined tags"):
        config_get_tag_refs(path, tags=[TagDefinition("!secret", str)], select=["!other"])
    with raises(TypeError, match="extract"):
        TagDefinition("!secret", str, extract="nope")  # type: ignore[arg-type]


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
