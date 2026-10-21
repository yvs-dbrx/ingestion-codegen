"""The table registry: what registers, what doesn't, who can see it, and what the database itself forbids."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import cols
from tests.test_pipelines import FOLDER, acct, deploy, preview, register, spec  # noqa: F401  (acct is a fixture)

CSV = f"{FOLDER}/customers.csv"


def assets(client, acct, **params):
    return client.get("/assets", params=params, headers=acct.headers)


def add_asset(conn, org_id, table, layer="silver", **extra):
    return conn.execute(
        text(
            "INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer) "
            "VALUES (:o, 'main', 'silver', :t, :l) RETURNING id"
        ),
        {"o": org_id, "t": table, "l": layer, **extra},
    ).scalar_one()


# --- registration through deploy ---------------------------------------------------------

def test_deploying_registers_the_bronze_table(client, acct, workspace):  # noqa: F811
    sid = register(client, acct)
    deployed = deploy(client, acct, spec([sid])).json()
    (a,) = assets(client, acct).json()
    assert a["full_name"] == "main.bronze.customers" and a["layer"] == "raw"  # "bronze" is just the schema name here
    assert (a["catalog"], a["schema_name"], a["table_name"]) == ("main", "bronze", "customers")
    assert a["origin"] == CSV
    assert a["produced_by"] == {"pipeline_id": deployed["id"], "job_name": "customers_daily"}
    assert a["row_count"] is None and a["last_run_at"] is None  # never measured yet
    assert a["upstream_count"] == 0 and a["downstream_count"] == 0


def test_a_multi_file_pipeline_registers_each_table(client, acct, workspace):  # noqa: F811
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv", columns=cols(("k", "STRING")))
    deploy(client, acct, spec([a, b], table_names=["alpha", "beta"], mode="multi"))
    got = {x["full_name"]: x["origin"] for x in assets(client, acct).json()}
    assert got == {"main.bronze.alpha": f"{FOLDER}/a.csv", "main.bronze.beta": f"{FOLDER}/b.csv"}


def test_a_pipeline_reports_its_layer(client, acct, workspace):  # noqa: F811
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    assert out["layer"] == "raw"
    assert client.get("/pipelines", headers=acct.headers).json()[0]["layer"] == "raw"


def test_preview_registers_nothing(client, acct, workspace):  # noqa: F811
    sid = register(client, acct)
    assert preview(client, acct, spec([sid])).status_code == 200
    assert assets(client, acct).json() == []


def test_a_failed_deploy_leaves_no_registry_entry(client, acct, workspace):  # noqa: F811
    sid = register(client, acct)
    workspace.fail_create = True
    assert deploy(client, acct, spec([sid])).status_code == 502
    assert assets(client, acct).json() == []


def test_a_name_clash_in_databricks_leaves_no_registry_entry(client, acct, workspace):  # noqa: F811
    workspace.add_foreign_job("customers_daily")
    sid = register(client, acct)
    assert deploy(client, acct, spec([sid])).status_code == 409
    assert assets(client, acct).json() == []


def test_deleting_the_pipeline_keeps_the_table_and_a_redeploy_adopts_it(client, acct, workspace):  # noqa: F811
    sid = register(client, acct)
    first = deploy(client, acct, spec([sid])).json()
    asset_id = assets(client, acct).json()[0]["id"]

    assert client.delete(f"/pipelines/{first['id']}", headers=acct.headers).status_code == 204
    (kept,) = assets(client, acct).json()
    assert kept["id"] == asset_id and kept["produced_by"] is None  # the table still exists in Databricks

    second = deploy(client, acct, spec([sid])).json()
    (again,) = assets(client, acct).json()
    assert again["id"] == asset_id  # same entry, not a duplicate
    assert again["produced_by"]["pipeline_id"] == second["id"]


# --- reading -----------------------------------------------------------------------------

def test_layer_filter_and_validation(client, acct, workspace):  # noqa: F811
    deploy(client, acct, spec([register(client, acct)]))
    assert len(assets(client, acct, layer="raw").json()) == 1
    assert assets(client, acct, layer="bronze").json() == []  # nothing is deduplicated yet
    assert assets(client, acct, layer="silver").json() == []
    assert assets(client, acct, layer="platinum").status_code == 422


def test_assets_are_tenant_scoped(client, acct, make_account, workspace):  # noqa: F811
    deploy(client, acct, spec([register(client, acct)]))
    asset_id = assets(client, acct).json()[0]["id"]
    other = make_account("b@b.com", "B")
    assert assets(client, other).json() == []
    assert client.get(f"/assets/{asset_id}", headers=other.headers).status_code == 404


def test_detail_shows_lineage(client, acct, workspace, test_engine):  # noqa: F811
    deploy(client, acct, spec([register(client, acct)]))
    bronze = assets(client, acct).json()[0]
    with test_engine.begin() as conn:
        silver = add_asset(conn, acct.org_id, "customer")
        conn.execute(
            text("INSERT INTO asset_lineage (org_id, downstream_id, upstream_id) VALUES (:o, :d, :u)"),
            {"o": acct.org_id, "d": silver, "u": bronze["id"]},
        )

    b = client.get(f"/assets/{bronze['id']}", headers=acct.headers).json()
    assert b["upstream"] == [] and b["downstream_count"] == 1
    assert [d["full_name"] for d in b["downstream"]] == ["main.silver.customer"]

    s = client.get(f"/assets/{silver}", headers=acct.headers).json()
    assert s["layer"] == "silver" and s["produced_by"] is None
    assert [u["full_name"] for u in s["upstream"]] == ["main.bronze.customers"] and s["upstream_count"] == 1


# --- what the database itself enforces ---------------------------------------------------

def test_lineage_cannot_cross_organizations(client, make_account, workspace, test_engine):  # noqa: F811
    a = make_account("a@a.com", "A")
    b = make_account("b@b.com", "B")
    with test_engine.begin() as conn:
        mine = add_asset(conn, a.org_id, "mine")
        theirs = add_asset(conn, b.org_id, "theirs")
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:  # org A claims a link to org B's table
            conn.execute(
                text("INSERT INTO asset_lineage (org_id, downstream_id, upstream_id) VALUES (:o, :d, :u)"),
                {"o": a.org_id, "d": mine, "u": theirs},
            )
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:  # ...or files it under org B
            conn.execute(
                text("INSERT INTO asset_lineage (org_id, downstream_id, upstream_id) VALUES (:o, :d, :u)"),
                {"o": b.org_id, "d": mine, "u": theirs},
            )


def test_an_asset_cannot_be_its_own_upstream(client, make_account, test_engine):
    a = make_account("a@a.com", "A")
    with test_engine.begin() as conn:
        x = add_asset(conn, a.org_id, "x")
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            conn.execute(
                text("INSERT INTO asset_lineage (org_id, downstream_id, upstream_id) VALUES (:o, :x, :x)"),
                {"o": a.org_id, "x": x},
            )


def test_a_table_is_registered_once_per_organization(client, make_account, test_engine):
    a = make_account("a@a.com", "A")
    b = make_account("b@b.com", "B")
    with test_engine.begin() as conn:
        add_asset(conn, a.org_id, "t")
        add_asset(conn, b.org_id, "t")  # same name, different org: fine
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            add_asset(conn, a.org_id, "t")


def test_unknown_layers_are_rejected(client, make_account, test_engine):
    a = make_account("a@a.com", "A")
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            add_asset(conn, a.org_id, "t", layer="platinum")


def test_deleting_an_asset_removes_its_lineage_and_an_org_takes_everything(client, make_account, test_engine):
    a = make_account("a@a.com", "A")
    with test_engine.begin() as conn:
        up, down = add_asset(conn, a.org_id, "up", "bronze"), add_asset(conn, a.org_id, "down")
        conn.execute(
            text("INSERT INTO asset_lineage (org_id, downstream_id, upstream_id) VALUES (:o, :d, :u)"),
            {"o": a.org_id, "d": down, "u": up},
        )
        conn.execute(text("DELETE FROM data_assets WHERE id = :i"), {"i": up})
        assert conn.execute(text("SELECT count(*) FROM asset_lineage")).scalar_one() == 0
        conn.execute(text("DELETE FROM orgs WHERE id = :o"), {"o": a.org_id})
        assert conn.execute(text("SELECT count(*) FROM data_assets")).scalar_one() == 0


# --- the schema change -------------------------------------------------------------------

def test_migration_005_upgrades_an_existing_database_and_registers_its_pipelines(scratch_database):
    from tests.conftest import schema_files

    files = schema_files()
    assert files[4].name == "005_data_assets.sql"

    with scratch_database.begin() as conn:  # a database as it was before this change, with a deployed pipeline
        for f in files[:4]:
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name) VALUES ('Old Co')"))
        conn.execute(
            text("INSERT INTO source_registry (org_id, source_type, source_path, schema_json, schema_hash) "
                 "SELECT id, 'csv', '/Volumes/m/l/old.csv', '[]'::jsonb, 'h' FROM orgs")
        )
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema) SELECT id, 'old_job', 'main', 'bronze' FROM orgs"))
        conn.execute(
            text("INSERT INTO pipeline_sources (pipeline_id, source_registry_id, table_name) "
                 "SELECT p.id, s.id, 'old_table' FROM pipelines p, source_registry s")
        )

    for _ in range(2):  # apply, then apply AGAIN — must be harmless and must not duplicate the backfill
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(files[4].read_text(encoding="utf-8"))

    with scratch_database.connect() as conn:
        rows = conn.execute(
            text("SELECT catalog, schema_name, table_name, layer, origin, produced_by IS NOT NULL FROM data_assets")
        ).all()
        assert [tuple(r) for r in rows] == [("main", "bronze", "old_table", "bronze", "/Volumes/m/l/old.csv", True)]
        assert conn.execute(text("SELECT layer, spec FROM pipelines")).one() == ("bronze", {})
        assert conn.execute(text("SELECT to_regclass('asset_lineage')")).scalar_one() is not None
        assert conn.execute(
            text("SELECT count(*) FROM information_schema.columns WHERE table_name='pipeline_runs' AND column_name='metrics'")
        ).scalar_one() == 1
    with pytest.raises(IntegrityError):  # the layer rule is in force on pipelines too
        with scratch_database.begin() as conn:
            conn.execute(text("UPDATE pipelines SET layer = 'platinum'"))


def test_migration_006_relabels_existing_bronze_to_raw_once_and_is_rerunnable(scratch_database):
    from tests.conftest import schema_files

    files = schema_files()
    assert files[5].name == "006_raw_layer.sql"

    with scratch_database.begin() as conn:  # a database as it was before: ingestion pipelines labelled 'bronze'
        for f in files[:5]:
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name) VALUES ('Old Co')"))
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema, layer) SELECT id, 'ingest', 'main', 'landing', 'bronze' FROM orgs"))
        conn.execute(text("INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer) SELECT id, 'main', 'landing', 'customers', 'bronze' FROM orgs"))

    with scratch_database.begin() as conn:
        conn.exec_driver_sql(files[5].read_text(encoding="utf-8"))
    with scratch_database.connect() as conn:
        assert conn.execute(text("SELECT layer FROM pipelines")).scalar_one() == "raw"
        assert conn.execute(text("SELECT layer FROM data_assets")).scalar_one() == "raw"

    # Real Bronze (deduplicated) tables now exist. Re-applying the migration must NOT relabel them.
    with scratch_database.begin() as conn:
        conn.execute(text("INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer) SELECT id, 'main', 'bronze', 'customers', 'bronze' FROM orgs"))
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema, layer) SELECT id, 'dedupe', 'main', 'bronze', 'bronze' FROM orgs"))
    for _ in range(2):
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(files[5].read_text(encoding="utf-8"))
    with scratch_database.connect() as conn:
        assert dict(conn.execute(text("SELECT table_name || '/' || schema_name, layer FROM data_assets")).all()) == {
            "customers/landing": "raw",
            "customers/bronze": "bronze",
        }
        assert dict(conn.execute(text("SELECT job_name, layer FROM pipelines")).all()) == {"ingest": "raw", "dedupe": "bronze"}
        # new pipelines default to raw, and the full layer vocabulary is enforced
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema) SELECT id, 'fresh', 'main', 'x' FROM orgs"))
        assert conn.execute(text("SELECT layer FROM pipelines WHERE job_name = 'fresh'")).scalar_one() == "raw"
        conn.rollback()

    for layer, table, ok in (("silver", "pipelines", True), ("gold", "pipelines", True), ("reference", "pipelines", False),
                             ("reference", "data_assets", True), ("platinum", "data_assets", False)):
        sql = (
            "INSERT INTO pipelines (org_id, job_name, catalog, target_schema, layer) SELECT id, :n, 'm', 's', :l FROM orgs"
            if table == "pipelines"
            else "INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer) SELECT id, 'm', 's', :n, :l FROM orgs"
        )
        try:
            with scratch_database.begin() as conn:
                conn.execute(text(sql), {"n": f"x_{layer}", "l": layer})
            allowed = True
        except IntegrityError:
            allowed = False
        assert allowed == ok, (table, layer)
