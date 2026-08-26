"""
app.py

Streamlit UI wrapping the existing cli.py / deploy_cli.py flow (PRD
Section 5.2-5.6, scoped to Phase 1: CSV/JSON -> Databricks, local files
only). No auth, no multi-tenant store, no real billing — see
pipeline_store.py for what stands in for those until the hosted stack
exists. Same generate -> review -> deploy steps as the CLI, just as a
form instead of flags + an input() prompt.

Run with:
    streamlit run app.py
"""

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import pipeline_store as store
from codegen import infer_schema, render_notebook
from databricks_deploy import build_cron_expression, deploy_and_schedule

st.set_page_config(page_title="Ingestion Codegen", layout="wide")

for key in ("inferred_columns", "source_config", "generated_path"):
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
        st.dataframe(pd.DataFrame(pipelines), use_container_width=True, hide_index=True)
        for p in pipelines:
            cols = st.columns([4, 1])
            cols[0].markdown(f"**{p['job_name']}** — [view job]({p.get('job_url', '')})")
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
    source_type = st.selectbox("Source type", ["csv", "json"])
    uploaded = st.file_uploader("Sample file (used for schema discovery only, per PRD Section 2)")

    delimiter, has_header, multiline = ",", True, False
    if source_type == "csv":
        c1, c2 = st.columns(2)
        delimiter = c1.text_input("Delimiter", ",")
        has_header = c2.checkbox("Has header row", True)
    else:
        multiline = st.checkbox("Single JSON document (not NDJSON)", False)

    st.subheader("2. Target (Databricks)")
    c1, c2, c3 = st.columns(3)
    catalog = c1.text_input("Catalog", "main")
    target_schema = c2.text_input("Schema", "bronze")
    table = c3.text_input("Table", "")

    ready_to_discover = uploaded is not None and bool(table)
    if st.button("Discover schema", disabled=not ready_to_discover):
        suffix = Path(uploaded.name).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(uploaded.getvalue())
        tmp.close()

        source_config = {
            "file_path": tmp.name,
            "location": "local",
            "delimiter": delimiter,
            "has_header": has_header,
            "multiline": multiline,
        }
        try:
            columns = infer_schema(source_type, source_config)
        except Exception as e:
            st.error(f"Schema inference failed: {e}")
        else:
            st.session_state.inferred_columns = columns
            st.session_state.source_config = source_config
            st.session_state.generated_path = None  # discard any stale generation

    if st.session_state.inferred_columns:
        st.subheader("3. Confirm schema")
        columns = st.session_state.inferred_columns
        st.dataframe(
            pd.DataFrame(
                [{"column": c.name, "pandas dtype": c.pandas_dtype, "target type": c.sql_type} for c in columns]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "This is the required review checkpoint from PRD Section 5.4 — it becomes the fixed DDL "
            "baked into the notebook. Re-upload and re-discover if something looks wrong."
        )
        confirmed = st.checkbox("This schema looks correct")

        output_path = st.text_input(
            "Notebook output path", f"notebooks/{table or 'pipeline'}_ingest.py"
        )

        if st.button("Generate notebook", disabled=not (confirmed and table)):
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

    if st.session_state.generated_path:
        st.success(f"Notebook written to `{st.session_state.generated_path}`")

        st.subheader("4. Review generated code")
        code = Path(st.session_state.generated_path).read_text()
        st.code(code, language="python")

        st.subheader("5. Deploy & schedule")
        st.caption(
            "Requires DATABRICKS_HOST / DATABRICKS_TOKEN set as environment variables "
            "before launching this app — see README."
        )
        job_name = st.text_input("Job name", f"{table}-daily-ingest" if table else "")
        workspace_path = st.text_input(
            "Workspace path", f"/Workspace/Shared/ingestion/{table or 'pipeline'}"
        )
        volume_source_path = st.text_input(
            "Source path in Databricks (Volume/S3 — where the job reads from at run time)"
        )
        c1, c2, c3 = st.columns(3)
        freq = c1.selectbox("Frequency", ["daily", "hourly", "weekly"])
        time_str = c2.text_input("Time (HH:MM, 24h)", "02:00")
        tz = c3.text_input("Timezone", "UTC")
        run_now = st.checkbox("Run immediately after deploying", True)

        deploy_ready = bool(job_name and workspace_path and volume_source_path)
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
                            "table": table,
                            "job_id": result["job_id"],
                            "job_url": result["job_url"],
                        }
                    )
                    st.success(f"Deployed — [view job]({result['job_url']})")
                    st.session_state.inferred_columns = None
                    st.session_state.source_config = None
                    st.session_state.generated_path = None
                    st.rerun()
