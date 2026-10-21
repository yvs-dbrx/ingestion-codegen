"""Gatekeeper between customer-controlled text and generated code.

The generator turns names, paths and settings into Python and SQL that then RUNS inside the
customer's Databricks workspace with their credentials. Any of those strings — a CSV header,
a file name in a storage folder, a copybook path — can be attacker-influenced, and the
generator templates simply paste them into source text. A header like  a"), os.system('..')#
would become live code. So every value that reaches a template is validated here first, with
allowlists (what's permitted) rather than blocklists (what's known to be bad).

`UnsafeInput` collects ALL problems in one go so the caller can fix everything at once.
"""
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
STRUCT_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
ROW_TAG = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-:]{0,127}$")
JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,99}$")
CLUSTER_ID = re.compile(r"^[A-Za-z0-9\-]{5,64}$")
TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DELIMITER = re.compile(r'^[^"\\\r\n\x00{}]{1,4}$')  # a tab is fine; quotes/backslash/newline/braces aren't
WORKSPACE_ROOT = "/Workspace/Shared/ingestion/"
WORKSPACE_PATH = re.compile(r"^/Workspace/Shared/ingestion/[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*$")

PATH_PREFIXES = ("/", "s3://", "s3a://", "abfss://", "gs://", "dbfs:/")
_PATH_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\"'`\\${}]")  # control chars (incl. newline), quotes, backslash, $, braces

# The only names the engine's discovery produces, plus DECIMAL(p,s) — nothing else may reach a DDL.
SQL_TO_SPARK = {
    "STRING": "StringType",
    "BIGINT": "LongType",
    "INT": "IntegerType",
    "SMALLINT": "ShortType",
    "TINYINT": "ByteType",
    "DOUBLE": "DoubleType",
    "FLOAT": "FloatType",
    "BOOLEAN": "BooleanType",
    "DATE": "DateType",
    "TIMESTAMP": "TimestampType",
    "BINARY": "BinaryType",
}
DECIMAL = re.compile(r"^DECIMAL\((\d{1,2}),(\d{1,2})\)$")
RESERVED_COLUMNS = {"_ingested_at", "_source_file", "_batch_id"}  # the lineage columns every pipeline adds
WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
# Constants a user types into a Silver transform (values to treat as empty, a default, the unknown
# member). Letters, digits, spaces and a few separators only: no quotes, backslashes or control
# characters, so whatever else happens a value can't break out of the SQL string it is placed in.
SIMPLE_TEXT = re.compile(r"^[A-Za-z0-9 _.\-/+:@#]{0,64}$")
# The only date / timestamp layouts a transform may use (Spark datetime patterns).
DATE_FORMATS = ("yyyyMMdd", "yyyy-MM-dd", "yyyy/MM/dd", "MM/dd/yyyy", "dd/MM/yyyy", "dd-MM-yyyy", "MMddyyyy", "ddMMyyyy")
TIMESTAMP_FORMATS = (
    "yyyy-MM-dd HH:mm:ss",
    "yyyy-MM-dd HH:mm:ss.SSS",
    "yyyy-MM-dd'T'HH:mm:ss",
    "yyyyMMddHHmmss",
    "MM/dd/yyyy HH:mm:ss",
    "dd/MM/yyyy HH:mm:ss",
)


class UnsafeInput(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _fail_if(problems: list[str]) -> None:
    if problems:
        raise UnsafeInput(problems)


def collect(problems: list[str], fn, *args, **kwargs):
    """Runs one check, adding its complaints to `problems` instead of stopping at the first;
    returns the check's result, or None if it failed."""
    try:
        return fn(*args, **kwargs)
    except UnsafeInput as exc:
        problems.extend(exc.problems)
        return None


def check_identifier(value: str, what: str) -> str:
    _fail_if([] if IDENTIFIER.match(value or "") else [
        f"{what} {value!r} must start with a letter or underscore and contain only letters, digits and underscores (max 128)"
    ])
    return value


def check_simple_text(value: str, what: str, *, allow_empty: bool = True) -> str:
    ok = isinstance(value, str) and SIMPLE_TEXT.match(value) is not None and (allow_empty or value != "")
    _fail_if([] if ok else [
        f"{what} {value!r} may only contain letters, digits, spaces and . _ - / + : @ # (max 64)"
        + ("" if allow_empty else ", and can't be empty")
    ])
    return value


def check_path(value: str, what: str) -> str:
    problems = []
    if not value or len(value) > 2048:
        problems.append(f"{what} must be 1–2048 characters")
    elif not value.startswith(PATH_PREFIXES):
        problems.append(f"{what} must start with one of: {', '.join(PATH_PREFIXES)}")
    elif _PATH_FORBIDDEN.search(value):
        problems.append(f"{what} contains a character that isn't allowed (quotes, backslash, $, braces, control characters)")
    elif ".." in value.split("/"):
        problems.append(f"{what} can't contain '..'")
    _fail_if(problems)
    return value


def check_file_name(value: str, what: str = "file name") -> str:
    """A bare file name from a storage folder. It's pasted into an f-string in the notebook, so
    braces (which an f-string would EXECUTE) are rejected along with quotes and control characters."""
    problems = []
    if not value or len(value) > 255 or "/" in value:
        problems.append(f"{what} {value!r} must be 1–255 characters with no '/'")
    elif _PATH_FORBIDDEN.search(value) or value in {".", ".."}:
        problems.append(f"{what} {value!r} contains a character that isn't allowed (quotes, backslash, $, braces, control characters)")
    _fail_if(problems)
    return value


def check_delimiter(value: str) -> str:
    _fail_if([] if DELIMITER.match(value or "") else ["delimiter must be 1–4 characters and can't include quotes, backslashes, braces or line breaks"])
    return value


def check_row_tag(value: str) -> str:
    _fail_if([] if ROW_TAG.match(value or "") else [f"row tag {value!r} isn't a valid XML element name"])
    return value


def check_job_name(value: str) -> str:
    _fail_if([] if JOB_NAME.match(value or "") else [
        "job name must start with a letter or digit and use only letters, digits, spaces, '_', '.' and '-' (max 100)"
    ])
    return value


def check_workspace_path(value: str) -> str:
    problems = []
    if not WORKSPACE_PATH.match(value or "") or any(seg in (".", "..") for seg in value.split("/")):
        problems.append(f"workspace path must be inside {WORKSPACE_ROOT} (letters, digits, '_', '.', '-')")
    _fail_if(problems)
    return value


def check_cluster_id(value: str) -> str:
    _fail_if([] if CLUSTER_ID.match(value or "") else ["cluster id isn't in the expected format (like 0101-000000-abc123)"])
    return value


def check_schedule(frequency: str, time_of_day: str, day_of_week: str, timezone: str) -> None:
    problems = []
    if frequency not in {"hourly", "daily", "weekly"}:
        problems.append("schedule frequency must be hourly, daily or weekly")
    if not TIME_OF_DAY.match(time_of_day or ""):
        problems.append("schedule time must be HH:MM in 24-hour format")
    if day_of_week not in WEEKDAYS:
        problems.append("schedule day_of_week must be one of MON, TUE, WED, THU, FRI, SAT, SUN")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        problems.append(f"unknown timezone {timezone!r} (use an IANA name like America/Chicago)")
    _fail_if(problems)


def normalize_sql_type(sql_type: str) -> str | None:
    """Canonical form ('decimal(15, 2)' -> 'DECIMAL(15,2)'), or None if it isn't an allowed type."""
    canonical = re.sub(r"\s+", "", sql_type or "").upper()
    if canonical in SQL_TO_SPARK:
        return canonical
    m = DECIMAL.match(canonical)
    if m and 1 <= int(m.group(1)) <= 38 and int(m.group(2)) <= int(m.group(1)):
        return canonical
    return None


def spark_type_for(sql_type: str) -> str:
    """The Spark type class for an already-normalized SQL type. Always derived from the SQL
    type — a client-supplied spark_type is never used, so the schema and the DDL can't disagree."""
    return "DecimalType" if sql_type.startswith("DECIMAL(") else SQL_TO_SPARK[sql_type]


@dataclass
class SafeColumn:
    name: str
    sql_type: str
    spark_type: str
    nullable: bool
    struct_path: list[str] | None


def check_columns(columns: list[dict], *, source_type: str) -> list[SafeColumn]:
    """Validates a discovered/registered column list for use in generated code and DDL."""
    problems: list[str] = []
    safe: list[SafeColumn] = []
    seen: dict[str, str] = {}

    for i, col in enumerate(columns):
        name = str(col.get("name", ""))
        label = f"column {i + 1} ({name!r})"
        if not IDENTIFIER.match(name):
            problems.append(
                f"{label}: name must start with a letter or underscore and contain only letters, digits "
                f"and underscores — rename it in the source (a name like this can't be safely put into generated code)"
            )
        elif name.lower() in RESERVED_COLUMNS:
            problems.append(f"{label}: '{name}' is reserved for the lineage columns every pipeline adds")
        elif name.lower() in seen:
            problems.append(f"{label}: duplicate of '{seen[name.lower()]}' (column names are case-insensitive)")
        else:
            seen[name.lower()] = name

        sql_type = normalize_sql_type(str(col.get("sql_type", "")))
        if sql_type is None:
            problems.append(f"{label}: type {col.get('sql_type')!r} isn't a supported type")
        elif sql_type.startswith("DECIMAL(") and source_type != "cobol":
            problems.append(f"{label}: DECIMAL columns are only supported for COBOL sources")

        path = col.get("struct_path")
        if path is not None:
            if source_type != "cobol":
                problems.append(f"{label}: struct_path is only valid for COBOL sources")
            elif not isinstance(path, list) or not path or len(path) > 10 or not all(
                isinstance(p, str) and STRUCT_SEGMENT.match(p) for p in path
            ):
                problems.append(f"{label}: struct_path segments may only contain letters, digits, '_' and '-'")

        if not problems:
            safe.append(
                SafeColumn(
                    name=name,
                    sql_type=sql_type,
                    spark_type=spark_type_for(sql_type),
                    nullable=bool(col.get("nullable", True)),
                    struct_path=path,
                )
            )

    _fail_if(problems)
    return safe
