"""
csv_inferrer.py

Reads a sample of a CSV file with pandas and infers column names + types.
Only reads `sample_rows` rows (default 1000) — enough for reliable type
inference without pulling a potentially huge file into memory. In the
full SaaS this sample read happens against object storage (S3/ADLS/GCS);
here it just reads a local path, since the goal right now is proving the
inference → codegen flow works, not building the full connector.
"""

import pandas as pd

from schema_types import ColumnSchema, map_pandas_dtype
from inferrers.base import SchemaInferrer


class CSVSchemaInferrer(SchemaInferrer):
    source_type = "csv"

    def infer(self, config: dict) -> list[ColumnSchema]:
        path = config["file_path"]
        delimiter = config.get("delimiter", ",")
        has_header = config.get("has_header", True)
        sample_rows = config.get("sample_rows", 1000)

        df = pd.read_csv(
            path,
            delimiter=delimiter,
            header=0 if has_header else None,
            nrows=sample_rows,
        )

        if not has_header:
            df.columns = [f"col_{i}" for i in range(len(df.columns))]

        columns = []
        for name, dtype in df.dtypes.items():
            spark_type, sql_type = map_pandas_dtype(str(dtype))
            # Nullable if any null was seen in the sample. Note this is a
            # sample-based check (only `sample_rows` rows were read), so a
            # column that happens to have no nulls in the sample could
            # still contain nulls elsewhere in the full file — treat this
            # as a best-effort signal, not a guarantee.
            nullable = bool(df[name].isnull().any())
            columns.append(
                ColumnSchema(
                    name=str(name).strip().replace(" ", "_"),
                    pandas_dtype=str(dtype),
                    spark_type=spark_type,
                    sql_type=sql_type,
                    nullable=nullable,
                )
            )
        return columns
