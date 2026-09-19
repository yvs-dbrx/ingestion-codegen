"""
xml_inferrer.py

Handles the flat/shallow XML shape: repeated row elements as direct
children of a root, each row's own direct children holding scalar text
values — e.g.:

    <customers>
      <record><customer_id>1001</customer_id><first_name>Jane</first_name>...</record>
      <record>...</record>
    </customers>

The repeated row element's tag ("record" above) is a required config
field (config["row_tag"]), not auto-detected — same pattern as CSV's
delimiter or JSON's multiline: it's "how to parse this format" input the
caller already supplies, not something worth threading discovered
metadata back through the SchemaInferrer interface for. If the given tag
doesn't match anything, the error message reports what tags *were* found
at that level, so picking the right one is a quick fix, not a guess.

Known limitations, flagged deliberately rather than silently mishandled:
  - XML attributes are ignored — only child-element text becomes a
    column. Attribute-heavy XML isn't this shape (see json_inferrer.py's
    array-column gap for the equivalent kind of documented limitation).
  - Nested (non-scalar) child elements aren't flattened — their text is
    whatever ElementTree gives back for a non-leaf element (usually
    None), which will show up as a very sparse/empty column, a visible
    signal something needs a different approach, not a silent wrong
    answer.
  - Only reads a sample (config.get("sample_rows", 1000)) via streaming
    iterparse + elem.clear(), not the whole file — same discipline as
    CSV/JSON's sample-only reads.

Only local files for now — unlike DatabricksRemoteInferrer's CSV/JSON
support, there's no remote schema-probe template for XML yet. A
Databricks Volume/S3 XML *source* still works fine for the *deployed
job* (the generated notebook reads it via Spark at run time); this gap
is specifically about *discovering* the schema from a file that only
exists remotely.
"""

import xml.etree.ElementTree as ET

import pandas as pd

from schema_types import ColumnSchema, map_pandas_dtype
from inferrers.base import SchemaInferrer

DEFAULT_ROW_TAG = "record"


def _strip_namespace(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _coerce_column(series: pd.Series) -> pd.Series:
    """
    XML text is always a string (or None) at the ElementTree level, so
    every column starts out as STRING — this recovers int/float/bool the
    same way a human skimming the file would, without attempting date
    parsing (matching CSV's own documented "dates come back as STRING"
    limitation).
    """
    non_null = series.dropna()
    if not non_null.empty and non_null.str.lower().isin(["true", "false"]).all():
        return series.str.lower().map({"true": True, "false": False})
    try:
        return pd.to_numeric(series)
    except (ValueError, TypeError):
        return series


class XMLSchemaInferrer(SchemaInferrer):
    source_type = "xml"

    def infer(self, config: dict) -> list[ColumnSchema]:
        path = config["file_path"]
        row_tag = config.get("row_tag") or DEFAULT_ROW_TAG
        sample_rows = config.get("sample_rows", 1000)

        records, seen_tags = self._read_records(path, row_tag, sample_rows)
        if not records:
            raise ValueError(
                f"No <{row_tag}> elements found as direct children of the root in {path}. "
                f"Tags found at that level: {sorted(seen_tags) or 'none'}. "
                f"Set the row tag to match one of those."
            )

        df = pd.DataFrame(records)
        for col in df.columns:
            df[col] = _coerce_column(df[col])

        columns = []
        for name, dtype in df.dtypes.items():
            spark_type, sql_type = map_pandas_dtype(str(dtype))
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

    @staticmethod
    def _read_records(path: str, row_tag: str, sample_rows: int) -> tuple[list[dict], set[str]]:
        records = []
        seen_tags = set()
        stack = []
        for event, elem in ET.iterparse(path, events=("start", "end")):
            tag = _strip_namespace(elem.tag)
            if event == "start":
                stack.append(tag)
                if len(stack) == 2:
                    seen_tags.add(tag)
            else:
                stack.pop()
                if tag == row_tag and len(stack) == 1:
                    records.append({_strip_namespace(c.tag): c.text for c in elem})
                    elem.clear()
                    if len(records) >= sample_rows:
                        break
        return records, seen_tags
