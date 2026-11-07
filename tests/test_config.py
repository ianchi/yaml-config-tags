"""This module contains tests for the yaml-config library."""

import datetime as dt
import os
from collections.abc import Generator
from functools import partial
from pathlib import Path
from typing import Any, Literal

import yaml
from pytest import mark, raises

from yaml_config_tags import ConfigLoader, TagDefinition, config_load


def test_custom_scalar_default(tmp_path: Path) -> None:
    """Custom scalar tags receive implicitly typed values by default."""
    path = tmp_path / "custom.yaml"
    path.write_text("value: !double 21\n", encoding="utf-8")

    result = config_load(
        path, tags=[TagDefinition(tag="!double", constructor=lambda value: value * 2)]
    )

    assert result == {"value": 42}


@mark.parametrize(
    ("source", "expected"),
    [
        ("42", 42),
        ('"42"', "42"),
        ("'yes'", "yes"),
        ("yes", True),
        ("3.5", 3.5),
        ("null", None),
        ("", None),
        ("2026-11-07", dt.date(2026, 11, 7)),
        ("|\n  42\n", "42\n"),
        (">\n  hello\n  world\n", "hello world\n"),
        ("<<", "<<"),
        ("=", "="),
    ],
)
def test_custom_scalar_inference(tmp_path: Path, source: str, expected: object) -> None:
    """Scalar payloads follow implicit YAML typing and preserve quoted strings."""
    path = tmp_path / "custom.yaml"
    path.write_text(f"!value {source}\n", encoding="utf-8")

    result = config_load(path, tags=[TagDefinition("!value", lambda value: value)])

    assert result == expected
    assert type(result) is type(expected)


def test_custom_collections(tmp_path: Path) -> None:
    """Collection converters receive fully constructed builtin and custom children."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "!mapping\n"
        "items: !sequence [!double 3, !env [CUSTOM_TAG_TEST_ENV, 7]]\n"
        'greeting: !jinja "{{ name }}"\n',
        encoding="utf-8",
    )
    tags = [
        TagDefinition("!mapping", dict, kind="mapping"),
        TagDefinition("!sequence", tuple, kind="sequence"),
        TagDefinition("!double", lambda value: value * 2),
    ]

    result = config_load(path, context={"name": "hello"}, tags=tags, constructor=dict)

    assert result == {"items": (6, 7), "greeting": "hello"}


@mark.parametrize(
    ("kind", "source", "actual"),
    [
        ("scalar", "[1, 2]", "sequence"),
        ("scalar", "{key: value}", "mapping"),
        ("sequence", "42", "scalar"),
        ("mapping", "[1, 2]", "sequence"),
    ],
)
def test_custom_kind_error(
    tmp_path: Path, kind: Literal["scalar", "sequence", "mapping"], source: str, actual: str
) -> None:
    """Shape mismatches report the tag and its source location."""
    path = tmp_path / "custom.yaml"
    path.write_text(f"value: !custom {source}\n", encoding="utf-8")

    with raises(yaml.constructor.ConstructorError, match=f"found {actual}") as error:
        config_load(path, tags=[TagDefinition("!custom", lambda value: value, kind=kind)])

    assert error.value.problem_mark is not None
    assert error.value.problem_mark.name == str(path)
    assert error.value.problem_mark.line == 0
    assert "!custom expects" in str(error.value)


def test_custom_constructor_error(tmp_path: Path) -> None:
    """Constructor errors keep their original cause and YAML source location."""
    path = tmp_path / "custom.yaml"
    path.write_text("value: !integer invalid\n", encoding="utf-8")

    with raises(yaml.constructor.ConstructorError, match="failed to construct !integer") as error:
        config_load(path, tags=[TagDefinition("!integer", int)])

    assert isinstance(error.value.__cause__, ValueError)
    assert error.value.problem_mark is not None
    assert error.value.problem_mark.name == str(path)


@mark.parametrize("tag", ["custom", "!", "!!str", "!bad name", "!bad,tag"])
def test_custom_invalid_tag(tag: str) -> None:
    """Tag definitions require literal local YAML tag names."""
    with raises(ValueError, match="local YAML tag"):
        TagDefinition(tag, str)


def test_custom_invalid_definition() -> None:
    """Invalid kinds and non-callable constructors fail at definition time."""
    invalid: Any = "invalid"
    with raises(ValueError, match="kind must"):
        TagDefinition("!custom", str, kind=invalid)
    with raises(TypeError, match="constructor must"):
        TagDefinition("!custom", invalid)


@mark.parametrize("tag", ["!env", "!jinja", "!include", "!env:str", "!jinja:obj", "!include:yaml"])
def test_custom_reserved_tag(tmp_path: Path, tag: str) -> None:
    """Custom registrations cannot override builtin tags or their suffix families."""
    path = tmp_path / "custom.yaml"
    path.write_text("plain\n", encoding="utf-8")
    with raises(ValueError, match="reserved tag"):
        config_load(path, tags=[TagDefinition(tag, str)])


def test_custom_registration_isolation(tmp_path: Path) -> None:
    """Registrations neither leak across loads nor mutate the caller's definitions."""
    path = tmp_path / "custom.yaml"
    path.write_text("!custom 5\n", encoding="utf-8")
    tags = [TagDefinition("!custom", lambda value: value * 2)]

    assert config_load(path, tags=tags) == 10
    assert len(tags) == 1
    assert config_load(path, tags=(TagDefinition("!custom", str),)) == "5"
    with raises(yaml.constructor.ConstructorError):
        config_load(path)
    with raises(yaml.constructor.ConstructorError):
        config_load(path, tags=[])
    with raises(ValueError, match="duplicate"):
        config_load(path, tags=tags * 2)
    invalid: Any = ["!custom"]
    with raises(TypeError, match="TagDefinition"):
        config_load(path, tags=invalid)


def test_custom_direct_loader() -> None:
    """Direct loader instances keep separate constructor registries."""
    loader = ConfigLoader("!custom 5", ".", tags=[TagDefinition("!custom", str)])
    other = ConfigLoader("!custom 5", ".")
    try:
        assert loader.get_single_data() == "5"
        with raises(yaml.constructor.ConstructorError):
            other.get_single_data()
    finally:
        loader.dispose()
        other.dispose()


def test_custom_aliases(tmp_path: Path) -> None:
    """Prior anchors are complete and aliases share a once-constructed result."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "base: &base {nested: [1, 2]}\n"
        "converted: &converted !custom {base: *base}\n"
        "alias: *converted\n",
        encoding="utf-8",
    )
    seen = []

    def convert(value: dict) -> dict:
        seen.append(value["base"]["nested"].copy())
        return value

    result = config_load(path, tags=[TagDefinition("!custom", convert, kind="mapping")])

    assert seen == [[1, 2]]
    assert result["converted"] is result["alias"]
    assert result["converted"]["base"] is result["base"]


def test_custom_recursive_values(tmp_path: Path) -> None:
    """Recursive custom values fail while ordinary untagged recursion remains supported."""
    path = tmp_path / "custom.yaml"
    path.write_text("&recursive !custom [*recursive]\n", encoding="utf-8")
    with raises(yaml.constructor.ConstructorError, match="recursive"):
        config_load(path, tags=[TagDefinition("!custom", tuple, kind="sequence")])

    path.write_text("&recursive [*recursive]\n", encoding="utf-8")
    result = config_load(path)
    assert result[0] is result


def test_custom_mapping_merge(tmp_path: Path) -> None:
    """Custom mappings decode structural YAML merge keys before conversion."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "base: &base {number: 1}\nvalue: !custom {<<: *base, number: 2}\n",
        encoding="utf-8",
    )
    result = config_load(path, tags=[TagDefinition("!custom", dict, kind="mapping")])
    assert result["value"] == {"number": 2}


def test_custom_callable_forms(tmp_path: Path) -> None:
    """Partials and generator-returning constructors use the one-value contract."""
    path = tmp_path / "custom.yaml"
    path.write_text('!hex "ff"\n', encoding="utf-8")
    assert config_load(path, tags=[TagDefinition("!hex", partial(int, base=16))]) == 255

    def generate(value: object) -> Generator[object, None, None]:
        yield value

    path.write_text("!generator 42\n", encoding="utf-8")
    result = config_load(path, tags=[TagDefinition("!generator", generate)])
    assert list(result) == [42]


def test_custom_yaml_includes(tmp_path: Path) -> None:
    """Nested and glob YAML includes inherit custom definitions and relative paths."""
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "value.yaml").write_text("!double 3\n", encoding="utf-8")
    (nested / "child.yaml").write_text("!include value.yaml\n", encoding="utf-8")
    path = tmp_path / "custom.yaml"
    path.write_text(
        "nested: !include nested/child.yaml\nglob: !include nested/*.yaml\n",
        encoding="utf-8",
    )
    result = config_load(path, tags=[TagDefinition("!double", lambda value: value * 2)])
    assert result == {"nested": 6, "glob": [6, 6]}


def test_jinja_str() -> None:
    """Test loading a YAML file with Jinja string."""
    result = config_load("tests/data/jinja.yaml", context={"context": "value1"})

    assert result["hello"] == "world"
    assert result["context"] == "value1"


def test_jinja_obj() -> None:
    """Test loading a YAML file with Jinja object."""
    context = {"key1": "value1"}
    result = config_load("tests/data/obj.yaml", context={"context": context})

    assert len(result["list"]) == 2
    assert result["context"] == context


def test_jinja_filter() -> None:
    """Test loading a YAML file with Jinja filter."""
    context = "value1"
    result = config_load(
        "tests/data/jinja_filter.yaml",
        context={"context": context},
        jinja_filters={"custom_filter": lambda x: x.upper()},
    )

    assert result["hello"] == "WORLD"
    assert result["context"] == context.upper()


def test_obj_error() -> None:
    """Test loading a YAML file with malformed Jinja object."""
    with raises(yaml.constructor.ConstructorError):
        config_load("tests/data/obj_err.yaml")


def test_env() -> None:
    """Test loading a YAML file with environment variables."""
    # set environment
    os.environ["ENV_VAR1"] = "value1"
    os.environ["ENV_VAR2"] = "value2"
    if "ENV_VAR0" in os.environ:
        del os.environ["ENV_VAR0"]

    result = config_load("tests/data/env.yaml")

    assert result["simple"] == "value1"
    assert result["default"] == 10
    assert result["fallback"] == "value2"


def test_env_suffix() -> None:
    """Test loading a YAML file with environment variables."""
    # set environment

    os.environ["ENV_VAR_INT"] = "10"
    os.environ["ENV_VAR_FLOAT"] = "10.1"
    os.environ["ENV_VAR_STR"] = "2024-12-01"
    os.environ["ENV_VAR_TIMESTAMP"] = "2024-12-01"

    result = config_load("tests/data/env_suffix.yaml")

    assert result["int"] == 10
    assert isinstance(result["int"], int)
    assert result["float"] == 10.1
    assert isinstance(result["float"], float)
    assert result["str"] == "2024-12-01"
    assert isinstance(result["str"], str)
    assert result["timestamp"] == dt.date(2024, 12, 1)
    assert isinstance(result["timestamp"], dt.date)


def test_env_suffix_err() -> None:
    """Test loading a YAML file with environment variables."""
    # set environment

    os.environ["ENV_VAR_STR"] = "mystring"

    with raises(ValueError):
        config_load("tests/data/env_suffix_err.yaml")


def test_env_missing() -> None:
    """Test loading a YAML file with missing environment variables."""
    with raises(yaml.constructor.ConstructorError):
        config_load("tests/data/env_err.yaml")


def test_include_yaml() -> None:
    """Test loading a YAML file with include directive."""
    result = config_load("tests/data/include_yaml.yaml", context={"context": "value1"})

    assert result["plain"]["context"] == "value1"
    assert len(result["yaml"]["list"]) == 2


def test_include_txt() -> None:
    """Test loading a YAML file with include directive to a txt."""
    result = config_load("tests/data/include_txt.yaml")

    assert result["txt"] == "hello world\n"


def test_include_json():
    """Test loading a YAML file with include directive to a JSON."""
    result = config_load("tests/data/include_json.yaml")

    assert result["json"]["hello"] == "world"


def test_include_yfm() -> None:
    """Test loading a YAML file with include directive to a YAML front matter file."""
    result = config_load("tests/data/include_yfm.yaml")

    assert result["text"] == "This is the body content.\n"
    assert result["explicit_text"] == result["text"]
    assert result["front"]["title"] == "Hello World"
    assert result["front"]["author"] == "test"
    assert result["front"]["tags"] == ["one", "two"]
