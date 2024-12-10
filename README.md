# yaml-config-tags

[![PyPI](https://img.shields.io/pypi/v/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)
[![Python Versions](https://img.shields.io/pypi/pyversions/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)
[![License](https://img.shields.io/pypi/l/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)

Provides a set of custom tags to extend YAML for managing advanced configurations easily within a file.
It supports:

- Environment variables: `!env`
- File includes: `!include`
- Jinja templating: `!jinja`

## Installation

```bash
pip install yaml-config-tags
```

## Usage

Simply load the configuration file using `config_load` and pass a context dictionary to the loader.

```python
config_load(
    path: str | Path,
    context: dict[str, Any] | None = None,
    jinja_settings: dict[str, Any] | None = None,
) -> Any
```

#### Example

```python
from yaml_config import config_load

context = {
    'name': 'John Doe'
}
config = config_load('config.yml', context)
```

```yaml
# config.yml
database:
  user: !env DB_USER
  password: !env DB_PASSWORD

advanced: !include advanced.yaml

greeting: !jinja |
  Hello, {{ name }}!
```

## Features

### Environment Variables

You can use environment variables in your configuration file by using the `!env` tag.

There are three ways to use environment variables:

- `!env VAR_NAME` - Load the environment variable `VAR_NAME`. If it is not set, an exception will be raised.
- `!env [VAR_NAME, default_value]` - Load the environment variable `VAR_NAME` with a default value if it is not set.
- `!env [VAR_NAME, FALLBACK_VAR1, .., FALLBACK_VARn, default_value]` - Load the environment variable `VAR_NAME`, if it is not set, try to load the fallback variables in order. If none of them are set, use the default value.

### Includes

You can include other files in your configuration file by using the `!include` tag.

```yaml
# config.yml
advanced: !include advanced.yaml
```

Three types of files are supported, specified as a tag suffix:

- `yaml` - Load the file as a YAML file.
- `json` - Load the file as a JSON file.
- `txt` - Load the file as a plain text file.

If no suffix is specified, the file will be loaded as a YAML file.

```yaml
text_data: !include:txt text.txt
json_data: !include:json data.json
```

Relative paths are resolved relative to the directory of the file that contains the include.

#### Glob Patterns

You can use glob patterns in the file path, and all matching files will be included as a list.

```yaml
files: !include:yaml "data/*.yaml"
```

### Jinja Templating

You can use Jinja templating in your configuration file by using the `!jinja` tag. The context available to the template is passed as an argument to the loader.

```yaml
greeting: !jinja |
  Hello, {{ name }}!
```

The `!jinja` tag is a short form for `!jinja:str`, a jinja template rendered as a string.

#### Native objects

In addition to plain text templates, you can also render a template to native objects.

```yaml
connection:
  url: !env DB_URL
  token_provider: !jinja:obj |
    {{token_provider}}
```

When using `!jinja:obj` the template is rendered using _NativeEnvironment_ and the result is evaluated as a native object instead of a string.
