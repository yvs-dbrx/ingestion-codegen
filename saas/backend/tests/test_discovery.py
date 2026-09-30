"""POST /discover: reads a schema through the org's own Databricks connection and records it."""
import pytest

import codegen
from app.services import engine
from schema_types import ColumnSchema
from tests.test_connection import connect

CSV = "/Volumes/main/landing/customers.csv"


def discovered(columns, path=CSV, table="customers"):
    return [engine.DiscoveredTable(table, path.rsplit("/", 1)[-1], path, columns)]


def dcol(name, sql_type="STRING"):
    return {"name": name, "sql_type": sql_type, "spark_type": "StringType", "pandas_dtype": "object", "nullable": True, "struct_path": None}


@pytest.fixture()
def fake_engine(monkeypatch):
    """Stands in for the probe job; `calls` records what the endpoint asked the engine for."""
    state = {"calls": [], "result": discovered([dcol("id", "BIGINT"), dcol("name")]), "raises": None}

    def fake_discover(client, **kwargs):
        state["calls"].append((client, kwargs))
        if state["raises"]:
            raise state["raises"]
        return state["result"]

    monkeypatch.setattr(engine, "discover", fake_discover)
    return state


def discover(client, acct, **body):
    return client.post("/discover", json={"source_type": "csv", "source_path": CSV, **body}, headers=acct.headers)


def test_discovery_needs_a_connected_workspace(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    r = discover(client, acct)
    assert r.status_code == 409 and "Connect your Databricks workspace" in r.json()["detail"]
    assert fake_engine["calls"] == []


def test_discovery_registers_the_source_and_uses_the_orgs_own_client(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    r = discover(client, acct, delimiter="|", has_header=False)
    assert r.status_code == 200, r.text
    (item,) = r.json()["sources"]
    assert item["status"] == "created" and item["suggested_table_name"] == "customers"
    assert item["source"]["version"] == 1 and item["source"]["source_path"] == CSV
    assert item["source"]["discovery_config"]["delimiter"] == "|"
    assert [c["name"] for c in item["source"]["columns"]] == ["id", "name"]

    (used_client, kwargs), = fake_engine["calls"]
    assert used_client is workspace  # built from this org's stored connection
    assert kwargs["delimiter"] == "|" and kwargs["has_header"] is False

    listed = client.get("/sources", headers=acct.headers).json()
    assert [s["id"] for s in listed] == [item["source"]["id"]]


def test_rediscovery_reports_unchanged_then_drift(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    assert discover(client, acct).json()["sources"][0]["status"] == "created"
    assert discover(client, acct).json()["sources"][0]["status"] == "unchanged"

    fake_engine["result"] = discovered([dcol("id", "BIGINT"), dcol("name"), dcol("email")])
    item = discover(client, acct).json()["sources"][0]
    assert item["status"] == "drifted" and item["previous_version"] == 1 and item["source"]["version"] == 2


def test_multi_file_discovery_registers_one_source_per_file(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    folder = "/Volumes/main/landing"
    fake_engine["result"] = discovered([dcol("id", "BIGINT")], f"{folder}/a.csv", "a") + discovered(
        [dcol("k")], f"{folder}/b.csv", "b"
    )
    r = discover(client, acct, mode="multi", source_path=folder)
    assert [s["source"]["source_path"] for s in r.json()["sources"]] == [f"{folder}/a.csv", f"{folder}/b.csv"]


@pytest.mark.parametrize(
    "body",
    [
        {"source_path": "relative.csv"},
        {"source_path": '/Volumes/x/a".csv'},
        {"source_path": "/Volumes/x/{y}.csv"},
        {"delimiter": '"'},
        {"source_type": "xml", "row_tag": "a b"},
        {"cluster_id": "not a cluster!"},
        {"schema_definition_path": "/Volumes/x/../y.cpy"},
    ],
)
def test_unsafe_settings_never_reach_the_engine(client, make_account, workspace, fake_engine, body):
    acct = make_account("a@a.com")
    connect(client, acct)
    r = discover(client, acct, **body)
    assert r.status_code == 422 and r.json()["problems"]
    assert fake_engine["calls"] == []


def test_a_discovered_schema_with_unsafe_names_is_refused_and_not_recorded(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    fake_engine["result"] = discovered([dcol("id"), dcol('x"); import os#'), dcol("_ingested_at")])
    r = discover(client, acct)
    assert r.status_code == 422
    assert len(r.json()["problems"]) == 2
    assert client.get("/sources", headers=acct.headers).json() == []


def test_a_folder_listing_with_a_hostile_file_name_is_refused(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    folder = "/Volumes/main/landing"
    fake_engine["result"] = discovered([dcol("id")], f"{folder}/{{__import__('os')}}.csv", "x")
    assert discover(client, acct, mode="multi", source_path=folder).status_code == 422
    assert client.get("/sources", headers=acct.headers).json() == []


def test_engine_failures_are_reported_as_the_right_kind_of_error(client, make_account, workspace, fake_engine):
    acct = make_account("a@a.com")
    connect(client, acct)
    fake_engine["raises"] = engine.EngineInputError("header row is empty")
    r = discover(client, acct)
    assert r.status_code == 422 and "header row is empty" in r.json()["detail"]
    fake_engine["raises"] = engine.EngineRemoteError("RuntimeError: cluster terminated")
    assert discover(client, acct).status_code == 502


def test_discovery_is_tenant_scoped(client, make_account, workspace, fake_engine):
    a = make_account("a@a.com", "A")
    b = make_account("b@b.com", "B")
    connect(client, a)
    discover(client, a)
    assert client.get("/sources", headers=b.headers).json() == []
    assert discover(client, b).status_code == 409  # B has no connection of its own — A's is not usable by B


# --- the adapter itself: it must only ever read REMOTE files, with the caller's client -------

def test_engine_discovery_is_always_remote_and_uses_the_injected_client(monkeypatch):
    seen = {}

    def fake_infer(source_type, config):
        seen.update(config)
        return [ColumnSchema(name="id", pandas_dtype="int64", spark_type="LongType", sql_type="BIGINT", nullable=False)]

    monkeypatch.setattr(codegen, "infer_schema", fake_infer)
    sentinel = object()
    (t,) = engine.discover(
        sentinel, source_type="csv", mode="single", source_path=CSV, schema_definition_path=None,
        delimiter=",", has_header=True, multiline=False, row_tag="record",
        is_record_sequence=False, is_text=False, cluster_id=None,
    )
    assert seen["location"] == "databricks_workspace"  # never the engine's local-file mode
    assert seen["workspace_client"] is sentinel
    assert seen["file_path"] == CSV
    assert t.table_name == "customers" and t.columns[0]["sql_type"] == "BIGINT"


def test_engine_xml_and_cobol_pass_the_client_and_stay_remote(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        codegen, "infer_schema_from_xsd",
        lambda xsd, tag, location="local", workspace_client=None: calls.update(xml=(xsd, tag, location, workspace_client)) or [],
    )
    monkeypatch.setattr(
        codegen, "infer_cobol_schema",
        lambda cpy, data, cluster, seq=False, txt=False, workspace_client=None: calls.update(cobol=(cpy, data, cluster, workspace_client)) or [],
    )
    sentinel = object()
    common = dict(mode="single", delimiter=",", has_header=True, multiline=False, row_tag="row",
                  is_record_sequence=False, is_text=False)
    engine.discover(sentinel, source_type="xml", source_path="/Volumes/x/a.xml", schema_definition_path="/Volumes/x/a.xsd", cluster_id=None, **common)
    engine.discover(sentinel, source_type="cobol", source_path="/Volumes/x/a.dat", schema_definition_path="/Volumes/x/a.cpy", cluster_id="0101-000000-abc123", **common)
    assert calls["xml"] == ("/Volumes/x/a.xsd", "row", "databricks_workspace", sentinel)
    assert calls["cobol"] == ("/Volumes/x/a.cpy", "/Volumes/x/a.dat", "0101-000000-abc123", sentinel)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(source_type="xml", schema_definition_path=None, cluster_id=None),
        dict(source_type="cobol", schema_definition_path=None, cluster_id="0101-000000-abc123"),
        dict(source_type="cobol", schema_definition_path="/Volumes/x/a.cpy", cluster_id=None),
        dict(source_type="xml", schema_definition_path="/Volumes/x/a.xsd", cluster_id=None, mode="multi"),
    ],
)
def test_engine_asks_for_what_it_needs(kwargs):
    base = dict(mode="single", source_path="/Volumes/x/a.dat", delimiter=",", has_header=True, multiline=False,
                row_tag="row", is_record_sequence=False, is_text=False)
    with pytest.raises(engine.EngineInputError):
        engine.discover(object(), **{**base, **kwargs})
