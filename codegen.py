"""
codegen.py

Orchestrates the metadata-driven codegen flow described in PRD Section 5:
  1. Run the appropriate SchemaInferrer for the source type.
  2. Show the discovered schema back to the user for confirmation
     (Section 5.4's required checkpoint — here that's a printed table
     and a y/n prompt, since there's no UI yet).
  3. Render the Databricks notebook template with that confirmed schema.
  4. Write the notebook file, ready for manual import into Databricks.

Deployment automation (Databricks Jobs API) is NOT part of this
prototype — see the project README for why that's a deliberate next
step, not this one.
"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from schema_types import ColumnSchema, TableSpec
from inferrers.csv_inferrer import CSVSchemaInferrer
from inferrers.json_inferrer import JSONSchemaInferrer
from inferrers.xml_inferrer import XMLSchemaInferrer
from inferrers.xsd_inferrer import XSDSchemaInferrer
from inferrers.databricks_remote_inferrer import DatabricksRemoteInferrer
from inferrers.cobol_remote_inferrer import CobolRemoteInferrer

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Local-file inferrers — used when location="local", i.e. testing against
# a file on the machine running this script. This is NOT how real client
# sources are read (see DatabricksRemoteInferrer below); it exists so the
# codegen logic itself can be tested quickly without a live workspace,
# the same way sample_data/ has been used throughout.
# Note: DatabricksRemoteInferrer (location="databricks_workspace") only
# covers csv/json today — xml has no remote schema-probe template yet,
# so location="local" is the only discovery path for xml (the *deployed
# job* can still read xml from a real Volume/S3 path at run time; this
# is specifically about discovering the schema from a remote-only file).
LOCAL_INFERRERS = {
    "csv": CSVSchemaInferrer(),
    "json": JSONSchemaInferrer(),
    "xml": XMLSchemaInferrer(),
}

# Registry of source-type -> template file. Each template extends
# _base_notebook.py.jinja2 and only overrides the read_source block —
# see templates/csv_notebook.py.jinja2 for the pattern to follow when
# adding a new source type.
TEMPLATES = {
    "csv": "csv_notebook.py.jinja2",
    "json": "json_notebook.py.jinja2",
    "xml": "xml_notebook.py.jinja2",
}

# Multi-file counterpart of TEMPLATES — one folder of same-source-type
# files becomes one notebook (looping over every table) and one job, not
# one notebook per file. See render_multi_notebook().
MULTI_TEMPLATES = {
    "csv": "csv_multi_notebook.py.jinja2",
    "json": "json_multi_notebook.py.jinja2",
    "xml": "xml_multi_notebook.py.jinja2",
}

# Glob pattern used by list_source_files() for *local*-folder multi-file
# discovery. csv/json also support remote (Databricks Volume/S3) folder
# discovery — see infer_multi_schema_remote() — which lists the folder
# via GLOB_PATTERNS in databricks_remote_inferrer.py instead of this dict.
# xml has no remote-folder probe yet (only local, via this dict).
MULTI_FILE_GLOBS = {
    "csv": "*.csv",
    "json": "*.json",
    "xml": "*.xml",
}


def get_inferrer(source_type: str, source_config: dict):
    """
    Real client source files live in a Databricks Volume or an
    S3/external-location path — not on whatever machine runs this
    script. location="databricks_workspace" (the default for anything
    that isn't explicit local testing) routes schema discovery through
    DatabricksSQLSchemaInferrer, which runs the discovery read inside
    the client's own Databricks SQL warehouse via read_files(), rather
    than this script trying to open the file directly.
    """
    location = source_config.get("location", "databricks_workspace")

    if location == "local":
        if source_type not in LOCAL_INFERRERS:
            raise ValueError(
                f"No local inferrer for source_type='{source_type}'. "
                f"Available: {list(LOCAL_INFERRERS.keys())}"
            )
        return LOCAL_INFERRERS[source_type]

    elif location == "databricks_workspace":
        # One inferrer handles csv/json (and parquet later) uniformly —
        # it runs the same pandas logic remotely regardless of format,
        # since Volumes are FUSE-mounted and pandas just reads them like
        # any other local file from the cluster's point of view.
        return DatabricksRemoteInferrer()

    else:
        raise ValueError(
            f"Unknown location='{location}'. Use 'local' (testing) or "
            f"'databricks_workspace' (real client sources)."
        )


def print_schema_for_review(columns: list[ColumnSchema]) -> None:
    print("\nDiscovered schema:")
    print(f"{'column':<30} {'pandas dtype':<15} {'target type':<12}")
    print("-" * 60)
    for col in columns:
        print(f"{col.name:<30} {col.pandas_dtype:<15} {col.sql_type:<12}")
    print()


def confirm_schema() -> bool:
    answer = input("Does this schema look right? [y/n]: ").strip().lower()
    return answer == "y"


def build_ddl_columns(columns: list[ColumnSchema]) -> str:
    lines = [f"    {col.name} {col.sql_type}," for col in columns]
    return "\n".join(lines)


def build_type_imports(columns: list[ColumnSchema]) -> str:
    types_used = sorted({col.spark_type for col in columns})
    return ",\n    ".join(types_used)


def infer_schema(source_type: str, source_config: dict) -> list[ColumnSchema]:
    """
    Runs schema discovery only — no review prompt, no rendering. Split out
    from generate_notebook() so callers with their own review UI (the
    Streamlit app's schema-confirmation step, for instance) can drive the
    confirm step themselves instead of going through the CLI's blocking
    input() prompt.
    """
    inferrer = get_inferrer(source_type, source_config)
    # DatabricksSQLSchemaInferrer needs to know which format to ask
    # read_files() for — piggyback on source_type rather than adding a
    # separate field the caller has to remember to set.
    if source_config.get("location", "databricks_workspace") == "databricks_workspace":
        source_config = {**source_config, "format": source_type}

    return inferrer.infer(source_config)


def infer_schema_from_xsd(xsd_path: str, row_tag: str, location: str = "local") -> list[ColumnSchema]:
    """
    Alternate schema-discovery path for XML sources: derive columns from
    an XSD's row-level element definition instead of sampling actual
    data. Bypasses get_inferrer()'s location-based dispatch entirely —
    the local/remote question here is "where does the XSD file live",
    which is a much smaller ask than remote *data* sampling (no cluster
    job needed — see xsd_inferrer.py's _fetch_remote_text). See
    inferrers/xsd_inferrer.py for scope (flat/shallow rows, same as
    xml_inferrer.py).
    """
    return XSDSchemaInferrer().infer({"xsd_path": xsd_path, "row_tag": row_tag, "location": location})


def infer_cobol_schema(
    copybook_path: str,
    data_path: str,
    existing_cluster_id: str,
    is_record_sequence: bool = False,
    is_text: bool = False,
) -> list[ColumnSchema]:
    """
    COBOL schema discovery is remote-only — bypasses get_inferrer()'s
    location dispatch entirely, since there's no local path to choose
    between (Cobrix is a Spark/JVM data source, not a pip package — see
    inferrers/cobol_remote_inferrer.py for the full reasoning). Returns
    already-flattened leaf columns — group items (Cobrix maps these to
    Spark StructType, not flat scalars) are recursively flattened by the
    probe itself; each leaf's ColumnSchema.struct_path carries the
    original nested path so render_cobol_notebook() can rebuild the same
    flattening as real Spark code.
    """
    return CobolRemoteInferrer().infer(
        {
            "copybook_path": copybook_path,
            "file_path": data_path,
            "existing_cluster_id": existing_cluster_id,
            "is_record_sequence": is_record_sequence,
            "is_text": is_text,
        }
    )


def build_cobol_select_columns(columns: list[ColumnSchema]) -> str:
    """
    Builds the Python source for the notebook's post-read .select(...) —
    one F.col(...).getField(...)...alias(...) expression per column,
    walking each column's struct_path (falling back to just its own name
    when the field was never nested) to pull COBOL group-item leaves out
    of the StructType columns Cobrix returns them as.
    """
    lines = []
    for col in columns:
        segments = col.struct_path or [col.name]
        expr = f'F.col("{segments[0]}")'
        for segment in segments[1:]:
            expr += f'.getField("{segment}")'
        lines.append(f'    {expr}.alias("{col.name}"),')
    return "\n".join(lines)


def render_cobol_notebook(
    copybook_path: str,
    source_path: str,
    catalog: str,
    target_schema: str,
    table: str,
    columns: list[ColumnSchema],
    output_path: str,
    is_record_sequence: bool = False,
    is_text: bool = False,
) -> str:
    """
    Renders the COBOL ingestion notebook. Doesn't go through render_notebook()
    /TEMPLATES — cobol_notebook.py.jinja2 doesn't extend _base_notebook.py.jinja2.
    Unlike every other format here, it deliberately does NOT bake in a
    fixed StructType: Cobrix re-derives the schema from the copybook on
    every run, which is the correct idiom for a stable, change-controlled
    schema contract like a copybook (see the template's own docstring).
    `columns` is still used, though — for the CREATE TABLE DDL (which does
    need concrete types) and for the flattening .select() (see
    build_cobol_select_columns), both captured once at generation time
    from the schema you reviewed.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("cobol_notebook.py.jinja2")

    rendered = template.render(
        copybook_path=copybook_path,
        source_path=source_path,
        catalog=catalog,
        target_schema=target_schema,
        table=table,
        ddl_columns=build_ddl_columns(columns),
        select_columns=build_cobol_select_columns(columns),
        is_record_sequence=is_record_sequence,
        is_text=is_text,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"Notebook written to: {out}")
    return str(out)


def render_notebook(
    source_type: str,
    source_config: dict,
    catalog: str,
    target_schema: str,
    table: str,
    columns: list[ColumnSchema],
    output_path: str,
) -> str:
    """
    Renders and writes the notebook for an already-confirmed schema. Takes
    the same source_config shape infer_schema() was called with.
    """
    if source_type not in TEMPLATES:
        raise ValueError(
            f"No template registered for source_type='{source_type}'. "
            f"Available: {list(TEMPLATES.keys())}"
        )

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template(TEMPLATES[source_type])

    # source_config is passed through as-is (in addition to being spread
    # explicitly below) so each new source type can carry whatever
    # fields its own template needs (delimiter, multiline, etc.) without
    # this function needing to know about them individually.
    rendered = template.render(
        source_type=source_type,
        source_path=source_config["file_path"],
        catalog=catalog,
        target_schema=target_schema,
        table=table,
        columns=columns,
        ddl_columns=build_ddl_columns(columns),
        spark_type_imports=build_type_imports(columns),
        **{k: (str(v).lower() if isinstance(v, bool) else v) for k, v in source_config.items()},
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"Notebook written to: {out}")
    return str(out)


def sanitize_table_name(name: str) -> str:
    """Derives a safe default Bronze table name from a file stem (e.g. 'Order Item' -> 'order_item')."""
    name = re.sub(r"[^0-9a-zA-Z_]", "_", name.strip().lower()).strip("_")
    if not name:
        return "table"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def list_source_files(folder_path: str, source_type: str) -> list[str]:
    """
    Local-folder file discovery for multi-file mode. Only local folders —
    see MULTI_FILE_GLOBS for why Databricks Volume/S3 folder discovery
    isn't built yet.
    """
    if source_type not in MULTI_FILE_GLOBS:
        raise ValueError(
            f"No multi-file glob for source_type='{source_type}'. "
            f"Available: {list(MULTI_FILE_GLOBS.keys())}"
        )
    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError(f"'{folder_path}' is not a folder (or doesn't exist).")

    paths = sorted(str(p) for p in folder.glob(MULTI_FILE_GLOBS[source_type]))
    if not paths:
        raise ValueError(f"No {MULTI_FILE_GLOBS[source_type]} files found in {folder_path}")
    return paths


def infer_multi_schema(
    source_type: str,
    file_paths: list[str],
    source_config_template: dict,
) -> list[TableSpec]:
    """
    Runs infer_schema() once per file in a folder. Mirrors infer_schema()'s
    split from generate_notebook(): no confirm step here either — the
    caller (the Streamlit multi-file review step) owns confirming/editing
    table names before render_multi_notebook().
    """
    tables = []
    for path in file_paths:
        file_config = {**source_config_template, "file_path": path, "location": "local"}
        columns = infer_schema(source_type, file_config)
        tables.append(
            TableSpec(
                table=sanitize_table_name(Path(path).stem),
                file_name=Path(path).name,
                source_path=path,
                columns=columns,
            )
        )
    return tables


def infer_multi_schema_remote(source_type: str, source_config: dict) -> list[TableSpec]:
    """
    Remote counterpart of infer_multi_schema(): the folder listing AND
    per-file sampling both happen inside a single probe job run on the
    client's own cluster (see DatabricksRemoteInferrer.infer_multi) —
    this script never lists the client's folder itself, unlike the local
    path (list_source_files() + a Python-side loop).
    """
    config = {**source_config, "format": source_type}
    return DatabricksRemoteInferrer().infer_multi(config)


def render_multi_notebook(
    source_type: str,
    source_config: dict,
    catalog: str,
    target_schema: str,
    tables: list[TableSpec],
    output_path: str,
) -> str:
    """
    Renders and writes ONE notebook covering every table in `tables` — one
    job, not one job per file. source_config["file_path"] here is the
    shared source *folder*, not a single file's path (see MULTI_TEMPLATES'
    base template: it becomes the "source_path" job parameter, and each
    table's file_name is joined onto it at run time).
    """
    if source_type not in MULTI_TEMPLATES:
        raise ValueError(
            f"No multi-file template registered for source_type='{source_type}'. "
            f"Available: {list(MULTI_TEMPLATES.keys())}"
        )

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template(MULTI_TEMPLATES[source_type])

    all_columns = [col for t in tables for col in t.columns]

    rendered = template.render(
        source_type=source_type,
        source_path=source_config["file_path"],
        catalog=catalog,
        target_schema=target_schema,
        tables=tables,
        spark_type_imports=build_type_imports(all_columns),
        **{
            k: (str(v).lower() if isinstance(v, bool) else v)
            for k, v in source_config.items()
            if k != "file_path"
        },
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"Notebook written to: {out}")
    return str(out)


def generate_notebook(
    source_type: str,
    source_config: dict,
    catalog: str,
    target_schema: str,
    table: str,
    output_path: str,
) -> str:
    """CLI entry point: infer, print + confirm via input(), then render."""
    columns = infer_schema(source_type, source_config)

    print_schema_for_review(columns)
    if not confirm_schema():
        print("Aborted — adjust the source file or config and try again.")
        return ""

    return render_notebook(
        source_type=source_type,
        source_config=source_config,
        catalog=catalog,
        target_schema=target_schema,
        table=table,
        columns=columns,
        output_path=output_path,
    )
