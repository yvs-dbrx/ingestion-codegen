"""
xsd_inferrer.py

Derives column schema directly from an XSD's row-level element, instead
of sampling actual XML data (see xml_inferrer.py). This is actually a
better fit for the PRD's control-plane-only principle than sampling is:
parsing a schema *definition* never touches an actual data file at all —
not even a scoped sample read. The tradeoff is it needs a real XSD on
hand, and only covers what the XSD declares (an optional field that's
declared but happens to be absent from a data sample would be *missed*
by xml_inferrer.py but *caught* here — the reverse of the usual
inference tradeoff).

Scope matches xml_inferrer.py's: flat/shallow rows — the row element
(config["row_tag"]) is a complexType whose direct children are simple-
typed leaf elements. Nested complex types below the row level, choices,
xs:extension/xs:restriction, and imported/included schemas aren't
handled — genuinely more design work, deferred the same way
xml_inferrer.py defers XML attributes and deep nesting.

Uses the `xmlschema` library to parse the XSD rather than hand-rolling a
parser — same reasoning as this project's planned approach to COBOL
copybooks via Cobrix: don't reinvent a mature format parser.
"""

import xmlschema

from schema_types import ColumnSchema
from inferrers.base import SchemaInferrer

# XSD built-in simple types -> (Spark type class, Databricks SQL DDL type).
# Deliberately not attempting date/dateTime parsing here either — same
# disclosed "comes back as STRING" gap as the CSV/JSON/XML inferrers.
XSD_TYPE_MAP = {
    "string": ("StringType", "STRING"),
    "normalizedString": ("StringType", "STRING"),
    "token": ("StringType", "STRING"),
    "anyURI": ("StringType", "STRING"),
    "date": ("StringType", "STRING"),
    "dateTime": ("StringType", "STRING"),
    "time": ("StringType", "STRING"),
    "boolean": ("BooleanType", "BOOLEAN"),
    "integer": ("LongType", "BIGINT"),
    "int": ("IntegerType", "INT"),
    "long": ("LongType", "BIGINT"),
    "short": ("IntegerType", "INT"),
    "byte": ("IntegerType", "INT"),
    "positiveInteger": ("LongType", "BIGINT"),
    "nonNegativeInteger": ("LongType", "BIGINT"),
    "negativeInteger": ("LongType", "BIGINT"),
    "nonPositiveInteger": ("LongType", "BIGINT"),
    "decimal": ("DoubleType", "DOUBLE"),
    "float": ("FloatType", "FLOAT"),
    "double": ("DoubleType", "DOUBLE"),
}
DEFAULT_XSD_TYPE = ("StringType", "STRING")


class XSDSchemaInferrer(SchemaInferrer):
    source_type = "xsd"

    def infer(self, config: dict) -> list[ColumnSchema]:
        xsd_path = config["xsd_path"]
        row_tag = config.get("row_tag") or "record"
        location = config.get("location", "local")

        # Unlike sampling actual data, fetching an XSD needs no cluster job:
        # it's a schema definition, not customer data, so reading its (small)
        # text directly from the control plane doesn't cross the line that
        # data sampling does. See _fetch_remote_text for the two Databricks
        # path shapes this handles.
        xsd_source = self._fetch_remote_text(xsd_path) if location == "databricks_workspace" else xsd_path

        schema = xmlschema.XMLSchema(xsd_source)
        row_element = self._find_row_element(schema, row_tag)
        if row_element is None:
            top_level = sorted(schema.elements.keys())
            raise ValueError(
                f"No element named '{row_tag}' found anywhere in {xsd_path}. "
                f"Top-level elements: {top_level or 'none'}."
            )
        if row_element.type.is_simple():
            raise ValueError(
                f"Element '{row_tag}' in {xsd_path} is a simple type (a single value), not a row "
                f"of fields — expected a complexType with a sequence of child elements."
            )

        columns = []
        for child in row_element.type.content.iter_elements():
            if not child.type.is_simple():
                print(
                    f"  [warning] element '{child.local_name}' is a nested complex type — "
                    f"mapping to STRING rather than flattening it. Deeply nested XSDs aren't "
                    f"supported yet; review this column before relying on it."
                )
                spark_type, sql_type = DEFAULT_XSD_TYPE
                display_type_name = "string"
            else:
                resolved_type_name = self._resolve_type_name(child.type)
                spark_type, sql_type = XSD_TYPE_MAP.get(resolved_type_name, DEFAULT_XSD_TYPE)
                # Prefer the type's own declared name for display (e.g. "USAmountType") — it's
                # more meaningful than the resolved primitive. Anonymous inline-restricted types
                # (no local_name of their own) fall back to the resolved name instead, so the
                # display doesn't default to a misleading "string" for a column that resolved to
                # something else entirely.
                display_type_name = child.type.local_name or resolved_type_name

            columns.append(
                ColumnSchema(
                    name=child.local_name,
                    pandas_dtype=f"xsd:{display_type_name}",
                    spark_type=spark_type,
                    sql_type=sql_type,
                    nullable=bool(child.min_occurs == 0 or child.nillable),
                )
            )
        return columns

    @staticmethod
    def _fetch_remote_text(path: str) -> str:
        """
        Fetches a file's text content from a Databricks Workspace or
        Volume path. /Volumes/... paths go through the Files API (works
        over REST regardless of cluster FUSE mounting); anything else is
        treated as a Workspace path via the export API.

        NOT YET LIVE-TESTED against a real workspace — same caveat as
        databricks_remote_inferrer.py, which this mirrors.

        Note: an XSD fetched this way that itself uses xsd:include/import
        with a relative path won't resolve those — same deferred gap as
        local multi-file XSDs (see codegen.py's MULTI_FILE_GLOBS note).
        """
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()

        if path.startswith("/Volumes/"):
            response = w.files.download(path)
            return response.contents.read().decode("utf-8")
        else:
            import base64
            from databricks.sdk.service.workspace import ExportFormat

            response = w.workspace.export(path, format=ExportFormat.AUTO)
            return base64.b64decode(response.content).decode("utf-8")

    @staticmethod
    def _resolve_type_name(xsd_type) -> str:
        """
        Walks up the XSD type's base_type chain, returning the first
        local_name found in XSD_TYPE_MAP. This is what makes named custom
        types resolve correctly — real-world XSDs (government e-file
        schemas especially) almost always name their types rather than
        use built-ins directly, e.g. a "USAmountType" that's really just
        a restricted xsd:decimal. Stops at the first match rather than
        walking straight to the root primitive, so a more specific type
        like xsd:long (itself derived from xsd:decimal in the XSD type
        hierarchy) still resolves to "long"/BIGINT, not "decimal"/DOUBLE.
        """
        seen = set()
        current = xsd_type
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            name = current.local_name
            if name in XSD_TYPE_MAP:
                return name
            current = getattr(current, "base_type", None)
        return "string"

    @classmethod
    def _find_row_element(cls, schema: "xmlschema.XMLSchema", row_tag: str):
        for root_element in schema.elements.values():
            found = cls._search(root_element, row_tag, set())
            if found is not None:
                return found
        return None

    @classmethod
    def _search(cls, element, row_tag: str, seen: set):
        if id(element) in seen:
            return None  # guards against a schema with a recursive/self-referential type
        seen.add(id(element))

        if element.local_name == row_tag:
            return element
        if element.type.is_simple():
            return None
        for child in element.type.content.iter_elements():
            found = cls._search(child, row_tag, seen)
            if found is not None:
                return found
        return None
