"""Runs the GENERATED notebooks end to end on a real local Spark + Delta (see the Dockerfile).

A generated notebook is code that will run in a customer's workspace with their credentials, so
"it renders" isn't evidence it works. This renders each notebook exactly as the platform does,
executes it for real (with a stand-in for Databricks' `dbutils`), and checks the results against an
oracle computed independently in plain Python.

    docker run --rm -v <repo>:/repo:ro refinaire-spark-e2e python /repo/saas/backend/tests/spark_e2e/run_e2e.py

Exit status is non-zero if anything fails. Not run by pytest (needs a JVM); run it whenever a
template changes.
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(os.environ.get("REPO", "/repo"))
sys.path.insert(0, str(REPO))

import codegen  # noqa: E402  (the engine, exactly as the backend imports it)
from schema_types import ColumnSchema, TableSpec  # noqa: E402

from delta import configure_spark_with_delta_pip  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="refinaire_e2e_"))
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# --- a stand-in for the bits of Databricks the notebooks touch -----------------------------

class NotebookExit(Exception):
    pass


class _Widgets:
    def __init__(self, given):
        self.values = dict(given)  # what a job's base_parameters would supply wins over defaults

    def text(self, name, default=""):
        self.values.setdefault(name, default)

    def get(self, name):
        return self.values[name]


class _Notebook:
    def exit(self, value):
        raise NotebookExit(value)


class FakeDbutils:
    def __init__(self, widgets):
        self.widgets = _Widgets(widgets)
        self.notebook = _Notebook()


def run_notebook(spark, path, widgets=None) -> dict:
    ns = {"spark": spark, "dbutils": FakeDbutils(widgets or {}), "__name__": "__notebook__"}
    try:
        exec(compile(Path(path).read_text(encoding="utf-8"), str(path), "exec"), ns)
    except NotebookExit as done:
        return json.loads(str(done))
    raise AssertionError(f"{path} finished without calling dbutils.notebook.exit")


def make_spark():
    builder = (
        SparkSession.builder.master("local[2]")
        .appName("refinaire-e2e")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.warehouse.dir", str(WORK / "warehouse"))
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        # Databricks writes Delta unless told otherwise; open-source Spark defaults to Parquet.
        .config("spark.sql.sources.default", "delta")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def col(name, spark_type, sql_type, nullable=True):
    return ColumnSchema(name=name, pandas_dtype="", spark_type=spark_type, sql_type=sql_type, nullable=nullable, struct_path=None)


CSV_COLS = [
    col("id", "LongType", "BIGINT", False),
    col("name", "StringType", "STRING"),
    col("city", "StringType", "STRING"),
    col("balance", "DoubleType", "DOUBLE"),
]
CSV_CFG = {"location": "local", "delimiter": ",", "has_header": True, "multiline": False, "row_tag": "record"}


def write_csv(name, rows):
    path = WORK / "landing" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id,name,city,balance\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def render_raw(table, source):
    out = WORK / "nb" / f"raw_{table}.py"
    codegen.render_notebook("csv", {**CSV_CFG, "file_path": source}, "spark_catalog", "raw", table, CSV_COLS, str(out))
    return out


def render_bronze(tables):
    out = WORK / "nb" / "bronze.py"
    codegen.render_bronze_notebook(tables, str(out))
    return out


def bronze_spec(name, columns):
    return {"raw": f"spark_catalog.raw.{name}", "bronze": f"spark_catalog.bronze.{name}", "name": name, "columns": columns}


# --- scenarios -----------------------------------------------------------------------------

def scenario_csv_raw_then_bronze(spark):
    print("\n1. Raw CSV loaded repeatedly, then Bronze")
    v1 = write_csv("customers_v1.csv", ["1,Ada,London,10.5", "2,Bob,Paris,20", "3,Cy,,30"])
    raw_nb = render_raw("customers", v1)

    m1 = run_notebook(spark, raw_nb, {"batch_id": "L1"})
    t = m1["tables"]["customers"]
    check("raw reports its counts", (m1["layer"], t["rows_read"], t["rows_written"], t["rows_in_target"]) == ("raw", 3, 3, 3), str(m1))
    m2 = run_notebook(spark, raw_nb, {"batch_id": "L2"})
    check("raw appends the same file again (duplicates kept)", m2["tables"]["customers"]["rows_in_target"] == 6, str(m2))

    cols = [("id", "BIGINT"), ("name", "STRING"), ("city", "STRING"), ("balance", "DOUBLE")]
    bronze_nb = render_bronze([bronze_spec("customers", cols)])
    b1 = run_notebook(spark, bronze_nb)
    r = b1["tables"]["customers"]
    check("bronze: duplicates collapse to one copy", (r["rows_read"], r["duplicates_removed"], r["distinct_records"]) == (6, 3, 3), str(r))
    check("bronze: everything is new on the first run", (r["rows_inserted"], r["already_in_target"], r["rows_in_target"]) == (3, 0, 3), str(r))
    check("bronze: counts reconcile", b1["reconciled"] is True and r["reconciled"] is True)
    first_seen = {row["_batch_id"] for row in spark.table("spark_catalog.bronze.customers").collect()}
    check("bronze keeps the FIRST-seen lineage", first_seen == {"L1"}, str(first_seen))

    b2 = run_notebook(spark, bronze_nb)["tables"]["customers"]
    check("bronze is idempotent: a re-run inserts nothing", (b2["rows_inserted"], b2["already_in_target"], b2["rows_in_target"]) == (0, 3, 3), str(b2))

    v2 = write_csv("customers_v2.csv", ["1,Ada,London,10.5", "2,Bob,Paris,25", "3,Cy,,30", "4,Di,Rome,40"])
    run_notebook(spark, render_raw("customers", v2), {"batch_id": "L3"})
    b3 = run_notebook(spark, bronze_nb)["tables"]["customers"]
    check(
        "bronze: a changed record is a new version, a new record is added, unchanged ones are not repeated",
        (b3["rows_read"], b3["distinct_records"], b3["rows_inserted"], b3["already_in_target"], b3["rows_in_target"]) == (10, 5, 2, 3, 5),
        str(b3),
    )
    versions = sorted(r["balance"] for r in spark.table("spark_catalog.bronze.customers").where("id = 2").collect())
    check("bronze keeps every version of a changed record", versions == [20.0, 25.0], str(versions))
    # Insert-only is a promise about the table itself, so check it in the table's own Delta history:
    # across every MERGE that ever ran, not one existing row was updated or deleted.
    merges = [h["operationMetrics"] for h in spark.sql("DESCRIBE HISTORY spark_catalog.bronze.customers").collect() if h["operation"] == "MERGE"]
    check(
        "bronze never updates or deletes an existing row (read from the table's Delta history)",
        len(merges) >= 2 and all(int(m.get("numTargetRowsUpdated", "0")) == 0 and int(m.get("numTargetRowsDeleted", "0")) == 0 for m in merges),
        str(merges),
    )
    dupes = spark.table("spark_catalog.bronze.customers").groupBy("_record_hash").count().where("count > 1").count()
    check("bronze never holds the same record twice", dupes == 0)

    # an independent oracle: distinct tuples of the source columns, computed in plain Python
    raw_rows = [tuple(r[c] for c in ("id", "name", "city", "balance")) for r in spark.table("spark_catalog.raw.customers").collect()]
    check("bronze row count equals an independent distinct count", len(set(raw_rows)) == spark.table("spark_catalog.bronze.customers").count())

    # --- sightings: which loads each record appeared in ---
    check(
        "sightings: first run recorded one per (record, load); re-run added none; third load added its own",
        (b1["tables"]["customers"]["sightings_inserted"], b2["sightings_inserted"], b3["sightings_inserted"], b3["sightings_in_target"]) == (6, 0, 4, 10),
        str((b1["tables"]["customers"], b2, b3)),
    )
    sight = "spark_catalog.bronze.customers_sightings"
    dup_sightings = spark.table(sight).groupBy("_record_hash", "_batch_id").count().where("count > 1").count()
    check("sightings never hold the same (record, load) twice", dup_sightings == 0)
    check(
        "every sighting points at a record that exists in Bronze",
        spark.sql(f"SELECT 1 FROM {sight} s LEFT ANTI JOIN spark_catalog.bronze.customers b ON s._record_hash = b._record_hash").count() == 0,
    )
    sm = [h["operationMetrics"] for h in spark.sql(f"DESCRIBE HISTORY {sight}").collect() if h["operation"] == "MERGE"]
    check(
        "sightings are insert-only too (Delta history)",
        len(sm) >= 2 and all(int(m.get("numTargetRowsUpdated", "0")) == 0 and int(m.get("numTargetRowsDeleted", "0")) == 0 for m in sm),
        str(sm),
    )

    # Balance for customer 2 went 20 -> 25; now it goes back to 20. Bronze holds two versions (no new
    # row), but the sightings must say that 20 is the one seen most recently.
    # (render_raw reuses one output path, so the v1 notebook has to be rendered again)
    run_notebook(spark, render_raw("customers", v1), {"batch_id": "L4"})
    b4 = run_notebook(spark, bronze_nb)["tables"]["customers"]
    check("revert: no new Bronze row, but the load is recorded", (b4["rows_inserted"], b4["sightings_inserted"]) == (0, 3), str(b4))
    latest = spark.sql(f"""
        SELECT b.balance FROM spark_catalog.bronze.customers b JOIN {sight} s USING (_record_hash)
        WHERE b.id = 2 ORDER BY s._ingested_at DESC, s._batch_id DESC LIMIT 1
    """).first()["balance"]
    check("revert: the most recently seen version of customer 2 is 20 again", latest == 20.0, str(latest))


def scenario_types_and_edges(spark):
    print("\n2. Every column type, NULL vs empty, near-collisions, time zones")
    spark.sql("""
        CREATE TABLE spark_catalog.raw.typed (
            i INT, l BIGINT, d DECIMAL(9,2), dt DATE, ts TIMESTAMP, b BOOLEAN, f DOUBLE, bin BINARY,
            s STRING, s2 STRING,
            _ingested_at TIMESTAMP, _source_file STRING, _batch_id STRING
        ) USING DELTA
    """)
    ts = datetime(2026, 3, 1, 12, 30, 45, 123456, tzinfo=timezone.utc)
    base = (1, 10**12, Decimal("1.50"), date(2026, 3, 1), ts, True, 2.5, bytearray(b"\x00\xffab"), "x", "y")
    rows = [
        base,
        base,  # exact duplicate
        (1, 10**12, Decimal("1.5"), date(2026, 3, 1), ts, True, 2.5, bytearray(b"\x00\xffab"), "x", "y"),  # same decimal value
        (2, None, None, None, None, None, None, None, "", "z"),       # empty string ...
        (2, None, None, None, None, None, None, None, None, "z"),     # ... is not NULL
        (3, None, None, None, None, None, None, None, "ab", "c"),     # ("ab","c") ...
        (3, None, None, None, None, None, None, None, "a", "bc"),     # ... is not ("a","bc")
        (5, None, None, None, None, None, None, None, "a|Vb", "c"),   # data that CONTAINS the separator: without
        (5, None, None, None, None, None, None, None, "a", "b|Vc"),   # length prefixes these two would hash alike
        (4, None, None, None, None, None, None, None, "x ", "y"),     # trailing space is data
        (4, None, None, None, None, None, None, None, "x", "y"),
        (None,) * 10,
        (None,) * 10,
        (1, 10**12, Decimal("1.50"), date(2026, 3, 1), ts.replace(microsecond=123457), True, 2.5, bytearray(b"\x00\xffab"), "x", "y"),  # 1 microsecond apart
    ]
    lineage = [(datetime(2026, 3, 1, 0, 0, i, tzinfo=timezone.utc), f"f{i}", f"B{i}") for i in range(len(rows))]
    df = spark.createDataFrame([r + l for r, l in zip(rows, lineage)], schema=spark.table("spark_catalog.raw.typed").schema)
    df.write.mode("append").saveAsTable("spark_catalog.raw.typed")

    cols = [("i", "INT"), ("l", "BIGINT"), ("d", "DECIMAL(9,2)"), ("dt", "DATE"), ("ts", "TIMESTAMP"),
            ("b", "BOOLEAN"), ("f", "DOUBLE"), ("bin", "BINARY"), ("s", "STRING"), ("s2", "STRING")]
    nb = render_bronze([bronze_spec("typed", cols)])
    m = run_notebook(spark, nb)["tables"]["typed"]

    def key(r):  # the oracle: Python's own notion of "the same record"
        return tuple(bytes(v) if isinstance(v, bytearray) else v for v in r)

    expected = len({key(r) for r in rows})
    check("typed: Bronze distinct count matches the oracle", m["distinct_records"] == expected == m["rows_in_target"],
          f"got {m['distinct_records']}/{m['rows_in_target']}, oracle {expected}")
    check("typed: only exact duplicates were removed", m["duplicates_removed"] == len(rows) - expected, str(m))

    spark.conf.set("spark.sql.session.timeZone", "Asia/Tokyo")
    again = run_notebook(spark, nb)["tables"]["typed"]
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    check("typed: a different session time zone does not change any record's identity", again["rows_inserted"] == 0, str(again))

    spark.sql("ALTER TABLE spark_catalog.raw.typed ADD COLUMN extra STRING")
    extra = run_notebook(spark, nb)["tables"]["typed"]
    check("a column added to Raw later is reported, not silently compared",
          extra["rows_inserted"] == 0 and extra["extra_source_columns"] == ["extra"], str(extra))

    try:
        run_notebook(spark, render_bronze([bronze_spec("typed", cols + [("gone", "STRING")])]))
        check("a column that vanished from Raw fails loudly", False, "notebook completed")
    except ValueError as exc:
        check("a column that vanished from Raw fails loudly", "gone" in str(exc), str(exc))


def scenario_multi_table_bronze(spark):
    print("\n3. One Bronze notebook over several Raw tables")
    for name in ("orders", "items"):
        run_notebook(spark, render_raw(name, write_csv(f"{name}.csv", ["1,a,b,1.5", "2,c,d,2.5"])), {"batch_id": "M1"})
        run_notebook(spark, render_raw(name, write_csv(f"{name}.csv", ["1,a,b,1.5", "2,c,d,2.5"])), {"batch_id": "M2"})
    cols = [("id", "BIGINT"), ("name", "STRING"), ("city", "STRING"), ("balance", "DOUBLE")]
    out = run_notebook(spark, render_bronze([bronze_spec("orders", cols), bronze_spec("items", cols)]))
    check("both tables processed independently", set(out["tables"]) == {"orders", "items"}
          and all(t["rows_in_target"] == 2 and t["duplicates_removed"] == 2 for t in out["tables"].values()), str(out))


def scenario_other_raw_templates(spark):
    print("\n4. The other Raw templates report their counts and still compile")
    d = WORK / "multi"
    d.mkdir(exist_ok=True)
    for name in ("a", "b"):
        (d / f"{name}.csv").write_text("id,name,city,balance\n1,x,y,1.0\n2,z,w,2.0\n", encoding="utf-8")
    tables = [TableSpec(table=n, file_name=f"{n}.csv", source_path=str(d / f"{n}.csv"), columns=CSV_COLS) for n in ("a", "b")]
    out = WORK / "nb" / "multi.py"
    codegen.render_multi_notebook("csv", {**CSV_CFG, "file_path": str(d)}, "spark_catalog", "raw", tables, str(out))
    res = run_notebook(spark, out, {"batch_id": "X1"})
    check("multi-file Raw reports every table", res["layer"] == "raw" and set(res["tables"]) == {"a", "b"}
          and all(t["rows_written"] == 2 for t in res["tables"].values()), str(res))

    # COBOL needs the Cobrix library, which a local Spark doesn't have: render and compile only
    cob = WORK / "nb" / "cobol.py"
    cols = [ColumnSchema(name="CUST_ID", pandas_dtype="", spark_type="IntegerType", sql_type="INT", nullable=True, struct_path=["CUST_ID"])]
    codegen.render_cobol_notebook(copybook_path="/v/c.cpy", source_path="/v/c.dat", catalog="main", target_schema="raw", table="cust",
                                  columns=cols, output_path=str(cob), is_record_sequence=False, is_text=False)
    src = cob.read_text(encoding="utf-8")
    compile(src, "cobol", "exec")
    check("COBOL notebook compiles and reports counts", "dbutils.notebook.exit" in src and "import json" in src)
    for kind in ("json", "xml"):
        p = WORK / "nb" / f"{kind}.py"
        codegen.render_notebook(kind, {**CSV_CFG, "file_path": "/v/x"}, "main", "raw", "t", CSV_COLS, str(p))
        compile(p.read_text(encoding="utf-8"), kind, "exec")
        check(f"{kind} notebook compiles and reports counts", "dbutils.notebook.exit" in p.read_text(encoding="utf-8"))


# --- Silver --------------------------------------------------------------------------------

RAW_DDL = (
    "cust_id INT, cust_name STRING, birth INT, status STRING, cat_code STRING, cat_desc STRING, "
    "_ingested_at TIMESTAMP, _source_file STRING, _batch_id STRING"
)
SRC_COLS = ["cust_id", "cust_name", "birth", "status", "cat_code", "cat_desc"]


def load_raw(spark, batch_no, rows):
    """Append one load to a Raw table exactly as the Raw notebooks do: every row, with lineage."""
    stamp = datetime(2026, 1, batch_no, tzinfo=timezone.utc)
    data = [(*r, stamp, f"file{batch_no}.csv", f"L{batch_no}") for r in rows]
    df = spark.createDataFrame(data, RAW_DDL)
    if not spark.catalog.tableExists("spark_catalog.raw.cust"):
        df.write.format("delta").saveAsTable("spark_catalog.raw.cust")
    else:
        df.write.format("delta").mode("append").saveAsTable("spark_catalog.raw.cust")


def silver_spec(master=True):
    customer = {
        "name": "customer", "target": "spark_catalog.silver.customer", "quarantine": "spark_catalog.silver.customer_quarantine",
        "business_key": ["cust_id"],
        "attributes": [
            {"name": "cust_id", "from": "cust_id", "type": "INT", "required": True, "steps": []},
            {"name": "full_name", "from": "cust_name", "type": "STRING", "required": True,
             "steps": [{"kind": "trim"}, {"kind": "null_if", "values": [""]}, {"kind": "upper"}]},
            {"name": "birth_date", "from": "birth", "type": "DATE", "required": False,
             "steps": [{"kind": "null_if", "values": ["0"]}, {"kind": "parse_date", "format": "yyyyMMdd"}]},
            {"name": "status", "from": "status", "type": "STRING", "required": False, "steps": [{"kind": "trim"}, {"kind": "upper"}]},
        ],
        "master_key": {
            "column": "customer_mk", "crosswalk": "spark_catalog.ref.xwalk",
            "system_column": "src_sys", "key_column": "src_key", "master_column": "mk",
            "entity_type_column": "etype", "entity_type": "customer",
        } if master else None,
    }
    category = {
        "name": "category", "target": "spark_catalog.silver.category", "quarantine": "spark_catalog.silver.category_quarantine",
        "business_key": ["cat_code"],
        "attributes": [
            {"name": "cat_code", "from": "cat_code", "type": "STRING", "required": True, "steps": [{"kind": "trim"}, {"kind": "upper"}]},
            {"name": "cat_desc", "from": "cat_desc", "type": "STRING", "required": False, "steps": [{"kind": "trim"}]},
        ],
        "master_key": None,
    }
    return {
        "source": {"bronze": "spark_catalog.bronze.cust", "system": "CRM"},
        "unknown_master_key": "UNKNOWN",
        "entities": [customer, category],
    }


def render_silver(spec):
    out = WORK / "nb" / "silver.py"
    codegen.render_silver_notebook(spec, str(out))
    return out


def py_birth(value):
    if value is None or str(value) == "0":
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return "BAD"


def oracle_customers(loads, crosswalk):
    """Plain-Python Silver for the customer entity: of a key's versions, the one seen in the latest
    load wins; a key whose current version is bad is left out (Silver keeps whatever it already had,
    which in these scenarios is never anything for such a key)."""
    seen = {}  # (key, record) -> last load it appeared in
    for batch_no, rows in loads:
        for r in rows:
            if r[0] is not None:
                seen[(r[0], r)] = batch_no
    latest = {}
    for (key, rec), last in seen.items():
        if key not in latest or last > latest[key][0]:
            latest[key] = (last, rec)
    current = set()
    for key, (_, rec) in latest.items():
        name = (rec[1] or "").strip().upper() or None
        birth = py_birth(rec[2])
        if name is None or birth == "BAD":
            continue
        current.add((key, name, birth, (rec[3] or "").strip().upper(), crosswalk.get(str(key), "UNKNOWN")))
    return current


def scenario_silver(spark):
    print("\n5. Silver: current version per key, transforms, quarantine, master keys")
    spark.sql("CREATE SCHEMA IF NOT EXISTS spark_catalog.silver")
    spark.sql("CREATE SCHEMA IF NOT EXISTS spark_catalog.ref")
    spark.sql("CREATE TABLE spark_catalog.ref.xwalk (etype STRING, src_sys STRING, src_key STRING, mk STRING) USING DELTA")
    spark.sql("""INSERT INTO spark_catalog.ref.xwalk VALUES
        ('customer','CRM','1','M-100'), ('customer','CRM','1','M-100'), ('customer','CRM','2','M-200'), ('customer','OTHER','3','M-X'),
        ('product','CRM','1','P-900'), ('product','CRM','3','P-903')""")
    cols = list(zip(SRC_COLS, ["INT", "STRING", "INT", "STRING", "STRING", "STRING"]))
    bronze_nb = render_bronze([bronze_spec("cust", cols)])
    silver_nb = render_silver(silver_spec())

    loads = []

    def load(batch_no, rows):
        loads.append((batch_no, rows))
        load_raw(spark, batch_no, rows)
        run_notebook(spark, bronze_nb)

    def silver():
        return run_notebook(spark, silver_nb)

    def table(name, *cols):
        return {tuple(r[c] for c in cols) for r in spark.table(f"spark_catalog.silver.{name}").collect()}

    full = ("cust_id", "full_name", "birth_date", "status", "customer_mk")

    # load 1
    load(1, [
        (1, "  ada ", 19800131, "A", "E", "East"),
        (2, "BOB", 0, "A", "W", "West"),
        (3, "cy", 19991301, "I", "E", "East"),       # impossible date -> can't convert
        (None, "nokey", 20000101, "A", "E", "East"),  # no key
        (4, "", 19900505, "A", "E", "East"),          # required name is empty
    ])
    m = silver()
    c = m["tables"]["customer"]
    check("silver: first run counts",
          (c["distinct_keys"], c["quarantined_keys"], c["null_key_records"], c["rows_inserted"], c["rows_updated"], c["unchanged"], c["rows_in_target"])
          == (4, 2, 1, 2, 0, 0, 2) and m["reconciled"] is True, str(c))
    check("silver: values are trimmed, upper-cased, dates parsed, zero date -> NULL",
          table("customer", "cust_id", "full_name", "birth_date", "status")
          == {(1, "ADA", date(1980, 1, 31), "A"), (2, "BOB", None, "A")})
    crosswalk = {"1": "M-100", "2": "M-200"}
    check("silver: master keys looked up; a repeated identical mapping is harmless",
          table("customer", "cust_id", "customer_mk", "_master_key_resolved") == {(1, "M-100", True), (2, "M-200", True)})
    q = {r["_record_hash"]: r["_reasons"] for r in spark.table("spark_catalog.silver.customer_quarantine").collect()}
    check("silver: quarantine holds the bad-date, empty-name and keyless records", len(q) == 3, str(q))
    check("silver: quarantine says why",
          any("birth_date" in r and "converted" in r for r in q.values()) and any("full_name" in r and "empty" in r for r in q.values())
          and any("cust_id" in r for r in q.values()), str(q))
    check("silver: the independent oracle agrees", oracle_customers(loads, crosswalk) == table("customer", *full))

    again = silver()["tables"]["customer"]
    check("silver is idempotent: a re-run changes nothing",
          (again["rows_inserted"], again["rows_updated"], again["unchanged"], again["rows_in_target"]) == (0, 0, 2, 2), str(again))
    merges = [h["operationMetrics"] for h in spark.sql("DESCRIBE HISTORY spark_catalog.silver.customer").collect() if h["operation"] == "MERGE"]
    check("silver: the re-run's MERGE rewrote no row (Delta history)",
          int(merges[0].get("numTargetRowsUpdated", "0")) == 0 and int(merges[0].get("numTargetRowsInserted", "0")) == 0, str(merges[0]))

    # load 2: customer 1's status changes A -> I
    load(2, [(1, "  ada ", 19800131, "I", "E", "East")])
    c = silver()["tables"]["customer"]
    check("silver: a changed value updates the existing row", (c["rows_inserted"], c["rows_updated"], c["unchanged"]) == (0, 1, 1), str(c))
    check("silver: the new status is current", ("ADA", "I") in table("customer", "full_name", "status"))

    # load 3: customer 1 changes BACK to A, customer 3 is corrected, customer 5 is new
    # with a category description that conflicts with the others for the same category
    load(3, [
        (1, "  ada ", 19800131, "A", "E", "East"),
        (3, "cy", 19991201, "I", "E", "East"),
        (5, "dee", 19900101, "A", "E", "Eastern"),
    ])
    c = silver()["tables"]["customer"]
    statuses = table("customer", "full_name", "status")
    check("silver: A -> I -> A ends on A (most recently seen wins, not first seen)",
          ("ADA", "A") in statuses and ("ADA", "I") not in statuses)
    check("silver: a corrected record and a new key are inserted; the still-bad key stays quarantined",
          (c["distinct_keys"], c["quarantined_keys"], c["rows_inserted"], c["rows_updated"], c["unchanged"], c["rows_in_target"]) == (5, 1, 2, 1, 1, 4), str(c))
    check("silver: keys with no mapping get the unknown member and are flagged",
          table("customer", "cust_id", "customer_mk", "_master_key_resolved") >= {(3, "UNKNOWN", False), (5, "UNKNOWN", False)}
          and c["unmapped_keys"] == 2, str(c))
    check("silver: the oracle still agrees after three loads", oracle_customers(loads, crosswalk) == table("customer", *full))
    qn = spark.table("spark_catalog.silver.customer_quarantine")
    check("silver: quarantine never holds the same record twice across runs", qn.count() == qn.dropDuplicates(["_record_hash"]).count() == 3, str(qn.count()))

    cat = run_notebook(spark, silver_nb)["tables"]["category"]
    check("silver: two descriptions for one key in the same load are reported as a conflict", cat["conflicting_keys"] == 1, str(cat))
    check("silver: the category entity has one row per key (a 3NF split of the same Bronze table)",
          table("category", "cat_code") == {("E",), ("W",)})
    d1 = table("category", "cat_code", "cat_desc")
    silver()
    check("silver: a conflict resolves the same way on every run", table("category", "cat_code", "cat_desc") == d1)

    # the crosswalk gains a mapping: that key is re-resolved by the next run and nothing else changes
    spark.sql("INSERT INTO spark_catalog.ref.xwalk VALUES ('customer','CRM','3','M-300')")
    crosswalk["3"] = "M-300"
    c = silver()["tables"]["customer"]
    check("silver: a new crosswalk mapping re-resolves just that key",
          (c["rows_updated"], c["rows_inserted"], c["unmapped_keys"]) == (1, 0, 1)
          and (3, "M-300", True) in table("customer", "cust_id", "customer_mk", "_master_key_resolved"), str(c))
    check("silver: the oracle agrees with the new crosswalk", oracle_customers(loads, crosswalk) == table("customer", *full))
    check("silver: the crosswalk's size and version are reported",
          c["crosswalk_rows"] == 3 and isinstance(c.get("crosswalk_version"), int), str(c))

    # an ambiguous crosswalk must stop the run rather than pick a master key
    before = table("customer", "cust_id", "customer_mk")
    spark.sql("INSERT INTO spark_catalog.ref.xwalk VALUES ('customer','CRM','2','M-999')")
    try:
        silver()
        failed = False
    except ValueError as exc:
        failed = "more than one master key" in str(exc)
    check("silver: a source key mapped to two master keys fails the run loudly", failed)
    check("silver: ...and leaves Silver untouched", table("customer", "cust_id", "customer_mk") == before)
    spark.sql("DELETE FROM spark_catalog.ref.xwalk WHERE mk = 'M-999'")
    check("silver: runs again once the crosswalk is fixed", silver()["tables"]["customer"]["rows_updated"] == 0)

    # another system's mappings are never used
    spark.sql("INSERT INTO spark_catalog.ref.xwalk VALUES ('customer','OTHER','5','M-OTHER-5')")
    silver()
    check("silver: a mapping for a different source system is ignored",
          (5, "UNKNOWN", False) in table("customer", "cust_id", "customer_mk", "_master_key_resolved"))

    # a source column that vanished fails loudly
    bad = silver_spec()
    bad["entities"][0]["attributes"][1]["from"] = "no_such_column"
    try:
        run_notebook(spark, render_silver(bad))
        loud = False
    except ValueError as exc:
        loud = "no longer has column" in str(exc)
    check("silver: a missing source column fails loudly", loud)



def main() -> int:
    only = [t for t in os.environ.get("E2E_ONLY", "").split(",") if t]  # e.g. E2E_ONLY=types,csv_raw while debugging
    spark = make_spark()
    try:
        spark.sql("CREATE SCHEMA IF NOT EXISTS spark_catalog.raw")
        spark.sql("CREATE SCHEMA IF NOT EXISTS spark_catalog.bronze")
        for scenario in (scenario_csv_raw_then_bronze, scenario_types_and_edges, scenario_multi_table_bronze, scenario_other_raw_templates, scenario_silver):
            if only and not any(t in scenario.__name__ for t in only):
                continue
            try:
                scenario(spark)
            except Exception:  # a crash is a failure, not a reason to hide the rest
                traceback.print_exc()
                FAILURES.append(f"{scenario.__name__} crashed")
    finally:
        spark.stop()
        shutil.rmtree(WORK, ignore_errors=True)
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
