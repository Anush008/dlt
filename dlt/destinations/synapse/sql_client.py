import platform
import struct
from datetime import datetime, timedelta, timezone  # noqa: I251

from dlt.common.destination import DestinationCapabilitiesContext

import re

import pyodbc

from contextlib import contextmanager
from typing import Any, AnyStr, ClassVar, Iterator, Optional, Sequence

from dlt.destinations.exceptions import DatabaseTerminalException, DatabaseTransientException, DatabaseUndefinedRelation
from dlt.destinations.typing import DBApi, DBApiCursor, DBTransaction
from dlt.destinations.sql_client import DBApiCursorImpl, SqlClientBase, raise_database_error, raise_open_connection_error

from dlt.destinations.synapse.configuration import SynapseCredentials
from dlt.destinations.synapse import capabilities

import logging  # Import the logging module if it's not already imported

from typing import List, Tuple, Union, Any #Import List for INSERT query generation

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

import json
def is_valid_json(s: str) -> bool:
    """Check if a string is valid JSON."""
    if not (s.startswith('{') and s.endswith('}')) and not (s.startswith('[') and s.endswith(']')):
        return False
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


def handle_datetimeoffset(dto_value: bytes) -> datetime:
    # ref: https://github.com/mkleehammer/pyodbc/issues/134#issuecomment-281739794
    tup = struct.unpack("<6hI2h", dto_value)  # e.g., (2017, 3, 16, 10, 35, 18, 500000000, -6, 0)
    return datetime(
        tup[0], tup[1], tup[2], tup[3], tup[4], tup[5], tup[6] // 1000, timezone(timedelta(hours=tup[7], minutes=tup[8]))
    )

class PyOdbcSynapseClient(SqlClientBase[pyodbc.Connection], DBTransaction):
    def execute_fragments(self, fragments):
        # TODO clean up this methodology, tried mirroring original code
        # Unpack the outer tuple
        sql, params = fragments
        self.execute_query(sql, params)

    dbapi: ClassVar[DBApi] = pyodbc
    capabilities: ClassVar[DestinationCapabilitiesContext] = capabilities()

    def __init__(self, dataset_name: str, credentials: SynapseCredentials) -> None:
        super().__init__(credentials.database, dataset_name)
        self._conn: pyodbc.Connection = None
        self._transaction_in_progress = False  # Transaction state flag
        self.credentials = credentials

    def generate_insert_query(self, sql: str) -> tuple:
        # Extracting table name and column names
        table_name = sql.split(" INTO ")[1].split("(")[0].strip()
        column_names_str = sql.split("(")[1].split(")")[0].strip()

        # Extracting row data
        row_data_str = sql.split("VALUES")[1].strip()
        # Using regex to split rows accurately
        rows = [tuple(re.split(r"\s*,\s*(?![^()]*\))", row)) for row in re.findall(r"\((.*?)\)", row_data_str)]

        # Ensure each item in rows is properly formatted
        formatted_rows = []
        for row in rows:
            formatted_row = []
            for item in row:
                # Remove extra single quotes if item is not a JSON string
                if item.startswith("'") and item.endswith("'") and not item.lstrip("'").startswith("{"):
                    formatted_row.append(item.strip("'"))
                else:
                    formatted_row.append(item)
            formatted_rows.append(tuple(formatted_row))

        # Building SELECT statements with parameter markers
        select_statements = [f"SELECT {', '.join(['?' for _ in row])}" for row in formatted_rows]

        # Combining SELECT statements with UNION ALL
        all_select_statements = " UNION ALL ".join(select_statements)

        # Building the final SQL query
        new_sql = f'INSERT INTO {table_name}({column_names_str}) {all_select_statements}'

        # Extracting parameter values
        param_values = [item for row in formatted_rows for item in row]

        return new_sql, param_values

    def open_connection(self) -> pyodbc.Connection:
        try:
            # Establish a connection
            conn_str = (
                f"DRIVER={{{self.credentials.odbc_driver}}};"
                f"SERVER={self.credentials.host};"
                f"DATABASE={self.credentials.database};"
                f"UID={self.credentials.user};"
                f"PWD={self.credentials.password};"
            )
            self._conn = pyodbc.connect(conn_str)

            # Add the converter for datetimeoffset
            self._conn.add_output_converter(-155, handle_datetimeoffset)

            # Noting that autocommit is being set to True
            self._conn.autocommit = True

            return self._conn

        except pyodbc.Error as e:
            raise  # re-raise the error without logging it


    @raise_open_connection_error
    def close_connection(self) -> None:
        try:
            if self._conn:
                if self._transaction_in_progress:
                    self._conn.commit()
                self._conn.close()
        except pyodbc.Error as e:
            # Log detailed error information here
            logger.error(f"Failed to close connection due to pyodbc.Error: {str(e)}")

    @contextmanager
    def begin_transaction(self) -> Iterator[DBTransaction]:
        try:
            self._conn.autocommit = False
            yield self
            self.commit_transaction()
        except Exception:
            self.rollback_transaction()
            raise

    @raise_database_error
    def commit_transaction(self) -> None:
        self._conn.commit()
        self._conn.autocommit = True

    @raise_database_error
    def rollback_transaction(self) -> None:
        self._conn.rollback()
        self._conn.autocommit = True

    @property
    def native_connection(self) -> pyodbc.Connection:
        return self._conn

    def drop_dataset(self) -> None:
        rows = self.execute_sql(
            "SELECT table_name FROM information_schema.views WHERE table_schema = %s;", self.dataset_name
        )
        view_names = [row[0] for row in rows]
        self._drop_views(*view_names)
        rows = self.execute_sql(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s;", self.dataset_name
        )
        table_names = [row[0] for row in rows]
        self.drop_tables(*table_names)

        self.execute_sql("DROP SCHEMA %s;" % self.fully_qualified_dataset_name())

    def table_exists(self, table_name: str) -> bool:
        query = """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """
        result = self.execute_sql(query, self.dataset_name, table_name)
        return result[0][0] > 0

    def drop_tables(self, *tables: str) -> None:
        if not tables:
            return
        for table in tables:
            if self.table_exists(table):
                self.execute_sql(f"DROP TABLE {self.make_qualified_table_name(table)};")

    def _drop_views(self, *tables: str) -> None:
        if not tables:
            return
        statements = [f"DROP VIEW {self.make_qualified_table_name(table)};" for table in tables]
        self.execute_fragments(statements)

    def set_input_sizes(self, *args):
        if len(args) == 1 and isinstance(args[0], tuple):
            args = args[0]

        args = list(args)  # Convert tuple to list for modification
        input_sizes = []
        for index, arg in enumerate(args):
            if isinstance(arg, str):
                # Check if the string is a valid JSON
                if is_valid_json(arg):
                    input_sizes.append((pyodbc.SQL_WVARCHAR, 0, 0))  # Set input size for JSON columns
                else:
                    # Assuming ntext is represented as a regular string
                    # You may want to have a more robust check for ntext data here
                    input_sizes.append((pyodbc.SQL_WVARCHAR, 0, 0))  # Set input size for ntext columns
            else:
                input_sizes.append(None)  # Default handling for non-string types

        logger.debug(f"Input sizes: {input_sizes}")
        return input_sizes, tuple(args)


    def execute_sql(self, sql: AnyStr, *args: Any, **kwargs: Any) -> Optional[Sequence[Sequence[Any]]]:
        with self.execute_query(sql, *args, **kwargs) as curr:
            logger.info(f"Trying to execute SQL: {sql}, Parameters: {args}")
            if curr.description is None:
                return None
            else:
                f = curr.fetchall()
                return f

    @contextmanager
    @raise_database_error
    def execute_query(self, query: AnyStr, *args: Any, **kwargs: Any) -> Iterator[DBApiCursor]:
        # logger.info(f"Entered execute_query method with query: {query}")
        assert isinstance(query, str)
        curr: DBApiCursor = None

        if kwargs:
            raise NotImplementedError("pyodbc does not support named parameters in queries")

        curr = self._conn.cursor()

        # Set the converter for datetimeoffset
        self._conn.add_output_converter(-155, handle_datetimeoffset)

        # Set input sizes for Synapse to handle varchar(max) instead of ntext
        input_sizes, args = self.set_input_sizes(*args)
        curr.setinputsizes(input_sizes)

        try:
            query = query.replace('%s', '?')  # Updated line
            curr.execute(query, *args)  # No flattening, just unpack args directly
            print("QUERY FINISHED: " + str(query) + "\n\n")
            #logger.info(f"Query executed successfully")
            yield DBApiCursorImpl(curr)  # type: ignore[abstract]

        except pyodbc.Error as outer:
            logger.error(f"SQL Error during query execution. Query: {query}, Parameters: {args}, Types: {[type(arg) for arg in args]}, Error: {str(outer)}", exc_info=True)
            #logger.error(f"SQL Error during query execution. Query: {query}, Error: {str(outer)}", exc_info=True)
            raise outer

        finally:
            curr.close()

    def fully_qualified_dataset_name(self, escape: bool = True) -> str:
        return self.capabilities.escape_identifier(self.dataset_name) if escape else self.dataset_name

    @classmethod
    def _make_database_exception(cls, ex: Exception) -> Exception:
        if isinstance(ex, pyodbc.ProgrammingError):
            if ex.args[0] == "42S02":
                return DatabaseUndefinedRelation(ex)
            if ex.args[1] == "HY000":
                return DatabaseTransientException(ex)
            elif ex.args[0] == "42000":
                if "(15151)" in ex.args[1]:
                    return DatabaseUndefinedRelation(ex)
                return DatabaseTransientException(ex)
        elif isinstance(ex, pyodbc.OperationalError):
            return DatabaseTransientException(ex)
        elif isinstance(ex, pyodbc.Error):
            if ex.args[0] == "07002":  # incorrect number of arguments supplied
                return DatabaseTransientException(ex)
        return DatabaseTerminalException(ex)

    @staticmethod
    def is_dbapi_exception(ex: Exception) -> bool:
        return isinstance(ex, pyodbc.Error)