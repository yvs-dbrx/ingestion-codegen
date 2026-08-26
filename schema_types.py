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
