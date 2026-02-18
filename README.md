# yaml-config-tags

[![PyPI](https://img.shields.io/pypi/v/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)
[![Python Versions](https://img.shields.io/pypi/pyversions/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)
[![License](https://img.shields.io/pypi/l/yaml-config-tags)](https://pypi.org/project/yaml-config-tags/)

Provides a set of custom tags to extend YAML for managing advanced configurations easily within a file.
It supports:

- Environment variables: `!env`
- File includes: `!include`
- Jinja templating: `!jinja`
- Scoped context variables: `__vars__`

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
    jinja_filters: dict[str, Callable] | None = None,
    *,
    constructor: type[Any] | None = None,
    jinja_vars_key: str = "__vars__",
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

#### Explicit type

Environment variables are converted using implicit yaml types by default, but you can force a specific data type with tag suffix:

- `!env:str VAR_NAME`
- `!env:int VAR_NAME`

Valid type suffix are:

- str
- int
- float
- bool
- timestamp

You can also combine defaults and fallbacks with type suffix:
`!env:str [VAR_NAME, default_value]`

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

### Scoped Context Variables

You can create scoped context variables within any mapping using the special `__vars__` key. This allows you to define variables that are available to Jinja templates within that scope and any nested scopes.

Variables defined in `__vars__` are accessed via the `vars` namespace in Jinja templates: `{{ vars.variable_name }}`.

#### Basic Usage

```yaml
api_config:
  __vars__:
    base_url: "https://api.example.com"
    version: "v2"
  
  endpoint: !jinja "{{ vars.base_url }}/{{ vars.version }}/users"
  auth_url: !jinja "{{ vars.base_url }}/{{ vars.version }}/auth"
```

#### Nested Scopes

Nested mappings can define their own `__vars__` which inherit from and override parent scope variables:

```yaml
api_config:
  __vars__:
    base_url: "https://api.example.com"
    version: "v2"
  
  endpoint: !jinja "{{ vars.base_url }}/{{ vars.version }}/users"
  
  settings:
    __vars__:
      timeout: 30
    
    # This scope has access to both parent vars (base_url, version) and its own (timeout)
    connection_timeout: !jinja "{{ vars.timeout }}"
    read_url: !jinja "{{ vars.base_url }}/{{ vars.version }}/read"
```

#### Scope Restoration

When exiting a scope, the parent's context is automatically restored:

```yaml
# Global context
global_value: !jinja "{{ vars.base_url }}"  # Uses global context

api_config:
  __vars__:
    base_url: "https://api.example.com"  # Overrides base_url for this scope
  
  endpoint: !jinja "{{ vars.base_url }}"  # Uses scoped value

# Back to global context
fallback: !jinja "{{ vars.base_url }}"  # Uses global context again
```

#### Custom Key Name

You can customize the special key name by passing `jinja_vars_key` to the loader:

```python
config = config_load('config.yml', context={'vars': {'env': 'prod'}}, jinja_vars_key='_context')
```

```yaml
api:
  _context:
    version: "v3"
  url: !jinja "{{ vars.version }}"
```

Note: The `__vars__` key itself is never included in the final loaded configuration.
