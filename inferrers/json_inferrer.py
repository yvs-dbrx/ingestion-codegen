"""
json_inferrer.py

Handles two JSON shapes, controlled by config["multiline"]:
  - False (default): NDJSON — one JSON object per line.
  - True: a single JSON document — either one object, or an array of
    objects (both handled).

Nested objects are flattened into dot-notation columns via
pandas.json_normalize (e.g. {"address": {"city": "Dallas"}} becomes an
"address.city" column, sanitized to "address_city" for the target DDL).

Known limitation, flagged deliberately rather than silently mishandled:
array-valued fields (a JSON array nested inside an object) come back from
pandas as dtype "object" containing Python lists, which this inferrer
maps to STRING rather than a proper Spark ARRAY type. That's a real gap —
exactly the kind of schema edge case the PRD's hybrid codegen design
(Section 7) flags as needing either a smarter rule or an AI-assisted
resolution step later, not something to quietly get wrong now.
"""

import json

import pandas as pd

from schema_types import ColumnSchema, map_pandas_dtype
from inferrers.base import SchemaInferrer


class JSONSchemaInferrer(SchemaInferrer):
    source_type = "json"

    def infer(self, config: dict) -> list[ColumnSchema]:
        path = config["file_path"]
        multiline = config.get("multiline", False)
        sample_rows = config.get("sample_rows", 1000)

        records = self._read_records(path, multiline, sample_rows)
        if not records:
            raise ValueError(f"No records found in {path} — is the file empty or malformed?")

        df = pd.json_normalize(records)

        columns = []
        for name, dtype in df.dtypes.items():
            has_list_values = df[name].apply(lambda v: isinstance(v, list)).any()
            if has_list_values:
                print(
                    f"  [warning] column '{name}' contains JSON arrays — mapping to "
                    f"STRING rather than a proper array type. Review before relying "
                    f"on this column downstream."
                )
                spark_type, sql_type = "StringType", "STRING"
            else:
                spark_type, sql_type = map_pandas_dtype(str(dtype))

            nullable = bool(df[name].isnull().any())
            safe_name = str(name).replace(".", "_").replace(" ", "_")
            columns.append(
                ColumnSchema(
                    name=safe_name,
                    pandas_dtype=str(dtype),
                    spark_type=spark_type,
                    sql_type=sql_type,
                    nullable=nullable,
                )
            )
        return columns

    @staticmethod
    def _read_records(path: str, multiline: bool, sample_rows: int) -> list[dict]:
        if multiline:
            with open(path) as f:
                data = json.load(f)
            return data[:sample_rows] if isinstance(data, list) else [data]

        records = []
        with open(path) as f:
            for i, line in enumerate(f):
                if i >= sample_rows:
                    break
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
