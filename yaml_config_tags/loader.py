"""Custom YAML loader with support for Jinja templates, environment variables, and file includes."""

import json
import os
import re
from collections.abc import Callable, Collection, Generator, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Literal, TextIO, TypeVar, overload

import yaml
from jinja2 import Environment
from jinja2.nativetypes import NativeEnvironment

T = TypeVar("T")


@dataclass(frozen=True)
class TagDefinition:
    """Define a local YAML tag whose constructor receives one decoded value.

    Scalars use YAML implicit type inference. ``kind`` validates the YAML node
    shape, not the inferred Python type or the constructor's return type.
    """

    tag: str
    constructor: Callable[[Any], Any]
    kind: Literal["scalar", "sequence", "mapping"] = "scalar"
    extract: Callable[[Any], Iterable[str]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tag, str) or not re.fullmatch(r"![^\s!\[\]{},]+", self.tag):
            raise ValueError("tag must be a !-prefixed local YAML tag, such as !mytag")
        if self.kind not in ("scalar", "sequence", "mapping"):
            raise ValueError("kind must be scalar, sequence, or mapping")
        if not callable(self.constructor):
            raise TypeError("constructor must be callable with one positional value")
        if self.extract is not None and not callable(self.extract):
            raise TypeError("extract must be callable with one positional value")


class ConfigLoader(yaml.SafeLoader):
    """Custom YAML loader to parse configuration files with support for Jinja, ENV, and Includes."""

    def __init__(  # noqa: PLR0913
        self,
        stream: str | bytes | TextIO | BinaryIO,
        base_path: str | Path,
        *,
        context: dict[str, Any] | None = None,
        jinja_settings: dict[str, Any] | None = None,
        jinja_filters: dict[str, Callable[..., Any]] | None = None,
        parse_only: bool = False,
        jinja_vars_key: str = "__vars__",
        tags: Sequence[TagDefinition] | None = None,
        extract_tags: Collection[str] | None = None,
        follow_includes: bool = False,
    ) -> None:
        self.tags = tuple(tags or ())
        self.yaml_constructors = self.yaml_constructors.copy()
        for definition in self.tags:
            if not isinstance(definition, TagDefinition):
                raise TypeError("tags must contain TagDefinition instances")
            if definition.tag in self.yaml_constructors or any(
                definition.tag.startswith(prefix)
                for prefix in self.yaml_multi_constructors
                if prefix is not None
            ):
                raise ValueError(f"duplicate or reserved tag: {definition.tag}")
            self.yaml_constructors[definition.tag] = partial(
                ConfigLoader.construct_custom_tag, definition=definition
            )
        super().__init__(stream)
        # Store configurations
        self.base_path = Path(base_path)
        self.jinja_context = context or {}
        self.jinja_settings = jinja_settings or {}
        self.jinja_filters = jinja_filters
        self.text_env = Environment(autoescape=True, **(self.jinja_settings))
        self.native_env = NativeEnvironment(**(self.jinja_settings))
        if jinja_filters:
            for name, func in jinja_filters.items():
                self.text_env.filters[name] = func
                self.native_env.filters[name] = func
        self.env_vars: set[str] = set()
        self.tag_refs: dict[str, set[str]] = {
            definition.tag: set()
            for definition in self.tags
            if extract_tags is None or definition.tag in extract_tags
        }
        self.follow_includes = follow_includes
        self.scanned_files: set[Path] = set()
        self.parse_only = parse_only
        self.jinja_vars_key = jinja_vars_key

    def construct_document(self, node: yaml.nodes.Node) -> Any:  # noqa: ANN401
        """Fully construct anchors before passing their values to custom tags."""
        saved_deep = self.deep_construct
        if self.tags:
            self.deep_construct = True
        try:
            return super().construct_document(node)
        finally:
            self.deep_construct = saved_deep

    def construct_custom_tag(
        self,
        node: yaml.nodes.ScalarNode | yaml.nodes.SequenceNode | yaml.nodes.MappingNode,
        *,
        definition: TagDefinition,
    ) -> Generator[Any, None, None]:
        """Validate the node shape, decode its payload, and run its constructor."""
        node_types = {
            "scalar": yaml.nodes.ScalarNode,
            "sequence": yaml.nodes.SequenceNode,
            "mapping": yaml.nodes.MappingNode,
        }
        if not isinstance(node, node_types[definition.kind]):
            actual = type(node).__name__.removesuffix("Node").lower()
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"{definition.tag} expects a {definition.kind} node, found {actual}",
                node.start_mark,
            )

        if isinstance(node, yaml.nodes.ScalarNode):
            tag = self.resolve(yaml.nodes.ScalarNode, node.value, (node.style is None, False))
            if tag in ("tag:yaml.org,2002:merge", "tag:yaml.org,2002:value"):
                tag = "tag:yaml.org,2002:str"
            scalar = yaml.nodes.ScalarNode(
                tag, node.value, node.start_mark, node.end_mark, style=node.style
            )
            value = self.construct_object(scalar, deep=True)
        elif isinstance(node, yaml.nodes.SequenceNode):
            value = self.construct_sequence(node, deep=True)
        else:
            self.flatten_mapping(node)
            value = self.construct_mapping(node, deep=True)

        if self.parse_only:
            if definition.tag in self.tag_refs:
                try:
                    refs = definition.extract(value) if definition.extract else self._raw_refs(node)
                    if isinstance(refs, str):
                        raise TypeError("must return an iterable of strings, not a string")
                    self.tag_refs[definition.tag].update(refs)
                except Exception as exc:
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"failed to extract {definition.tag}: {exc}",
                        node.start_mark,
                    ) from exc
            yield value
            return
        try:
            result = definition.constructor(value)
        except Exception as exc:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"failed to construct {definition.tag}: {exc}",
                node.start_mark,
            ) from exc
        yield result

    @staticmethod
    def _raw_refs(node: yaml.nodes.Node) -> list[str]:
        """Return the raw scalar text of a node, or of its untagged scalar items."""
        if isinstance(node, yaml.nodes.ScalarNode):
            return [node.value]
        if isinstance(node, yaml.nodes.SequenceNode):
            return [
                child.value
                for child in node.value
                if isinstance(child, yaml.nodes.ScalarNode)
                and child.tag.startswith("tag:yaml.org,2002:")
            ]
        return []

    def _load_include(self, file: Path) -> Any:  # noqa: ANN401
        """Load an included YAML file with this loader's settings, merging scan results."""
        if self.parse_only:
            key = file.resolve()
            if key in self.scanned_files:
                return None
            self.scanned_files.add(key)
        file = file.absolute()
        with open(file, encoding="utf-8") as stream:
            loader = ConfigLoader(
                stream,
                file.parent,
                context=self.jinja_context,
                jinja_settings=self.jinja_settings,
                jinja_filters=self.jinja_filters,
                parse_only=self.parse_only,
                jinja_vars_key=self.jinja_vars_key,
                tags=self.tags,
                extract_tags=self.tag_refs.keys(),
                follow_includes=self.follow_includes,
            )
            loader.scanned_files = self.scanned_files
            try:
                data = loader.get_single_data()
            finally:
                loader.dispose()
        self.env_vars.update(loader.env_vars)
        for tag, refs in loader.tag_refs.items():
            self.tag_refs[tag].update(refs)
        return data

    def construct_mapping(self, node: yaml.nodes.Node, deep: bool = False) -> dict:  # noqa: FBT001, FBT002
        """Override to intercept mappings with jinja_vars_key for context management."""
        if not isinstance(node, yaml.nodes.MappingNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.tag}",
                node.start_mark,
            )

        # Check if this mapping contains the special jinja_vars_key
        if self.jinja_vars_key:
            env_node = None
            other_pairs = []

            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key == self.jinja_vars_key:
                    env_node = value_node
                else:
                    other_pairs.append((key_node, value_node))

            # If we found the special key, handle context management
            if env_node is not None:
                # Parse the environment definition using jinja
                # Always use deep=True to fully construct the env dict
                env_value = self.construct_object(env_node, deep=True)

                # Ensure it's a dictionary
                if not isinstance(env_value, dict):
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"{self.jinja_vars_key} must be a dict, "
                        f"but found {type(env_value).__name__}",
                        env_node.start_mark,
                    )

                # Ensure 'vars' exists in context
                if "vars" not in self.jinja_context:
                    self.jinja_context["vars"] = {}

                # Save current vars dict
                saved_vars = self.jinja_context["vars"]

                # Create new vars by merging old vars with new values
                self.jinja_context["vars"] = {**saved_vars, **env_value}

                try:
                    # Process remaining keys in new context
                    # Build result dict manually to ensure proper context
                    result = {}
                    for key_node, value_node in other_pairs:
                        key = self.construct_object(key_node, deep=deep)
                        # Always use deep=True to ensure nested mappings are fully
                        # constructed while the scoped vars are active
                        value = self.construct_object(value_node, deep=True)
                        result[key] = value
                    return result
                finally:
                    # Restore original vars
                    self.jinja_context["vars"] = saved_vars

        # No special key found, use default behavior
        return super().construct_mapping(node, deep=deep)

    def construct_str_jinja(self, node: yaml.nodes.Node) -> str:
        """Parse `!jinja` and `!jinja:str` tag, rendering Jinja template as text."""
        if not isinstance(node, yaml.nodes.ScalarNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a scalar node, but found {node.tag}",
                node.start_mark,
            )
        txt = self.construct_scalar(node)
        template = self.text_env.from_string(txt)
        return txt if self.parse_only else template.render(**self.jinja_context)

    def construct_obj_jinja(self, node: yaml.nodes.Node) -> Any:  # noqa: ANN401
        """Parse `!jinja:obj` tag, rendering Jinja template to native Python objects."""
        if not isinstance(node, yaml.nodes.ScalarNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a scalar node, but found {node.tag}",
                node.start_mark,
            )
        txt = self.construct_scalar(node)
        template = self.native_env.from_string(txt)
        return txt if self.parse_only else template.render(**self.jinja_context)

    def construct_multi_jinja(self, suffix: str, node: yaml.nodes.Node) -> Any:  # noqa: ANN401
        """Parse `!jinja:<suffix>` tag, rendering Jinja template based on the suffix."""
        if suffix == "str":
            return self.construct_str_jinja(node)
        if suffix == "obj":
            return self.construct_obj_jinja(node)

        raise yaml.constructor.ConstructorError(
            None,
            None,
            f"expected 'obj' or 'str' suffix, but found {suffix}",
            node.start_mark,
        )

    # adapted from
    # https://github.com/waylan/pyyaml-env-tag/blob/master/yaml_env_tag.py
    def env_constructor(self, node: yaml.nodes.Node, suffix: str | None = None) -> Any:  # noqa: ANN401
        """Parse `!env` tag, fetching values from environment variables.

        Supports fallback variables and default values using list syntax:

        ```yaml
        - !env [VAR_NAME]
        - !env [VAR_NAME, FALLBACK_1_VAR_NAME, ...,  FALLBACK_n_VAR_NAME, DEFAULT_VALUE]
        ```

        DEFAULT_VALUE can be any YAML value, and will be resolved using YAML's implicit types.
        """
        default = None
        has_default = False
        if isinstance(node, yaml.nodes.ScalarNode):
            variables = [self.construct_scalar(node)]

        elif isinstance(node, yaml.nodes.SequenceNode):
            child_nodes = node.value
            if len(child_nodes) > 1:
                # default is resolved using YAML's (implicit) types.
                default = self.construct_object(child_nodes[-1])
                has_default = True
                child_nodes = child_nodes[:-1]
            # Env Vars are resolved as string values, ignoring (implicit) types.
            variables = [self.construct_scalar(child) for child in child_nodes]
        else:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a scalar or sequence node, but found {node.tag}",
                node.start_mark,
            )

        self.env_vars.update(variables)
        if self.parse_only:
            return f"{variables}"

        for var in variables:
            if var in os.environ:
                value = os.environ[var]
                if suffix:
                    # Resolve value to Python type using YAML's implicit resolvers
                    tag = f"tag:yaml.org,2002:{suffix}"
                else:
                    # Resolve value to Python type using YAML's implicit resolvers
                    tag = self.resolve(yaml.nodes.ScalarNode, value, (True, False))
                return self.construct_object(yaml.nodes.ScalarNode(tag, value))

        if not has_default:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"environment variable(s) {variables} not found and no default value provided",
                node.start_mark,
            )
        return default

    def construct_multi_env(self, suffix: str, node: yaml.nodes.Node) -> Any:  # noqa: ANN401
        """Parse `!env:<suffix>` tag, converting environment variable to fixed type."""
        valid = ["str", "int", "float", "bool", "timestamp"]
        if suffix not in valid:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected {valid}, but found {suffix}",
                node.start_mark,
            )

        return self.env_constructor(node, suffix)

    @staticmethod
    def _parse_yfm(file: Path) -> tuple[dict, str]:
        """Parse a file with YAML front matter, returning (frontmatter_dict, body_text).

        Front matter is a YAML block delimited by ``---`` lines at the start of the file.
        If no front matter is found, returns an empty dict and the full file content.
        """
        content = file.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                front_text = content[3:end].strip()
                body = content[end + 4 :].lstrip("\n")
                front = yaml.safe_load(front_text) or {}
                return front, body
        return {}, content

    def include_constructor(self, node: yaml.nodes.Node) -> Any:  # noqa: ANN401
        """Parse `!include` tag, including another YAML file."""
        return self.multi_include_constructor("yaml", node)

    def multi_include_constructor(self, suffix: str, node: yaml.nodes.Node) -> Any:  # noqa: ANN401, PLR0912
        """Parse `!include:<suffix>` tag, including another YAML file based on the suffix.

        Supported suffixes:
        - ``yaml`` - parse as YAML (default for plain ``!include``)
        - ``json`` - parse as JSON
        - ``txt`` - read as raw text
        - ``yfm`` - read front matter and return the body (shorthand for ``yfm:body``)
        - ``yfm:body`` - same as ``yfm``
        - ``yfm:head`` - return the parsed front-matter dict
        """
        if not isinstance(node, yaml.nodes.ScalarNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a scalar node, but found {node.tag}",
                node.start_mark,
            )

        valid = ["yaml", "json", "txt", "yfm", "yfm:body", "yfm:head"]
        if suffix not in valid:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected {valid}, but found {suffix}",
                node.start_mark,
            )
        path = self.construct_scalar(node)

        if not isinstance(path, str):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a string, but found {path}",
                node.start_mark,
            )

        if self.parse_only and not (self.follow_includes and suffix == "yaml"):
            return f"{path}"

        # check if it is a glob pattern
        is_glob = "*" in path or "?" in path or "[" in path
        # check if it is a relative path
        root = Path("/") if path.startswith("/") else self.base_path

        files = [root / path] if not is_glob else root.glob(path)

        results = []
        for file in files:
            if not file.exists():
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"file {file} does not exist",
                    node.start_mark,
                )

            if suffix == "yaml":
                results.append(self._load_include(file))

            if suffix == "json":
                with open(file, encoding="utf-8") as fp:
                    try:
                        data = json.load(fp)
                    except json.JSONDecodeError as exc:
                        raise yaml.constructor.ConstructorError(
                            None,
                            None,
                            f"failed to parse JSON file {path}: {exc.msg}",
                            node.start_mark,
                        ) from exc
                    results.append(data)
            if suffix == "txt":
                with open(file, encoding="utf-8") as fp:
                    try:
                        data = fp.read()
                    except Exception as exc:
                        raise yaml.constructor.ConstructorError(
                            None,
                            None,
                            f"failed to read file {path}",
                            node.start_mark,
                        ) from exc
                    results.append(data)

            if suffix in ("yfm", "yfm:body", "yfm:head"):
                front, body = self._parse_yfm(file)
                results.append(front if suffix == "yfm:head" else body)

        if self.parse_only:
            return f"{path}"
        return results if is_glob else results[0]


ConfigLoader.add_constructor("!env", ConfigLoader.env_constructor)
ConfigLoader.add_multi_constructor("!env:", ConfigLoader.construct_multi_env)
ConfigLoader.add_constructor("!jinja", ConfigLoader.construct_str_jinja)
ConfigLoader.add_multi_constructor("!jinja:", ConfigLoader.construct_multi_jinja)
ConfigLoader.add_constructor("!include", ConfigLoader.include_constructor)
ConfigLoader.add_multi_constructor("!include:", ConfigLoader.multi_include_constructor)


@overload
def config_load(
    path: str | Path,
    context: dict[str, Any] | None = None,
    jinja_settings: dict[str, Any] | None = None,
    jinja_filters: dict[str, Callable[..., Any]] | None = None,
    *,
    constructor: type[T],
    jinja_vars_key: str = "__vars__",
    tags: Sequence[TagDefinition] | None = None,
) -> T: ...


@overload
def config_load(
    path: str | Path,
    context: dict[str, Any] | None = None,
    jinja_settings: dict[str, Any] | None = None,
    jinja_filters: dict[str, Callable[..., Any]] | None = None,
    *,
    constructor: None = None,
    jinja_vars_key: str = "__vars__",
    tags: Sequence[TagDefinition] | None = None,
) -> Any: ...  # noqa: ANN401


def config_load(  # noqa: PLR0913
    path: str | Path,
    context: dict[str, Any] | None = None,
    jinja_settings: dict[str, Any] | None = None,
    jinja_filters: dict[str, Callable[..., Any]] | None = None,
    *,
    constructor: type[Any] | None = None,
    jinja_vars_key: str = "__vars__",
    tags: Sequence[TagDefinition] | None = None,
) -> Any:
    """Load configuration from YAML file, with support for Jinja, ENV and Includes.

    Args:
        path: Path to the YAML configuration file
        context: Context variables for Jinja template rendering
        jinja_settings: Settings for Jinja environment
        jinja_filters: Custom Jinja filters
        constructor: Optional class constructor to create typed instances
        jinja_vars_key: Key name for context management in mappings (default: "__vars__")
        tags: Custom tag definitions, also used by YAML includes

    Returns:
        `Any` if no constructor is provided, otherwise an instance of the constructor type.

    Note:
        The constructor can be any Python class that accepts keyword arguments,
        including regular classes, dataclasses, NamedTuples, Pydantic models, etc.
        The loaded YAML data must be a dictionary to be passed as keyword arguments.
    """
    path = Path(path).absolute()

    with open(path, encoding="utf-8") as stream:
        loader = ConfigLoader(
            stream,
            path.parent,
            context=context,
            jinja_settings=jinja_settings,
            jinja_filters=jinja_filters,
            jinja_vars_key=jinja_vars_key,
            tags=tags,
        )
        try:
            data = loader.get_single_data()
        except yaml.YAMLError as exc:
            if exc.__cause__ is not None:
                raise
            raise exc from None

        finally:
            loader.dispose()

        if constructor:
            if not isinstance(data, dict):
                raise ValueError(
                    f"Failed to construct object from {path}: data is not a dictionary"
                )
            try:
                return constructor(**data)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Failed to construct object from {path}: {exc}") from None

        return data


def config_get_env(  # noqa: PLR0913
    path: str | Path,
    jinja_settings: dict[str, Any] | None = None,
    jinja_filters: dict[str, Callable[..., Any]] | None = None,
    jinja_vars_key: str = "__vars__",
    *,
    tags: Sequence[TagDefinition] | None = None,
    follow_includes: bool = False,
) -> set[str]:
    """Load configuration YAML file, parses it and return list of referenced ENV variables.

    Set ``follow_includes`` to also scan YAML files referenced by ``!include``.
    """
    return _scan(
        path,
        jinja_settings,
        jinja_filters,
        jinja_vars_key,
        tags,
        extract_tags=(),
        follow_includes=follow_includes,
    ).env_vars


def config_get_tag_refs(  # noqa: PLR0913
    path: str | Path,
    jinja_settings: dict[str, Any] | None = None,
    jinja_filters: dict[str, Callable[..., Any]] | None = None,
    jinja_vars_key: str = "__vars__",
    *,
    tags: Sequence[TagDefinition],
    select: Collection[str] | None = None,
    follow_includes: bool = False,
) -> dict[str, set[str]]:
    """Scan a YAML file and return the references of each custom tag, without constructing.

    Args:
        path: Path to the YAML configuration file
        jinja_settings: Settings for Jinja environment
        jinja_filters: Custom Jinja filters
        jinja_vars_key: Key name for context management in mappings
        tags: Custom tag definitions
        select: Custom tags to collect; all tags in ``tags`` if omitted
        follow_includes: Also scan YAML files referenced by ``!include``
    """
    if select is not None:
        unknown = set(select) - {definition.tag for definition in tags}
        if unknown:
            raise ValueError(f"select contains undefined tags: {sorted(unknown)}")
    return _scan(
        path,
        jinja_settings,
        jinja_filters,
        jinja_vars_key,
        tags,
        extract_tags=select,
        follow_includes=follow_includes,
    ).tag_refs


def _scan(  # noqa: PLR0913
    path: str | Path,
    jinja_settings: dict[str, Any] | None,
    jinja_filters: dict[str, Callable[..., Any]] | None,
    jinja_vars_key: str,
    tags: Sequence[TagDefinition] | None,
    *,
    extract_tags: Collection[str] | None = None,
    follow_includes: bool = False,
) -> ConfigLoader:
    """Parse a YAML file in parse-only mode and return the loader with collected data."""
    path = Path(path)

    with open(path, encoding="utf-8") as stream:
        loader = ConfigLoader(
            stream,
            path.parent.absolute(),
            context=None,
            jinja_settings=jinja_settings,
            jinja_filters=jinja_filters,
            parse_only=True,
            jinja_vars_key=jinja_vars_key,
            tags=tags,
            extract_tags=extract_tags,
            follow_includes=follow_includes,
        )
        loader.scanned_files.add(path.resolve())
        try:
            loader.get_single_data()
        finally:
            loader.dispose()

        return loader
