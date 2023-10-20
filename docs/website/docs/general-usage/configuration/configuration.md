---
title: Secrets and Config
description: Overview secrets and configs
keywords: [credentials, secrets.toml, environment variables]
---


# Secrets and Config

## Overview

### General Usage and an Example

The way config values and secrets are handled should promote correct behavior

1. secret values should never be present in the pipeline code
2. pipeline may be reconfigured for production after it is deployed. deployed and local code should be identical
3. still it must be easy and intuitive

For the source extractor function below (reads selected tab from google sheets) we can pass config values in following ways:

```python

import dlt


@dlt.source
def google_sheets(spreadsheet_id, tab_names=dlt.config.value, credentials=dlt.secrets.value, only_strings=False):
    sheets = build('sheets', 'v4', credentials=Services.from_json(credentials))
    tabs = []
    for tab_name in tab_names:
        data = sheets.get(spreadsheet_id, tab_name).execute().values()
        tabs.append(dlt.resource(data, name=tab_name))
    return tabs

# WRONG: provide all values directly - wrong but possible. secret values should never be present in the code!
google_sheets("23029402349032049", ["tab1", "tab2"], credentials={"private_key": ""}).run(destination="bigquery")

# OPTION A: provide config values directly and secrets via automatic injection mechanism (see later)
# `credentials` value will be injected by the `source` decorator
# `spreadsheet_id` and `tab_names` take values from the arguments below
# `only_strings` will be injected by the source decorator or will get the default value False
google_sheets("23029402349032049", ["tab1", "tab2"]).run(destination="bigquery")


# OPTION B: use `dlt.secrets` and `dlt.config` to explicitly take those values from providers from the explicit keys
google_sheets(dlt.config["sheet_id"], dlt.config["my_section.tabs"], dlt.secrets["my_section.gcp_credentials"]).run(destination="bigquery")
```

> one of the principles is that configuration, credentials and secret values are may be passed explicitly as arguments to the functions. this makes the injection behavior optional.

### Injection mechanism
Config and secret values are injected to the function arguments if the function is decorated with `@dlt.source` or `@dlt resource` (also `@with_config` which you can applu to any function - used havily in the dlt core)

The signature of the function `google_sheets` is **explicitly accepting all the necessary configuration and secrets in its arguments**. During runtime, `dlt` tries to supply (`inject`) the required values via various config providers. The injection rules are:
1. if you call the decorated function, the arguments that are passed explicitly are **never injected**
this makes injection mechanism optional

2. required arguments (ie. `spreadsheet_id`, `tab_names`) are not injected
3. arguments with default values are injected if present in config providers
4. arguments with the special default value `dlt.secrets.value` and `dlt.config.value` **must be injected** (or expicitly passed). If they are not found by the config providers the code raises exception. The code in the functions always receives those arguments.

additionally `dlt.secrets.value` tells `dlt` that supplied value is a secret and it will be injected only from secure config providers

### Passing config values and credentials explicitly

```python
# OPTION B: use `dlt.secrets` and `dlt.config` to explicitly take those values from providers from the explicit keys
google_sheets(dlt.config["sheet_id"], dlt.config["tabs"], dlt.secrets["my_section.gcp_credentials"]).run(destination="bigquery")
```

[See example](/docs/examples/credentials/explicit.py)

### Typing the source and resource signatures

You should type your function signatures! The effort is very low and it gives `dlt` much more information on what source/resource expects.
1. You'll never receive invalid type signatures
2. We can generate nice sample config and secret files for your source
3. You can request dictionaries or special values (ie. connection strings, service json) to be passed
4. ☮️ you can specify a set of possible types via `Union` ie. OAUTH or Api Key authorization

```python
@dlt.source
def google_sheets(spreadsheet_id: str, tab_names: List[str] = dlt.config.value, credentials: GcpClientCredentialsWithDefault = dlt.secrets.value, only_strings: bool = False):
  ...
```
Now:
1. you are sure that you get a list of strings as `tab_names`
2. you will get actual google credentials (see `CredentialsConfiguration` later) and your users can pass them in many different forms.

In case of `GcpClientCredentialsWithDefault`
* you may just pass the `service_json` as string or dictionary (in code and via config providers)
* you may pass a connection string (used in sql alchemy) (in code and via config providers)
* or default credentials will be used


## Secret and config values layout.

`dlt` uses an layout of hierarchical sections to organize the config and secret values. This makes configurations and secrets easy to manage and disambiguates values with the same keys by placing them in the different sections

> if you know how `toml` files are organized -> this is the same concept!

> a lot of config values are dictionaries themselves (ie. most of the credentials) and you want the values corresponding to one component to be close together.

> you can have a separate credentials for your destinations and each of source your pipeline uses, if you have many pipelines in single project, you can have a separate sections corresponding to them.

Here is the simplest default layout for our `google_sheets` example.

### OPTION A (default layout)

**secrets.toml**
```toml
[credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>
```
**config.toml**
```toml
tab_names=["tab1", "tab2"]
```

As you can see the details of gcp credentials are placed under `credentials` which is argument name to source function

### OPTION B (explicit layout)

Here user has full control over the layout

**secrets.toml**
```toml
[my_section]

  [my_section.gcp_credentials]
  client_email = <client_email from services.json>
  private_key = <private_key from services.json>
```
**config.toml**
```toml
[my_section]
tabs=["tab1", "tab2"]

  [my_section.gcp_credentials]
  project_id = <project_id from services json>  # I prefer to keep my project id in config file and private key in secrets
```

### Default layout and default key lookup during injection

`dlt` arranges the sections into **default layout** that is used by injection mechanism. This layout makes it easy to configure simple cases but also provides a room for more explicit sections and complex cases ie. having several soures with different credentials or even hosting several pipelines in the same project sharing the same config and credentials.

```
pipeline_name
    |
    |-sources
        |-<source 1 module name>
          |-<source function 1 name>
            |- {all source and resource options and secrets}
          |-<source function 2 name>
            |- {all source and resource options and secrets}
        |-<source 2 module>
          |...

        |-extract
          |- extract options for resources ie. parallelism settings, maybe retries
    |-destination
        |- <destination name>
          |- {destination options}
            |-credentials
              |-{credentials options}
    |-schema
        |-<schema name>
            |-schema settings: not implemented but I'll let people set nesting level, name convention, normalizer etc. here
    |-load
    |-normalize
```

Lookup rules:

**Rule 1** All the sections above are optional. You are free to arrange your credentials and config without any additional sections
Example: OPTION A (default layout)

**Rule 2** The lookup starts with the most specific possible path and if value is not found there, it removes the right-most section and tries again.
Example: In case of option A we have just one credentials. But what if `bigquery` credentials are different from `google sheets`? Then we need to allow some sections to separate them.

```toml
# google sheet credentials
[credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>

# bigquery credentials
[destination.credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>
```
Now when `dlt` looks for destination credentials, it will encounter the `destination` section and stop there.
When looking for `sources` credentials it will get  directly into `credentials` key (corresponding to function argument)

> we could also rename the argument in the source function! but then we are **forcing** the user to have two copies of credentials.

Example: let's be even more explicit and use full section path possible
```toml
# google sheet credentials
[sources.google_sheets.credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>

# bigquery credentials
[destination.bigquery.credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>
```
Where we add destination and source name to be very explicit.

**Rule 3** You can use your pipeline name to have separate configurations for each pipeline in your project

Pipeline created/obtained with `dlt.pipeline()` creates a global and optional namespace with the value of `pipeline_name`. All config values will be looked with pipeline name first and then again without it.

Example: the pipeline is named `ML_sheets`
```toml
[ML_sheets.credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>
```

or maximum path:
```toml
[ML_sheets.sources.google_sheets.credentials]
client_email = <client_email from services.json>
private_key = <private_key from services.json>
project_id = <project_id from services json>
```

### The `sources` section
Config and secrets for decorated sources and resources are kept in `sources.<source module name>.<function_name>` section. **All sections are optionsl**. For example if source module is named
`pipedrive` and the function decorated with `@dlt.source` is `deals(api_key: str=...)` then `dlt` will look for api key in:
1. `sources.pipedrive.deals.api_key`
2. `sources.pipedrive.api_key`
3. `sources.api_key`
4. `api_key`

Step 2 in search path allows all the sources/resources in a module to share the same set of credentials.

Also look at the following [test](/tests/extract/test_decorators.py) : `test_source_sections`


## Understanding the exceptions
Now we can finally understand the `ConfigFieldMissingException`. Let's run `chess.py` example without providing the password:

```
$ CREDENTIALS="postgres://loader@localhost:5432/dlt_data" python chess.py
...
dlt.common.configuration.exceptions.ConfigFieldMissingException: Following fields are missing: ['password'] in configuration with spec PostgresCredentials
        for field "password" config providers and keys were tried in following order:
                In Environment Variables key CHESS_GAMES__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD was not found.
                In Environment Variables key CHESS_GAMES__DESTINATION__CREDENTIALS__PASSWORD was not found.
                In Environment Variables key CHESS_GAMES__CREDENTIALS__PASSWORD was not found.
                In secrets.toml key chess_games.destination.postgres.credentials.password was not found.
                In secrets.toml key chess_games.destination.credentials.password was not found.
                In secrets.toml key chess_games.credentials.password was not found.
                In Environment Variables key DESTINATION__POSTGRES__CREDENTIALS__PASSWORD was not found.
                In Environment Variables key DESTINATION__CREDENTIALS__PASSWORD was not found.
                In Environment Variables key CREDENTIALS__PASSWORD was not found.
                In secrets.toml key destination.postgres.credentials.password was not found.
                In secrets.toml key destination.credentials.password was not found.
                In secrets.toml key credentials.password was not found.
Please refer to https://dlthub.com/docs/general-usage/credentials for more information
```

It tells you exactly which paths `dlt` looked at, via which config providers and in which order. In the example above
1. First it looked in a big section `chess_games` which is name of the pipeline
2. In each case it starts with full paths and goes to minimum path `credentials.password`
3. First it looks into `environ` then in `secrets.toml`. It displays the exact keys tried.
4. Note that `config.toml` was skipped! It may not contain any secrets.