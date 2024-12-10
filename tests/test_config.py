"""This module contains tests for the yaml-config library."""

import os

import yaml
from pytest import raises

from yaml_config_tags import config_load


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
