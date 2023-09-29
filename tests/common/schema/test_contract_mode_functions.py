import pytest
import copy

from dlt.common.schema import Schema, DEFAULT_SCHEMA_CONTRACT_MODE
from dlt.common.schema.exceptions import SchemaFrozenException


def get_schema() -> Schema:
    s = Schema("event")

    columns =  {
        "column_1": {
            "name": "column_1",
            "data_type": "string"
        },
        "column_2": {
            "name": "column_2",
            "data_type": "number",
            "is_variant": True
        }
    }

    incomplete_columns = {
        "incomplete_column_1": {
            "name": "incomplete_column_1",
        },
        "incomplete_column_2": {
            "name": "incomplete_column_2",
        }
    }


    # add some tables
    s.update_table({
        "name": "tables",
        "columns": columns
    })

    s.update_table({
        "name": "child_table",
        "parent": "tables",
        "columns": columns
    })

    s.update_table({
        "name": "incomplete_table",
        "columns": incomplete_columns
    })

    s.update_table({
        "name": "mixed_table",
        "columns": {**incomplete_columns, **columns}
    })

    return s


def test_resolve_contract_settings() -> None:

    # defaults
    schema = get_schema()
    assert schema.resolve_contract_settings_for_table(None, "tables") == DEFAULT_SCHEMA_CONTRACT_MODE
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == DEFAULT_SCHEMA_CONTRACT_MODE

    # table specific full setting
    schema = get_schema()
    schema.tables["tables"]["schema_contract"] = "freeze"
    assert schema.resolve_contract_settings_for_table(None, "tables") == {
        "tables": "freeze",
        "columns": "freeze",
        "data_type": "freeze"
    }
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == {
        "tables": "freeze",
        "columns": "freeze",
        "data_type": "freeze"
    }

    # table specific single setting
    schema = get_schema()
    schema.tables["tables"]["schema_contract"] = {
        "tables": "freeze",
        "columns": "discard_value",
    }
    assert schema.resolve_contract_settings_for_table(None, "tables") == {
        "tables": "freeze",
        "columns": "discard_value",
        "data_type": "evolve"
    }
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == {
        "tables": "freeze",
        "columns": "discard_value",
        "data_type": "evolve"
    }

    # schema specific full setting
    schema = get_schema()
    schema._settings["schema_contract"] = "freeze"
    assert schema.resolve_contract_settings_for_table(None, "tables") == {
        "tables": "freeze",
        "columns": "freeze",
        "data_type": "freeze"
    }
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == {
        "tables": "freeze",
        "columns": "freeze",
        "data_type": "freeze"
    }

    # schema specific single setting
    schema = get_schema()
    schema._settings["schema_contract"] = {
        "tables": "freeze",
        "columns": "discard_value",
    }
    assert schema.resolve_contract_settings_for_table(None, "tables") == {
        "tables": "freeze",
        "columns": "discard_value",
        "data_type": "evolve"
    }
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == {
        "tables": "freeze",
        "columns": "discard_value",
        "data_type": "evolve"
    }

    # mixed settings
    schema = get_schema()
    schema._settings["schema_contract"] = "freeze"
    schema.tables["tables"]["schema_contract"] = {
        "tables": "evolve",
        "columns": "discard_value",
    }
    assert schema.resolve_contract_settings_for_table(None, "tables") == {
        "tables": "evolve",
        "columns": "discard_value",
        "data_type": "freeze"
    }
    assert schema.resolve_contract_settings_for_table("tables", "child_table") == {
        "tables": "evolve",
        "columns": "discard_value",
        "data_type": "freeze"
    }


# ensure other settings do not interfere with the main setting we are testing
base_settings = [{
    "tables": "evolve",
    "columns": "evolve",
    "data_type": "evolve"
    },{
        "tables": "discard_row",
        "columns": "discard_row",
        "data_type": "discard_row"
    }, {
        "tables": "discard_value",
        "columns": "discard_value",
        "data_type": "discard_value"
    }, {
        "tables": "freeze",
        "columns": "freeze",
        "data_type": "freeze"
    }
]


@pytest.mark.parametrize("base_settings", base_settings)
def test_check_adding_table(base_settings) -> None:

    schema = get_schema()
    data = {
        "column_1": "some string",
        "column_2": 123
    }
    new_table = copy.deepcopy(schema.tables["tables"])
    new_table["name"] = "new_table"

    #
    # check adding new table
    #
    assert schema.apply_schema_contract({**base_settings, **{"tables": "evolve"}}, "new_table", data, new_table, False) == (data, new_table)
    assert schema.apply_schema_contract({**base_settings, **{"tables": "discard_row"}}, "new_table", data, new_table, False) == (None, None)
    assert schema.apply_schema_contract({**base_settings, **{"tables": "discard_value"}}, "new_table", data, new_table, False) == (None, None)

    with pytest.raises(SchemaFrozenException):
        schema.apply_schema_contract({**base_settings, **{"tables": "freeze"}}, "new_table", data, new_table, False)


@pytest.mark.parametrize("base_settings", base_settings)
def test_check_adding_new_columns(base_settings) -> None:
    schema = get_schema()

    #
    # check adding new column
    #
    data = {
        "column_1": "some string",
        "column_2": 123
    }
    data_with_new_row = {
        **data,
        "new_column": "some string"
    }
    table_update = {
        "name": "tables",
        "columns": {
            "new_column": {
                "name": "new_column",
                "data_type": "string"
            }
        }
    }
    popped_table_update = copy.deepcopy(table_update)
    popped_table_update["columns"].pop("new_column")

    assert schema.apply_schema_contract({**base_settings, **{"columns": "evolve"}}, "tables", copy.deepcopy(data_with_new_row), table_update, True) == (data_with_new_row, table_update)
    assert schema.apply_schema_contract({**base_settings, **{"columns": "discard_row"}}, "tables", copy.deepcopy(data_with_new_row), table_update, True) == (None, None)
    assert schema.apply_schema_contract({**base_settings, **{"columns": "discard_value"}}, "tables", copy.deepcopy(data_with_new_row), table_update, True) == (data, popped_table_update)

    with pytest.raises(SchemaFrozenException):
        schema.apply_schema_contract({**base_settings, **{"columns": "freeze"}}, "tables", copy.deepcopy(data_with_new_row), table_update, True)


    #
    # check adding new column if target column is not complete
    #
    data = {
        "column_1": "some string",
        "column_2": 123,
    }
    data_with_new_row = {
        **data,
        "incomplete_column_1": "some other string",
    }
    table_update = {
        "name": "mixed_table",
        "columns": {
            "incomplete_column_1": {
                "name": "incomplete_column_1",
                "data_type": "string"
            }
        }
    }
    popped_table_update = copy.deepcopy(table_update)
    popped_table_update["columns"].pop("incomplete_column_1")

    # incomplete columns should be treated like new columns
    assert schema.apply_schema_contract({**base_settings, **{"columns": "evolve"}}, "mixed_table", copy.deepcopy(data_with_new_row), table_update, True) == (data_with_new_row, table_update)
    assert schema.apply_schema_contract({**base_settings, **{"columns": "discard_row"}}, "mixed_table", copy.deepcopy(data_with_new_row), table_update, True) == (None, None)
    assert schema.apply_schema_contract({**base_settings, **{"columns": "discard_value"}}, "mixed_table", copy.deepcopy(data_with_new_row), table_update, True) == (data, popped_table_update)

    with pytest.raises(SchemaFrozenException):
        schema.apply_schema_contract({**base_settings, **{"columns": "freeze"}}, "mixed_table", copy.deepcopy(data_with_new_row), table_update, True)



def test_check_adding_new_variant() -> None:
    schema = get_schema()

    #
    # check adding new variant column
    #
    data = {
        "column_1": "some string",
        "column_2": 123
    }
    data_with_new_row = {
        **data,
        "column_2_variant": 345345
    }
    table_update = {
        "name": "tables",
        "columns": {
            "column_2_variant": {
                "name": "column_2_variant",
                "data_type": "number",
                "variant": True
            }
        }
    }
    popped_table_update = copy.deepcopy(table_update)
    popped_table_update["columns"].pop("column_2_variant")

    assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "evolve"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (data_with_new_row, table_update)
    assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "discard_row"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (None, None)
    assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "discard_value"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (data, popped_table_update)

    with pytest.raises(SchemaFrozenException):
        schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "freeze"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True)

    # check interaction with new columns settings, variants are new columns..
    with pytest.raises(SchemaFrozenException):
        assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "evolve", "columns": "freeze"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (data_with_new_row, table_update)

    assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "evolve", "columns": "discard_row"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (None, None)
    assert schema.apply_schema_contract({**DEFAULT_SCHEMA_CONTRACT_MODE, **{"data_type": "evolve", "columns": "discard_value"}}, "tables", copy.deepcopy(data_with_new_row), copy.deepcopy(table_update), True) == (data, popped_table_update)