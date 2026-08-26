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

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from schema_types import ColumnSchema
from inferrers.csv_inferrer import CSVSchemaInferrer
from inferrers.json_inferrer import JSONSchemaInferrer
from inferrers.databricks_remote_inferrer import DatabricksRemoteInferrer

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Local-file inferrers — used when location="local", i.e. testing against
# a file on the machine running this script. This is NOT how real client
# sources are read (see DatabricksRemoteInferrer below); it exists so the
# codegen logic itself can be tested quickly without a live workspace,
# the same way sample_data/ has been used throughout.
LOCAL_INFERRERS = {
    "csv": CSVSchemaInferrer(),
    "json": JSONSchemaInferrer(),
}

# Registry of source-type -> template file. Each template extends
# _base_notebook.py.jinja2 and only overrides the read_source block —
# see templates/csv_notebook.py.jinja2 for the pattern to follow when
# adding a new source type.
TEMPLATES = {
    "csv": "csv_notebook.py.jinja2",
    "json": "json_notebook.py.jinja2",
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
    out.write_text(rendered)
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
