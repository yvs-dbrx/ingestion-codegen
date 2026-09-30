"""The gatekeeper between customer-controlled text and generated code, and the workspace-host check.
Pure functions — no database needed."""
import pytest

from app.services import codegen_safety as safety
from app.services.databricks_client import InvalidHost, normalize_host


# --- workspace host (SSRF) ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://dbc-1234.cloud.databricks.com", "https://dbc-1234.cloud.databricks.com"),
        ("dbc-1234.cloud.databricks.com", "https://dbc-1234.cloud.databricks.com"),
        ("https://DBC-1234.Cloud.Databricks.com/", "https://dbc-1234.cloud.databricks.com"),
        ("https://adb-123.4.azuredatabricks.net", "https://adb-123.4.azuredatabricks.net"),
        ("https://x.gcp.databricks.com:443", "https://x.gcp.databricks.com"),
    ],
)
def test_real_workspace_addresses_are_accepted(raw, expected):
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://dbc-1.cloud.databricks.com",  # not https
        "https://evil.com",
        "https://databricks.com.evil.com",  # lookalike: suffix isn't at the end
        "https://evildatabricks.com",  # no dot before the suffix
        "https://databricks.com",  # the bare suffix itself
        "https://169.254.169.254",  # cloud metadata service
        "https://localhost",
        "https://postgres",  # an internal docker hostname
        "https://user:pw@dbc-1.cloud.databricks.com",
        "https://dbc-1.cloud.databricks.com:8443",
        "https://dbc-1.cloud.databricks.com/api/2.0",
        "https://dbc-1.cloud.databricks.com?x=1",
        "file:///etc/passwd",
        "",
    ],
)
def test_anything_else_is_rejected(raw):
    with pytest.raises(InvalidHost):
        normalize_host(raw)


# --- values pasted into generated code ---------------------------------------------------

def _problems(fn, *args, **kwargs) -> list[str]:
    with pytest.raises(safety.UnsafeInput) as info:
        fn(*args, **kwargs)
    return info.value.problems


@pytest.mark.parametrize("value", ["customers", "_x", "Col_1", "a" * 128])
def test_identifiers_accepted(value):
    assert safety.check_identifier(value, "x") == value


@pytest.mark.parametrize(
    "value", ["", "1abc", "a b", "a-b", "a.b", 'a"); os.system(\'x\')#', "a\nb", "a`b", "a{b}", "é", "a" * 129, "x;DROP TABLE t"]
)
def test_identifiers_rejected(value):
    assert _problems(safety.check_identifier, value, "x")


@pytest.mark.parametrize(
    "value",
    ["/Volumes/main/landing/a.csv", "s3://bucket/key.csv", "abfss://c@acct.dfs.core.windows.net/x.csv", "dbfs:/mnt/x.json"],
)
def test_paths_accepted(value):
    assert safety.check_path(value, "p") == value


@pytest.mark.parametrize(
    "value",
    [
        "relative/path.csv",
        "/Volumes/x/../../etc/passwd",
        '/Volumes/x/a".csv',
        "/Volumes/x/a'.csv",
        "/Volumes/x/a\\b.csv",
        "/Volumes/x/a\nb.csv",
        "/Volumes/x/${jndi}.csv",
        "/Volumes/x/{name}.csv",
        "http://evil.com/x.csv",
        "",
        "/" + "a" * 2050,
    ],
)
def test_paths_rejected(value):
    assert _problems(safety.check_path, value, "p")


@pytest.mark.parametrize("value", ["a.csv", "my file.csv", "2024-01_x.json"])
def test_file_names_accepted(value):
    assert safety.check_file_name(value) == value


@pytest.mark.parametrize("value", ["a/b.csv", "{x}.csv", '"q".csv', "a\nb.csv", "..", ".", "", "a`.csv", "$x.csv"])
def test_file_names_rejected(value):
    """File names end up inside an f-string in the notebook — braces would be EXECUTED."""
    assert _problems(safety.check_file_name, value)


@pytest.mark.parametrize("value", [",", "|", "\t", ";", "||"])
def test_delimiters_accepted(value):
    assert safety.check_delimiter(value) == value


@pytest.mark.parametrize("value", ['"', "\\", "\n", "{", "", "abcde", "\x00"])
def test_delimiters_rejected(value):
    assert _problems(safety.check_delimiter, value)


@pytest.mark.parametrize("value", ["record", "ns:row", "a-b.c"])
def test_row_tags_accepted(value):
    assert safety.check_row_tag(value) == value


@pytest.mark.parametrize("value", ["", "1a", "a b", "a'b", "<x>", "a/b"])
def test_row_tags_rejected(value):
    assert _problems(safety.check_row_tag, value)


@pytest.mark.parametrize("value", ["customers_daily", "Load 1.0", "a"])
def test_job_names_accepted(value):
    assert safety.check_job_name(value) == value


@pytest.mark.parametrize("value", ["", " x", "-x", "a/b", "a" * 101, "a'b", "a\nb"])
def test_job_names_rejected(value):
    assert _problems(safety.check_job_name, value)


@pytest.mark.parametrize("value", ["/Workspace/Shared/ingestion/ab12cd34/customers", "/Workspace/Shared/ingestion/x/y.z"])
def test_workspace_paths_accepted(value):
    assert safety.check_workspace_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "/Workspace/Users/someone@x.com/nb",
        "/Workspace/Shared/ingestion/../../Users/x",
        "/Workspace/Shared/ingestion/a/../b",
        "/Workspace/Shared/other/x",
        "/Workspace/Shared/ingestion/",
        "/Workspace/Shared/ingestion/a b",
    ],
)
def test_workspace_paths_rejected(value):
    assert _problems(safety.check_workspace_path, value)


def test_schedule_rules():
    safety.check_schedule("weekly", "23:59", "SUN", "America/Chicago")
    for args in [
        ("monthly", "02:00", "MON", "UTC"),
        ("daily", "24:00", "MON", "UTC"),
        ("daily", "2:00", "MON", "UTC"),
        ("weekly", "02:00", "Monday", "UTC"),
        ("daily", "02:00", "MON", "Mars/Olympus"),
    ]:
        assert _problems(safety.check_schedule, *args)


# --- columns -----------------------------------------------------------------------------

def col(name="id", sql_type="BIGINT", **extra):
    return {"name": name, "sql_type": sql_type, **extra}


def test_valid_columns_get_a_spark_type_derived_from_the_sql_type():
    safe = safety.check_columns(
        [col("id", "bigint"), col("amt", "decimal(15, 2)"), col("d", "DATE")], source_type="cobol"
    )
    assert [(c.name, c.sql_type, c.spark_type) for c in safe] == [
        ("id", "BIGINT", "LongType"),
        ("amt", "DECIMAL(15,2)", "DecimalType"),
        ("d", "DATE", "DateType"),
    ]


def test_a_client_supplied_spark_type_is_ignored():
    safe = safety.check_columns([col("id", "BIGINT", spark_type='StringType"); evil()#')], source_type="csv")
    assert safe[0].spark_type == "LongType"


@pytest.mark.parametrize(
    "columns, fragment",
    [
        ([col('a"); os.system(\'x\')#')], "name must start"),
        ([col("has space")], "name must start"),
        ([col("_ingested_at")], "reserved"),
        ([col("_SOURCE_FILE")], "reserved"),
        ([col("a"), col("A")], "duplicate"),
        ([col("a", "VARCHAR(10); DROP TABLE x")], "isn't a supported type"),
        ([col("a", "ARRAY<STRING>")], "isn't a supported type"),
        ([col("a", "DECIMAL(10,2)")], "only supported for COBOL"),
        ([col("a", "DECIMAL(50,2)")], "isn't a supported type"),
        ([col("a", "STRING", struct_path=["x"])], "only valid for COBOL"),
    ],
)
def test_unsafe_or_unsupported_columns_are_rejected(columns, fragment):
    assert any(fragment in p for p in _problems(safety.check_columns, columns, source_type="csv"))


def test_cobol_struct_paths_are_checked():
    safety.check_columns([col("a", "STRING", struct_path=["GRP-1", "FLD_2"])], source_type="cobol")
    for bad in (["a b"], ['x"'], [], ["a"] * 11, "notalist"):
        assert _problems(safety.check_columns, [col("a", "STRING", struct_path=bad)], source_type="cobol")


def test_all_problems_are_reported_together():
    problems = _problems(safety.check_columns, [col("1bad"), col("ok", "NOPE"), col("_batch_id")], source_type="csv")
    assert len(problems) == 3
