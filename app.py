"""
app.py

Streamlit UI wrapping the existing cli.py / deploy_cli.py flow (PRD
Section 5.2-5.6, scoped to Phase 1: CSV/JSON -> Databricks). No auth, no
multi-tenant store, no real billing — see pipeline_store.py for what
stands in for those until the hosted stack exists. Same generate ->
review -> deploy steps as the CLI, just as a form instead of flags + an
input() prompt.

Two source dimensions, independent of each other:
  - Mode: single file, or a folder of same-source-type files ingested as
    one notebook / one job (see codegen.render_multi_notebook).
  - Location: a path on this machine, or a Databricks Volume/S3 path read
    remotely via DatabricksRemoteInferrer — for both single files and
    folders, for csv/json. XML (sample-based) has no remote probe yet;
    XSD-derived schema has its own, separate local-vs-remote choice
    (fetching the XSD's text needs no cluster job at all — see
    inferrers/xsd_inferrer.py).

COBOL is a third, separate case: always remote (Cobrix is a Spark/JVM
data source, no local Python path exists), single-file only, and needs
an existing cluster with Cobrix attached for both discovery *and*
deploy — see inferrers/cobol_remote_inferrer.py.

Run with:
    streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import pipeline_store as store
from codegen import (
    infer_schema,
    infer_schema_from_xsd,
    infer_cobol_schema,
    render_notebook,
    render_cobol_notebook,
    list_source_files,
    infer_multi_schema,
    infer_multi_schema_remote,
    render_multi_notebook,
)
from databricks_deploy import build_cron_expression, deploy_and_schedule

st.set_page_config(page_title="Ingestion Codegen", layout="wide")

for key in ("inferred_columns", "inferred_tables", "source_config", "generated_path", "mode"):
    st.session_state.setdefault(key, None)

# --- Sidebar: simulated plan + usage (PRD 5.8 / Section 9, stubbed locally) ---
st.sidebar.title("Ingestion Codegen")
tiers = list(store.TIER_LIMITS)
current_tier = st.sidebar.selectbox(
    "Plan (simulated — no billing wired up yet)",
    tiers,
    index=tiers.index(store.get_tier()),
)
if current_tier != store.get_tier():
    store.set_tier(current_tier)

count = store.pipeline_count()
limit = store.tier_limit()
st.sidebar.metric("Active pipelines", f"{count} / {limit}")
if count >= limit:
    st.sidebar.warning("At your plan's pipeline limit. Delete a pipeline or switch tiers above to add more.")

page = st.sidebar.radio("Navigate", ["Dashboard", "New pipeline"])

# --- Dashboard ---
if page == "Dashboard":
    st.header("Pipelines")
    pipelines = store.list_pipelines()
    if not pipelines:
        st.info("No pipelines yet — create one from **New pipeline**.")
    else:
        st.dataframe(pd.DataFrame(pipelines), width="stretch", hide_index=True)
        for p in pipelines:
            cols = st.columns([4, 1])
            tables_label = ", ".join(p.get("tables", [])) or p.get("table", "")
            cols[0].markdown(f"**{p['job_name']}** ({tables_label}) — [view job]({p.get('job_url', '')})")
            if cols[1].button("Delete", key=f"del_{p['job_name']}"):
                store.remove_pipeline(p["job_name"])
                st.rerun()

# --- New pipeline wizard ---
else:
    st.header("New pipeline")

    if not store.can_create_pipeline():
        st.error(
            f"You're at your plan's limit of {limit} active pipelines. "
            "Switch tiers in the sidebar or delete an existing pipeline to continue."
        )
        st.stop()

    st.subheader("1. Source")
    mode = st.radio("Ingestion mode", ["Single file", "Multi-file (folder → one job)"], horizontal=True)
    source_type = st.selectbox("Source type", ["csv", "json", "xml", "cobol"])
    is_cobol = source_type == "cobol"

    if is_cobol and mode != "Single file":
        st.warning("COBOL only supports single-file mode for now — switch Ingestion mode above to continue.")

    discovery_mode = "sample"
    if source_type == "xml" and mode == "Single file":
        schema_source = st.radio(
            "Schema source", ["Infer from sample data", "Derive from XSD"], horizontal=True
        )
        discovery_mode = "xsd" if schema_source == "Derive from XSD" else "sample"

    remote_discovery_supported = source_type in ("csv", "json")
    xsd_location = "local"
    copybook_path_input = ""
    cobol_cluster_id = ""
    if is_cobol:
        location = "Local (this machine)"  # meaningless for COBOL — always remote, no local path exists
        st.caption(
            "COBOL discovery always runs on your Databricks cluster — Cobrix (the library that "
            "understands copybook syntax and EBCDIC decoding) is a Spark/JVM data source, not "
            "something this app can run locally."
        )
        copybook_path_input = st.text_input(
            "Copybook path in Databricks (Workspace or Volume)",
            placeholder="/Volumes/main/schemas/customer.cpy",
        )
        source_path_input = st.text_input(
            "Data file path in Databricks (Volume/S3 — a real sample; Cobrix needs an actual "
            "file to resolve against, even though the schema itself comes from the copybook)",
            placeholder="/Volumes/main/landing/customer.dat",
        )
        cobol_cluster_id = st.text_input(
            "Existing cluster ID * (required — must have Cobrix attached, Scala suffix matching "
            "this cluster's Spark version: spark-cobol_2.12 for Spark 3.x/DBR<=16.x, spark-cobol_2.13 "
            "for Spark 4.0+/DBR 17.x+. Serverless isn't known to support custom JAR attachment)",
            placeholder="0101-000000-abc123",
        )
        c1, c2 = st.columns(2)
        is_record_sequence = c1.checkbox(
            "Variable-length records (is_record_sequence)", False,
            help="Common Cobrix option for files where each record is prefixed with a length indicator."
        )
        is_text = c2.checkbox(
            "Text mode (is_text)", False,
            help="Check this if each record is newline-terminated — typical of mainframe-to-Unix "
                 "text transfers. Off matches Cobrix's own default (pure fixed-length binary, no "
                 "separators); if that fails with an IllegalArgumentException about the file size "
                 "not being a multiple of the record size, this is usually why."
        )
    elif discovery_mode == "xsd":
        location = "Local (this machine)"  # irrelevant here — XSD parsing never reads a data file
        xsd_loc_choice = st.radio(
            "XSD file location", ["Local (this machine)", "Databricks Workspace / Volume"], horizontal=True
        )
        xsd_location = "databricks_workspace" if xsd_loc_choice == "Databricks Workspace / Volume" else "local"
    elif remote_discovery_supported:
        location = st.radio("Source location", ["Local (this machine)", "Databricks Volume / S3"], horizontal=True)
    else:
        location = "Local (this machine)"
        st.caption(
            "XML schema discovery is local-only for now — there's no remote probe template "
            "for it yet (unlike csv/json, which support this for both single files and folders). "
            "The deployed job can still read XML from a real Databricks Volume/S3 path at run "
            "time — this only affects how the schema gets discovered."
        )

    is_remote = location == "Databricks Volume / S3"

    xsd_path_input = ""
    if discovery_mode == "xsd":
        xsd_path_placeholder = (
            "/Volumes/main/schemas/pts4255.xsd" if xsd_location == "databricks_workspace"
            else str(Path("sample_data/sample.xsd").resolve())
        )
        xsd_path_input = st.text_input(
            "XSD file path (schema is derived from this; no data file is read for discovery)",
            placeholder=xsd_path_placeholder,
        )
        source_path_input = st.text_input(
            "Data file path (optional — just becomes the notebook's default source; "
            "you'll set the real Databricks Volume/S3 path at deploy time regardless)",
            placeholder="/Volumes/main/landing/customers.xml",
        )
    elif not is_cobol:
        if mode == "Single file":
            path_label = "Databricks Volume/S3 file path" if is_remote else "Local file path"
            path_placeholder = "/Volumes/main/landing/customers.csv" if is_remote else str(Path("sample_data/sample.csv").resolve())
        else:
            path_label = (
                "Databricks Volume/S3 folder path (contains the files to ingest — all same source type)"
                if is_remote
                else "Local folder path (contains the files to ingest — all same source type)"
            )
            path_placeholder = "/Volumes/main/landing" if is_remote else str(Path("sample_data").resolve())

        source_path_input = st.text_input(path_label, placeholder=path_placeholder)
    # else: is_cobol — source_path_input and copybook_path_input were already set above

    cluster_id = ""
    if is_remote:
        cluster_id = st.text_input(
            "Existing cluster ID (optional — leave blank to use serverless for schema discovery)", ""
        )

    delimiter, has_header, multiline, row_tag = ",", True, False, "record"
    is_record_sequence = is_record_sequence if is_cobol else False
    is_text = is_text if is_cobol else False
    if source_type == "csv":
        c1, c2 = st.columns(2)
        delimiter = c1.text_input("Delimiter", ",")
        has_header = c2.checkbox("Has header row", True)
    elif source_type == "json":
        multiline = st.checkbox("Single JSON document (not NDJSON)", False)
    elif source_type == "xml":
        row_tag = st.text_input(
            "Row tag (the repeated element name per record — e.g. `record` for <root><record>...</record>...</root>)",
            "record",
        )

    st.subheader("2. Target (Databricks)")
    c1, c2, c3 = st.columns(3)
    catalog = c1.text_input("Catalog", "main")
    target_schema = c2.text_input("Schema", "bronze")
    table = ""
    if mode == "Single file":
        table = c3.text_input("Table", "")
    else:
        c3.caption("Table names are derived per file below, once discovered — editable before you generate.")

    if discovery_mode == "xsd":
        ready_to_discover = bool(xsd_path_input) and bool(table)
    elif is_cobol:
        ready_to_discover = (
            mode == "Single file"
            and bool(copybook_path_input)
            and bool(source_path_input)
            and bool(cobol_cluster_id)
            and bool(table)
        )
    else:
        ready_to_discover = bool(source_path_input) and (mode != "Single file" or bool(table))

    if st.button("Discover schema", disabled=not ready_to_discover):
        base_config = {
            "file_path": source_path_input,
            "location": "databricks_workspace" if is_remote else "local",
            "delimiter": delimiter,
            "has_header": has_header,
            "multiline": multiline,
            "row_tag": row_tag,
            "copybook_path": copybook_path_input,
            "is_record_sequence": is_record_sequence,
            "is_text": is_text,
        }
        if is_remote and cluster_id:
            base_config["existing_cluster_id"] = cluster_id

        st.session_state.inferred_columns = None
        st.session_state.inferred_tables = None
        st.session_state.generated_path = None

        try:
            if discovery_mode == "xsd":
                st.session_state.inferred_columns = infer_schema_from_xsd(
                    xsd_path_input, row_tag, location=xsd_location
                )
            elif is_cobol:
                st.session_state.inferred_columns = infer_cobol_schema(
                    copybook_path_input, source_path_input, cobol_cluster_id,
                    is_record_sequence, is_text,
                )
            elif mode == "Single file":
                st.session_state.inferred_columns = infer_schema(source_type, base_config)
            elif is_remote:
                st.session_state.inferred_tables = infer_multi_schema_remote(source_type, base_config)
            else:
                file_paths = list_source_files(source_path_input, source_type)
                st.session_state.inferred_tables = infer_multi_schema(source_type, file_paths, base_config)
            st.session_state.source_config = base_config
            st.session_state.mode = mode
        except Exception as e:
            st.error(f"Schema discovery failed: {e}")

    # --- Single-file review ---
    if st.session_state.mode == "Single file" and st.session_state.inferred_columns:
        st.subheader("3. Confirm schema")
        columns = st.session_state.inferred_columns
        st.dataframe(
            pd.DataFrame(
                [{"column": c.name, "pandas dtype": c.pandas_dtype, "target type": c.sql_type} for c in columns]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "This is the required review checkpoint from PRD Section 5.4 — it becomes the fixed DDL "
            "baked into the notebook. Re-discover if something looks wrong."
        )
        confirmed = st.checkbox("This schema looks correct")
        output_path = st.text_input("Notebook output path", f"notebooks/{table or 'pipeline'}_ingest.py")

        if st.button("Generate notebook", disabled=not (confirmed and table)):
            if source_type == "cobol":
                out = render_cobol_notebook(
                    copybook_path=st.session_state.source_config["copybook_path"],
                    source_path=st.session_state.source_config["file_path"],
                    catalog=catalog,
                    target_schema=target_schema,
                    table=table,
                    columns=columns,
                    output_path=output_path,
                    is_record_sequence=st.session_state.source_config.get("is_record_sequence", False),
                    is_text=st.session_state.source_config.get("is_text", False),
                )
            else:
                out = render_notebook(
                    source_type=source_type,
                    source_config=st.session_state.source_config,
                    catalog=catalog,
                    target_schema=target_schema,
                    table=table,
                    columns=columns,
                    output_path=output_path,
                )
            st.session_state.generated_path = out

    # --- Multi-file review ---
    if st.session_state.mode != "Single file" and st.session_state.inferred_tables:
        st.subheader("3. Review tables & schema")
        edited_tables = []
        for i, t in enumerate(st.session_state.inferred_tables):
            with st.expander(f"{t.file_name} → table `{t.table}`", expanded=True):
                new_name = st.text_input("Table name", t.table, key=f"tablename_{i}")
                st.dataframe(
                    pd.DataFrame(
                        [{"column": c.name, "pandas dtype": c.pandas_dtype, "target type": c.sql_type} for c in t.columns]
                    ),
                    width="stretch",
                    hide_index=True,
                )
                edited_tables.append(
                    type(t)(table=new_name.strip(), file_name=t.file_name, source_path=t.source_path, columns=t.columns)
                )
        st.session_state.inferred_tables = edited_tables

        table_names = [t.table for t in edited_tables]
        has_duplicates = len(table_names) != len(set(table_names))
        has_blanks = any(not name for name in table_names)
        if has_duplicates:
            st.error("Table names must be unique — two files resolved to the same table name.")
        if has_blanks:
            st.error("Every table needs a name.")

        st.caption(
            "This is the required review checkpoint from PRD Section 5.4, applied per table — each becomes "
            "fixed DDL baked into the shared notebook. Re-discover if something looks wrong."
        )
        confirmed = st.checkbox("All table schemas above look correct")
        default_out = f"notebooks/{Path(source_path_input).name or 'multi'}_ingest.py" if source_path_input else "notebooks/multi_ingest.py"
        output_path = st.text_input("Notebook output path", default_out)

        if st.button("Generate notebook", disabled=not (confirmed and not has_duplicates and not has_blanks)):
            out = render_multi_notebook(
                source_type=source_type,
                source_config=st.session_state.source_config,
                catalog=catalog,
                target_schema=target_schema,
                tables=edited_tables,
                output_path=output_path,
            )
            st.session_state.generated_path = out

    # --- Review generated code + deploy (shared by both modes) ---
    if st.session_state.generated_path:
        st.success(f"Notebook written to `{st.session_state.generated_path}`")

        st.subheader("4. Review generated code")
        code = Path(st.session_state.generated_path).read_text(encoding="utf-8")
        st.code(code, language="python")

        is_multi = st.session_state.mode != "Single file"
        pipeline_tables = (
            [t.table for t in st.session_state.inferred_tables] if is_multi else [table]
        )

        st.subheader("5. Deploy & schedule")
        st.caption(
            "Requires DATABRICKS_HOST / DATABRICKS_TOKEN set as environment variables "
            "before launching this app — see README."
        )
        default_job_name = "-".join(pipeline_tables[:3]) + ("-ingest" if pipeline_tables else "")
        job_name = st.text_input("Job name", default_job_name)
        workspace_path = st.text_input(
            "Workspace path", f"/Workspace/Shared/ingestion/{pipeline_tables[0] if pipeline_tables else 'pipeline'}"
        )
        source_path_label = (
            "Source FOLDER path in Databricks (Volume/S3 — each table's file is read from here at run time) *"
            if is_multi
            else "Source path in Databricks (Volume/S3 — where the job reads from at run time) *"
        )
        source_path_placeholder = (
            "/Volumes/main/bronze/landing/customers" if is_multi else "/Volumes/main/bronze/landing/customers.csv"
        )
        volume_source_path = st.text_input(source_path_label, placeholder=source_path_placeholder)
        looks_like_catalog_table = (
            bool(volume_source_path)
            and not volume_source_path.startswith(("/", "s3:", "abfss:", "gs:", "dbfs:"))
            and volume_source_path.count(".") >= 1
        )
        if looks_like_catalog_table:
            st.warning(
                f"`{volume_source_path}` looks like a catalog.schema.table identifier (the notebook's *target*, "
                f"shown above as `target_table`), not a Databricks Volume/S3 *source* path. Expected something "
                f"like `{source_path_placeholder}`. Deploy will still work, but the job will fail at run time "
                f"trying to read from that as a file path."
            )
        deploy_copybook_path = ""
        deploy_cluster_id = ""
        if source_type == "cobol":
            deploy_copybook_path = st.text_input(
                "Copybook path in Databricks (Workspace/Volume) *",
                value=st.session_state.source_config.get("copybook_path", ""),
            )
            deploy_cluster_id = st.text_input(
                "Existing cluster ID for job runs * (required — Cobrix must be attached; "
                "serverless can't run COBOL jobs)",
                value=cobol_cluster_id,
            )

        c1, c2, c3 = st.columns(3)
        freq = c1.selectbox("Frequency", ["daily", "hourly", "weekly"])
        time_str = c2.text_input("Time (HH:MM, 24h)", "02:00")
        tz = c3.text_input("Timezone", "UTC")
        run_now = st.checkbox("Run immediately after deploying", True)

        deploy_ready = bool(job_name and workspace_path and volume_source_path)
        if source_type == "cobol":
            deploy_ready = deploy_ready and bool(deploy_copybook_path) and bool(deploy_cluster_id)
        if not deploy_ready:
            missing = [
                label
                for label, val in [
                    ("Job name", job_name),
                    ("Workspace path", workspace_path),
                    ("Source path *", volume_source_path),
                ] + (
                    [("Copybook path *", deploy_copybook_path), ("Cluster ID *", deploy_cluster_id)]
                    if source_type == "cobol" else []
                )
                if not val
            ]
            st.caption(f"⚠️ Deploy button is disabled until you fill in: {', '.join(missing)}.")
        if st.button("Deploy to Databricks", disabled=not deploy_ready):
            if not store.can_create_pipeline():
                st.error("Plan limit reached while this form was open — can't deploy another pipeline.")
            else:
                try:
                    cron = build_cron_expression(freq, time_str)
                    result = deploy_and_schedule(
                        local_notebook_path=st.session_state.generated_path,
                        workspace_path=workspace_path,
                        job_name=job_name,
                        source_path_param=volume_source_path,
                        cron_expression=cron,
                        timezone=tz,
                        run_now=run_now,
                        existing_cluster_id=deploy_cluster_id or None,
                        extra_parameters={"copybook_path": deploy_copybook_path} if source_type == "cobol" else None,
                    )
                except Exception as e:
                    st.error(f"Deployment failed: {e}")
                else:
                    store.add_pipeline(
                        {
                            "job_name": job_name,
                            "source_type": source_type,
                            "catalog": catalog,
                            "schema": target_schema,
                            "tables": pipeline_tables,
                            "job_id": result["job_id"],
                            "job_url": result["job_url"],
                        }
                    )
                    st.success(f"Deployed — [view job]({result['job_url']})")
                    st.session_state.inferred_columns = None
                    st.session_state.inferred_tables = None
                    st.session_state.generated_path = None
                    st.session_state.mode = None
                    st.rerun()
