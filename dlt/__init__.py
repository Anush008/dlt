"""dlt

How to create a data loading pipeline with dlt in 3 seconds:

    1. Write a pipeline script
    >>> import dlt
    >>> from dlt.sources.helpers import requests
    >>> dlt.run(requests.get("https://api.chess.com/pub/player/magnuscarlsen/games/2022/11").json()["games"], destination="duckdb", table_name="magnus_games")

    2. Run your pipeline script
    $ python magnus_games.py

    3. See and query your data with autogenerated Streamlit app
    $ dlt pipeline dlt_magnus_games show


Or start with our pipeline template with sample chess.com data loaded to bigquery

    $ dlt init chess duckdb

For more detailed info, see https://dlthub.com/docs/walkthroughs
"""

from dlt.version import __version__
from dlt.common.configuration.accessors import config, secrets
from dlt.common.typing import TSecretValue as _TSecretValue
from dlt.common.configuration.specs import CredentialsConfiguration as _CredentialsConfiguration
from dlt.common.pipeline import source_state as state
from dlt.common.schema import Schema

from dlt import sources
from dlt.extract.decorators import source, resource, transformer, defer
from dlt.pipeline import pipeline as _pipeline, run, attach, Pipeline, dbt, current as _current, mark as _mark
from dlt.pipeline import progress

pipeline = _pipeline
current = _current
mark = _mark

TSecretValue = _TSecretValue
"When typing source/resource function arguments it indicates that a given argument is a secret and should be taken from dlt.secrets."

TCredentials = _CredentialsConfiguration
"When typing source/resource function arguments it indicates that a given argument represents credentials and should be taken from dlt.secrets. Credentials may be a string, dictionary or any other type."
