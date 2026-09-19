"""
schema_types.py

Shared schema representation used by every source inferrer and by the
codegen templates. Keeping this format source-agnostic is what lets JSON,
Parquet, COBOL copybook, and XML inferrers plug in later without touching
the codegen engine itself.
"""

from dataclasses import dataclass


@dataclass
class ColumnSchema:
    name: str
    pandas_dtype: str   # as reported by pandas, e.g. "int64", "object"
    spark_type: str      # PySpark type class name, e.g. "StringType"
    sql_type: str         # Databricks SQL DDL type, e.g. "STRING"
    nullable: bool = True
    # COBOL only: original nested-field path (e.g. ["CUST-NAME", "FIRST-NAME"])
    # for a leaf column that came from flattening a COBOL group item — Cobrix
    # maps group items (level 05 with child level-10s) to Spark StructType
    # columns, not flat scalars, so the deployed notebook needs this to
    # rebuild a .getField() chain pulling the leaf value out. None for every
    # other format, and for COBOL fields that were never nested to begin with.
    struct_path: list[str] | None = None


@dataclass
class TableSpec:
    """
    One file/table within a multi-file ingestion job (one folder, one
    notebook, one job — see codegen.infer_multi_schema/render_multi_notebook).
    file_name is deliberately just the basename, not a full path: the
    generated notebook joins it onto a single job-level source_folder
    parameter at run time, so redeploying against a different folder
    doesn't require regenerating the notebook.
    """
    table: str
    file_name: str
    source_path: str  # the path actually read at discovery time — informational (UI display), not used by the template
    columns: list[ColumnSchema]


# pandas dtype -> (PySpark type class, Databricks SQL DDL type)
PANDAS_TO_SPARK = {
    "int64": ("LongType", "BIGINT"),
    "int32": ("IntegerType", "INT"),
    "float64": ("DoubleType", "DOUBLE"),
    "float32": ("FloatType", "FLOAT"),
    "bool": ("BooleanType", "BOOLEAN"),
    "datetime64[ns]": ("TimestampType", "TIMESTAMP"),
    "object": ("StringType", "STRING"),  # pandas < 3.0 string columns
    "str": ("StringType", "STRING"),      # pandas >= 3.0 string columns
}

DEFAULT_SPARK_TYPE = ("StringType", "STRING")  # safe fallback for unknown dtypes


def map_pandas_dtype(pandas_dtype: str) -> tuple[str, str]:
    """Returns (spark_type_class_name, sql_ddl_type) for a pandas dtype string."""
    if pandas_dtype not in PANDAS_TO_SPARK:
        print(
            f"  [warning] unrecognized pandas dtype '{pandas_dtype}' — "
            f"defaulting to STRING. Add it to PANDAS_TO_SPARK in "
            f"schema_types.py if that's wrong for this column."
        )
    return PANDAS_TO_SPARK.get(pandas_dtype, DEFAULT_SPARK_TYPE)
