"""Smoke tests: catalog loading, profile CRUD, validation paths (no network)."""
import contextlib
import json
import os
import pathlib
import tempfile

_TMP = tempfile.mkdtemp(prefix="mkt-util-test-")
os.environ["MARKETO_UTIL_DB"] = os.path.join(_TMP, "test.db")

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.backends.null import Keyring as NullKeyring  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from marketo_util import db as dbmod  # noqa: E402
from marketo_util.app import app  # noqa: E402

# Hermetic by default: never touch the real OS credential store. Tests that
# exercise the credential-store path install _FakeKeyring explicitly.
keyring.set_keyring(NullKeyring())

client = TestClient(app)


@contextlib.contextmanager
def _isolated_db(db_name: str):
    """Point the process-wide MARKETO_UTIL_DB at a fresh empty DB, then restore it.

    starlette 1.x's TestClient handles each request on a fresh worker thread, so
    `db.get_conn()` re-reads MARKETO_UTIL_DB on every new thread — the env var is
    the real lever, and it must be restored (with the main thread's cached
    connection dropped) so later tests see the suite DB again.
    """
    saved = os.environ.get("MARKETO_UTIL_DB")
    os.environ["MARKETO_UTIL_DB"] = os.path.join(_TMP, db_name)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("MARKETO_UTIL_DB", None)
        else:
            os.environ["MARKETO_UTIL_DB"] = saved
        dbmod._local.conn = None  # main thread: drop stale pin, re-read env on next use


PHASE1_IDS = [
    "leads.describe",
    "leads.describe2",
    "leads.get_by_id",
    "leads.get_by_filter",
    "leads.get_by_list",
    "leads.get_by_program",
    "leads.partitions",
    "leads.list_membership",
    "leads.program_membership",
    "leads.smart_campaign_membership",
    "leads.schema_fields",
    "leads.field_by_name",
    "activities.types",
    "activities.paging_token",
    "activities.lead_activities",
    "activities.lead_changes",
    "activities.deleted_leads",
]


def test_health_and_catalog():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["catalog_calls"] >= 15, f"expected >=15 calls, got {body['catalog_calls']}"
    groups = client.get("/api/catalog").json()["groups"]
    assert groups, "catalog should have at least one group"
    ids = [c["id"] for g in groups for c in g["calls"]]
    for expected in PHASE1_IDS + PHASE2_IDS:
        assert expected in ids, f"missing catalog entry: {expected}"
    # group ordering: Leads before Activities before Static Lists
    group_names = [g["group"] for g in groups]
    assert group_names.index("Leads") < group_names.index("Activities")
    assert group_names.index("Activities") < group_names.index("Static Lists")
    for g in ("Programs", "Opportunities", "Sales Persons", "Custom Objects"):
        assert g in group_names, f"missing group: {g}"


def test_catalog_entry_shape():
    entry = client.get("/api/catalog/leads.get_by_id").json()
    assert entry["method"] == "GET"
    path_params = {p["name"] for p in entry["params"]["path"]}
    assert "id" in path_params
    required = [p for p in entry["params"]["path"] if p["required"]]
    assert required and required[0]["name"] == "id"
    # field selection param is 'fields' (current API name) with the picker source
    query = {p["name"]: p for p in entry["params"]["query"]}
    assert query["fields"]["type"] == "lookup"
    assert query["fields"]["source"] == "describe"


def test_phase1_required_params():
    def entry(cid):
        return client.get(f"/api/catalog/{cid}").json()

    def qparams(e):
        return {p["name"]: p for p in e["params"]["query"]}

    f = entry("leads.get_by_filter")
    assert qparams(f)["filterType"]["required"] is True
    assert qparams(f)["filterValues"]["required"] is True

    a = entry("activities.lead_activities")
    assert qparams(a)["nextPageToken"]["required"] is True
    assert qparams(a)["activityTypeIds"]["required"] is False

    c = entry("activities.lead_changes")
    assert qparams(c)["nextPageToken"]["required"] is True
    assert qparams(c)["fields"]["required"] is True

    d = entry("activities.deleted_leads")
    assert qparams(d)["nextPageToken"]["required"] is True

    l = entry("leads.get_by_list")
    assert {p["name"] for p in l["params"]["path"]} == {"listId"}


PHASE2_IDS = [
    "leads.sync",
    "leads.push",
    "leads.submit_form",
    "leads.merge",
    "leads.associate",
    "leads.delete",
    "leads.change_program_status",
    "lists.get",
    "lists.by_id",
    "lists.add",
    "lists.remove",
    "lists.is_member",
    "programs.list",
    "programs.by_id",
    "programs.by_name",
    "programs.members",
    "programs.set_status",
    "programs.member_data",
    "programs.delete_members",
    "programs.members_describe",
    "programs.member_fields",
    "programs.member_field_by_name",
    "opportunities.describe",
    "opportunities.get",
    "opportunities.sync",
    "opportunities.delete",
    "opportunities.fields",
    "opportunities.field_by_name",
    "salespersons.describe",
    "salespersons.get",
    "salespersons.sync",
    "salespersons.delete",
    "customobjects.list",
    "customobjects.describe",
    "customobjects.query",
    "customobjects.sync",
    "customobjects.delete",
    "customobjects.types",
    "customobjects.type_describe",
    "customobjects.add_fields",
    "customobjects.approve",
    "customobjects.data_types",
    "customobjects.linkable",
]


PHASE3_IDS = [
    "landingpages.list",
    "landingpages.by_id",
    "landingpages.by_name",
    "landingpages.content",
    "landingpages.variables",
    "landingpages.full_content",
    "landingpages.domains",
    "emails.list",
    "emails.by_id",
    "emails.by_name",
    "emails.content",
    "emails.variables",
    "emails.cc_fields",
    "emails.full_content",
    "emails.send_sample",
    "forms.list",
    "forms.by_id",
    "forms.by_name",
    "forms.fields",
    "forms.available_fields",
    "forms.program_member_fields",
    "forms.thank_you",
    "forms.used_by",
    "forms.approve",
    "forms.clone",
    "forms.delete",
    "smartlists.list",
    "smartlists.by_id",
    "smartlists.by_name",
    "smartlists.by_program",
    "smartlists.by_campaign",
    "smartlists.clone",
    "smartlists.delete",
    "smartcampaigns.list",
    "smartcampaigns.by_id",
    "smartcampaigns.by_name",
    "smartcampaigns.activate",
    "smartcampaigns.deactivate",
    "smartcampaigns.trigger",
    "campaigns.list",
    "campaigns.by_id",
    "snippets.list",
    "snippets.by_id",
    "snippets.content",
    "snippets.dynamic_content",
    "segments.list",
    "segments.by_id",
]


def test_phase3_entries():
    """All 47 asset entries load, group correctly, and writes carry confirms."""
    groups = client.get("/api/catalog").json()["groups"]
    ids = {c["id"]: c for g in groups for c in g["calls"]}
    for expected in PHASE3_IDS:
        assert expected in ids, f"missing catalog entry: {expected}"

    def entry(cid):
        return ids[cid]

    for g in ("Landing Pages", "Emails", "Forms", "Lists & Campaigns", "Other Assets"):
        assert g in [x["group"] for x in groups], f"missing group: {g}"

    # write calls that act on live assets carry confirm dialogs
    for cid in ("emails.send_sample", "forms.approve", "forms.delete",
                "smartlists.delete", "smartcampaigns.activate",
                "smartcampaigns.deactivate", "smartcampaigns.trigger"):
        assert entry(cid)["confirm"], f"{cid} must have a confirm warning"

    # clone endpoints use form-urlencoded bodies (Marketo requirement)
    for cid in ("forms.clone", "smartlists.clone"):
        assert entry(cid)["params"]["body"]["mode"] == "record", cid
        assert entry(cid)["params"]["body"]["encoding"] == "form", cid

    # trigger campaign uses the nested {input: {leads: […]}} body
    t = entry("smartcampaigns.trigger")
    assert t["params"]["body"]["mode"] == "values"
    assert t["params"]["body"]["wrapKey"] == "leads"
    assert t["params"]["body"]["itemKey"] == "id"
    assert t["params"]["body"]["maxPerCall"] == 100

    # permissions per the endpoint reference
    assert entry("smartcampaigns.activate")["permission"] == "Activate Campaign"
    assert entry("smartcampaigns.deactivate")["permission"] == "Deactivate Campaign"
    assert entry("smartcampaigns.trigger")["permission"] == "Read-Write Campaigns"
    assert entry("campaigns.list")["permission"] == "Read-Only Campaigns"
    assert entry("landingpages.domains")["permission"] == "Read-Only Redirect Rules"

    # read-only calls stay GET
    for cid in ("landingpages.list", "emails.cc_fields", "forms.available_fields",
                "segments.list", "snippets.content"):
        assert entry(cid)["method"] == "GET", cid


def test_phase2_confirm_and_modes():
    """Destructive calls carry confirm dialogs; body modes match the docs."""
    def entry(cid):
        return client.get(f"/api/catalog/{cid}").json()

    for cid in (
        "leads.merge", "leads.delete", "lists.remove",
        "programs.delete_members", "opportunities.delete",
        "salespersons.delete", "customobjects.delete", "customobjects.approve",
    ):
        assert entry(cid)["confirm"], f"{cid} must have a confirm warning"

    # sync-mode calls
    for cid in ("leads.sync", "leads.push", "opportunities.sync",
                "salespersons.sync", "customobjects.sync", "programs.member_data",
                "customobjects.add_fields"):
        assert entry(cid)["params"]["body"]["mode"] == "sync", cid

    # values-mode calls
    for cid in ("leads.delete", "programs.set_status", "programs.delete_members",
                "opportunities.delete", "salespersons.delete", "customobjects.delete"):
        assert entry(cid)["params"]["body"]["mode"] == "values", cid

    # select options must be well-formed
    def all_selects(e):
        out = []
        for section in ("path", "query"):
            out.extend(p for p in e["params"].get(section) or [] if p.get("type") == "select")
        b = e["params"].get("body") or {}
        for k in ("fixed", "fields"):
            out.extend(p for p in b.get(k) or [] if p.get("type") == "select")
        return out

    s = entry("leads.sync")
    acts = [p for p in all_selects(s) if p["name"] == "action"]
    assert acts and {o["value"] for o in acts[0]["options"]} == {
        "createOrUpdate", "createOnly", "updateOnly", "createDuplicate"}

    o = entry("opportunities.get")
    ft = [p for p in o["params"]["query"] if p["name"] == "filterType"][0]
    assert {x["value"] for x in ft["options"]} == {"externalOpportunityId", "marketoGUID"}

    # repeat query params (id=…&id=…)
    for cid in ("lists.add", "lists.remove", "lists.is_member"):
        ip = [p for p in entry(cid)["params"]["query"] if p["name"] == "id"][0]
        assert ip.get("repeat") is True, cid

    # DELETE method is allowed and used for Remove from List
    assert entry("lists.remove")["method"] == "DELETE"


def test_build_form_encoding_body():
    """Clone calls must serialize as form-urlencoded (data=), not JSON."""
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}
    _, _, json_body, files, data = executor._build(
        calls["smartlists.clone"],
        {"path": {"id": "4363"}, "query": {},
         "body": {"name": "2018 Leads Qualified",
                  "folder": {"id": 31, "type": "Folder"}}},
    )
    assert json_body is None
    assert files is None
    assert data == {
        "name": "2018 Leads Qualified",
        "folder": json.dumps({"id": 31, "type": "Folder"}),
    }, data

    # JSON-string folder (typed by a user) also accepted
    _, _, _, _, data2 = executor._build(
        calls["forms.clone"],
        {"path": {"id": "736"}, "query": {},
         "body": {"name": "v2", "folder": '{"id":293,"type":"Folder"}', "description": "d"}},
    )
    assert data2 == {
        "name": "v2",
        "folder": '{"id":293,"type":"Folder"}',
        "description": "d",
    }, data2

    # bool coercion
    assert executor._form_value(True) == "true"
    assert executor._form_value(12) == "12"


def test_build_wrapkey_body():
    """Trigger Campaign body: {\"input\": {\"leads\": [{\"id\": …}]}}."""
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}
    _, _, body, _, _ = executor._build(
        calls["smartcampaigns.trigger"],
        {"path": {"id": "1234"}, "query": {}, "body": {"values": "318592, 318593"}},
    )
    assert body == {"input": {"leads": [{"id": 318592}, {"id": 318593}]}}, body

    # >100 leads rejected
    import pytest
    too_many = ",".join(str(1000 + i) for i in range(101))
    with pytest.raises(executor.ValidationError):
        executor._build(
            calls["smartcampaigns.trigger"],
            {"path": {"id": "1234"}, "query": {}, "body": {"values": too_many}},
        )


def test_build_values_mode_body():
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}

    # flat integer ids wrapped under itemKey -> {"input": [{"id": …}]}
    _, _, body, _, _ = executor._build(
        calls["leads.delete"],
        {"path": {}, "query": {}, "body": {"values": "235, 766"}},
    )
    assert body == {"input": [{"id": 235}, {"id": 766}]}, body

    # statusName is an extra scalar member alongside the array
    _, _, body, _, _ = executor._build(
        calls["programs.set_status"],
        {"path": {"programId": "55"}, "query": {},
         "body": {"values": "1800, 1801", "statusName": "Influenced"}},
    )
    assert body == {"statusName": "Influenced", "input": [{"leadId": 1800}, {"leadId": 1801}]}, body

    # deleteBy select passes through as a string
    _, _, body, _, _ = executor._build(
        calls["opportunities.delete"],
        {"path": {}, "query": {},
         "body": {"values": "001ABC", "deleteBy": "idField"}},
    )
    assert body["deleteBy"] == "idField" and body["input"] == [{"externalOpportunityId": "001ABC"}]


def test_build_sync_mode_body():
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}
    rows = [{"email": "a@b.c", "firstName": "A"}, {"email": "d@e.f"}]
    _, _, body, _, _ = executor._build(
        calls["leads.sync"],
        {"path": {}, "query": {},
         "body": {"fixed": {"action": "createOnly"}, "rows": rows}},
    )
    assert body == {"action": "createOnly", "input": rows}, body

    # empty rows rejected
    import pytest
    with pytest.raises(executor.ValidationError):
        executor._build(calls["leads.sync"], {"path": {}, "query": {}, "body": {"fixed": {}, "rows": []}})


def test_repeat_query_params():
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}
    _, query, _, _, _ = executor._build(
        calls["lists.add"],
        {"path": {"listId": "87"}, "query": {"id": "1, 2, 3"}, "body": {}},
    )
    assert query == [("id", "1"), ("id", "2"), ("id", "3")], query
    assert calls["lists.add"].raw["path"].replace("{listId}", "87") == "/rest/v1/lists/87/leads.json"


def test_required_body_fields_validated():
    from marketo_util import catalog, executor

    calls = {c.id: c for c in catalog.load_catalog()}
    # statusName is required for Set Program Member Status
    try:
        executor._validate_params(
            calls["programs.set_status"],
            {"path": {"programId": "55"}, "query": {}, "body": {"values": "1", "statusName": ""}},
        )
        raise AssertionError("expected ValidationError for missing statusName")
    except executor.ValidationError as exc:
        assert "Status to set" in str(exc)
    # leadId required column is enforced in the UI; server still validates path/query
    try:
        executor._validate_params(calls["programs.set_status"], {"path": {}, "query": {}, "body": {}})
        raise AssertionError("expected ValidationError for missing programId")
    except executor.ValidationError:
        pass


def test_profile_crud_and_instance_normalization():
    r = client.post(
        "/api/config/profiles",
        json={
            "name": "Test Co",
            "instance": "100-TEST-1",
            "client_id": "abc-123",
            "client_secret": "shh",
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["instance"] == "100-test-1.mktorest.com"
    assert p["is_active"] is True  # first profile auto-activates

    profiles = client.get("/api/config/profiles").json()["profiles"]
    assert len(profiles) == 1

    # duplicate name rejected
    r2 = client.post(
        "/api/config/profiles",
        json={"name": "Test Co", "instance": "100-X", "client_id": "x", "client_secret": "y"},
    )
    assert r2.status_code == 400

    # second profile; switch active
    r3 = client.post(
        "/api/config/profiles",
        json={"name": "Second", "instance": "https://200-BBB-456.mktorest.com/", "client_id": "x", "client_secret": "y"},
    )
    assert r3.status_code == 200
    second = r3.json()
    assert second["instance"] == "200-bbb-456.mktorest.com"
    assert second["is_active"] is False
    r4 = client.post(f"/api/config/active?profile_id={second['id']}")
    assert r4.status_code == 200 and r4.json()["is_active"] is True
    active = client.get("/api/config/profiles").json()["profiles"]
    assert sum(1 for p in active if p["is_active"]) == 1

    # secrets never leave the server
    assert "secret" not in r.json()
    assert all("secret" not in p for p in client.get("/api/config/profiles").json()["profiles"])


def test_default_data_dir_platform_branches(tmp_path):
    """Default data dir follows OS conventions: APPDATA (win), Library (mac), XDG (linux)."""
    import marketo_util.db as dbmod

    home = tmp_path / "home"

    # Windows: %APPDATA%
    assert (
        dbmod._default_data_dir("nt", "win32", home, {"APPDATA": str(home / "Roaming")})
        == home / "Roaming" / "MarketoUtility"
    )
    # Windows: no APPDATA -> home fallback
    assert dbmod._default_data_dir("nt", "win32", home, {}) == home / "MarketoUtility"

    # macOS: ~/Library/Application Support/
    assert (
        dbmod._default_data_dir("posix", "darwin", home, {})
        == home / "Library" / "Application Support" / "MarketoUtility"
    )

    # Linux: XDG default, then explicit XDG_DATA_HOME
    assert (
        dbmod._default_data_dir("posix", "linux", home, {})
        == home / ".local" / "share" / "marketo-utility"
    )
    assert (
        dbmod._default_data_dir("posix", "linux", home, {"XDG_DATA_HOME": str(home / "xdg")})
        == home / "xdg" / "marketo-utility"
    )


class _FakeKeyring(KeyringBackend):
    """In-memory credential store for exercising the keyring path."""

    priority = 5.0

    def __init__(self) -> None:
        super().__init__()
        self.d: dict[tuple[str, str], str] = {}

    def get_password(self, resource, username):
        return self.d.get((resource, username))

    def set_password(self, resource, username, password):
        self.d[(resource, username)] = password

    def delete_password(self, resource, username):
        self.d.pop((resource, username), None)


def _install_fake_keyring() -> _FakeKeyring:
    import keyring
    from marketo_util import secrets

    fake = _FakeKeyring()
    keyring.set_keyring(fake)
    secrets.reset_probe()
    return fake


def test_secret_in_credential_store_when_available():
    """With a credential store present, the secret lands there, not in the DB."""
    from marketo_util import config, secrets

    fake = _install_fake_keyring()
    assert secrets.available() is True

    r = client.post(
        "/api/config/profiles",
        json={"name": "KR Co", "instance": "100-KR-1", "client_id": "kid", "client_secret": "topsecret"},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # resolvable for auth...
    assert config.get_profile(pid).client_secret == "topsecret"
    # ...kept in the credential store...
    assert any(v == "topsecret" for v in fake.d.values())
    # ...not in the database
    row = dbmod.get_conn().execute(
        "SELECT client_secret FROM profiles WHERE id = ?", (pid,)
    ).fetchone()
    assert row["client_secret"] == ""

    # deleting the connection also removes the credential-store entry
    assert client.delete(f"/api/config/profiles/{pid}").status_code == 200
    assert all(v != "topsecret" for v in fake.d.values())


def test_secret_falls_back_to_db_without_credential_store():
    """No usable credential store -> secret stays in the local DB (old behavior)."""
    import keyring

    from marketo_util import config, secrets

    keyring.set_keyring(NullKeyring())
    secrets.reset_probe()
    assert secrets.available() is False

    r = client.post(
        "/api/config/profiles",
        json={"name": "DB Co", "instance": "100-DB-1", "client_id": "kid", "client_secret": "fallback-secret"},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert config.get_profile(pid).client_secret == "fallback-secret"
    row = dbmod.get_conn().execute(
        "SELECT client_secret FROM profiles WHERE id = ?", (pid,)
    ).fetchone()
    assert row["client_secret"] == "fallback-secret"
    client.delete(f"/api/config/profiles/{pid}")


def test_legacy_db_secret_migrates_to_credential_store():
    """A pre-existing DB row (secret in the DB) moves to the store on first read."""
    from marketo_util import config

    fake = _install_fake_keyring()

    # simulate an old DB written before secrets.py existed
    cur = dbmod.get_conn().execute(
        "INSERT INTO profiles (name, instance, client_id, client_secret) VALUES (?, ?, ?, ?)",
        ("Legacy Co", "100-LG-9.mktorest.com", "kid", "legacy-secret"),
    )
    dbmod.get_conn().commit()
    pid = cur.lastrowid

    assert config.get_profile(pid).client_secret == "legacy-secret"
    assert any(v == "legacy-secret" for v in fake.d.values())  # migrated into the store
    row = dbmod.get_conn().execute(
        "SELECT client_secret FROM profiles WHERE id = ?", (pid,)
    ).fetchone()
    assert row["client_secret"] == ""  # DB copy blanked
    client.delete(f"/api/config/profiles/{pid}")


def test_token_cached_in_memory(monkeypatch):
    """get_token reuses the in-memory cache; force=True refreshes. No DB involved."""
    import marketo_util.auth as authmod
    from marketo_util import config

    authmod._token_cache.clear()
    calls: list[int] = []

    def fake_fetch(profile):
        calls.append(profile.id)
        return authmod.TokenInfo(token="tok-1", expires_in=3600)

    monkeypatch.setattr(authmod, "fetch_token", fake_fetch)
    p = config.Profile(
        id=99999,
        name="t",
        instance="100-T.mktorest.com",
        client_id="i",
        client_secret="s",
        is_active=True,
    )
    assert authmod.get_token(p) == "tok-1"
    assert authmod.get_token(p) == "tok-1"
    assert len(calls) == 1, "second call must hit the in-memory cache"
    assert authmod.get_token(p, force=True) == "tok-1"
    assert len(calls) == 2
    authmod._token_cache.clear()


def test_run_call_validation_without_network():
    # missing required path param -> 400 before any network access
    r = client.post("/api/call/leads.get_by_id", json={"params": {}})
    assert r.status_code == 400
    assert "Lead ID" in r.json()["error"]["friendly"]

    # unknown call id -> 404
    r2 = client.post("/api/call/nope.nope", json={"params": {}})
    assert r2.status_code == 404


def test_run_call_with_no_profile():
    """Fresh DB (no profiles) must give a friendly 400, not a network call."""
    import threading

    result = {}

    def run():
        with TestClient(app) as c:
            r = c.post("/api/call/leads.get_by_id", json={"params": {"path": {"id": "5"}}})
            result["status"] = r.status_code
            result["body"] = r.json()

    with _isolated_db("empty.db"):
        t = threading.Thread(target=run)
        t.start()
        t.join()
    assert result["status"] == 400, result
    assert "No Marketo connection" in result["body"]["error"]["friendly"]


def test_lookup_endpoint_no_profile():
    """Field picker source must fail friendly (no network) without a connection."""
    import threading

    result = {}

    def run():
        with TestClient(app) as c:
            r = c.get("/api/lookup/describe")
            result["status"] = r.status_code
            result["body"] = r.json()

    with _isolated_db("empty2.db"):
        t = threading.Thread(target=run)
        t.start()
        t.join()
    assert result["status"] == 400, result
    assert "No Marketo connection" in result["body"]["detail"]

    # unknown source -> 404 (needs an active profile — create + activate one if
    # this test runs in isolation, before the profile-CRUD tests)
    from marketo_util import config as cfg

    try:
        p = cfg.create_profile("Lookup Check Co", "100-LKP-1", "lkp-1", "shh")
    except cfg.ConfigError:
        p = None
    if p is not None:
        cfg.set_active(p.id)
    assert client.get("/api/lookup/bogus").status_code == 404


def test_lookup_field_mapping():
    from marketo_util.lookups import _dedupe, _map_field

    # classic describe shape
    m = _map_field({"displayName": "Email Address", "dataType": "email",
                    "rest": {"name": "email", "readOnly": False}})
    assert m and m["name"] == "email" and m["label"] == "Email Address"
    # flat shape
    m2 = _map_field({"name": "leadScore", "restApiName": "leadScore", "dataType": "integer"})
    assert m2["name"] == "leadScore"
    assert _map_field({"id": 5}) is None
    dup = _dedupe([{"name": "a"}, {"name": "b"}, {"name": "a"}])
    assert [f["name"] for f in dup] == ["a", "b"]


def test_error_translation_table():
    from marketo_util.executor import _friendly

    # P8: wording comes from catalog/error_codes.json (official descriptions)
    m = _friendly(429, "606", "Rate limit exceeded")
    assert "rate limit" in m.lower() and "Rate limit exceeded" in m
    m = _friendly(429, "607", "daily quota")
    assert "quota" in m.lower() and "daily quota" in m
    assert "Invalid data" in _friendly(200, "1003", "Too many results")
    assert _friendly(500, "", "").startswith("Marketo returned HTTP 500")


# ================= Phase 4 — bulk wizards =================


class FakeResp:
    def __init__(self, status_code=200, payload=None, content=None, text=None):
        self.status_code = status_code
        self.content = content if content is not None else json.dumps(payload if payload is not None else {}).encode()
        self.text = text if text is not None else self.content.decode()

    def json(self):
        return json.loads(self.text)


def _make_bulk_profile():
    from marketo_util import config as cfg

    try:
        p = cfg.create_profile("Bulk Test Co", "100-BLK-777", "blk-123", "shh")
    except cfg.ConfigError:
        p = cfg.list_profiles()[0]
    return cfg.set_active(p.id)


def test_bulk_ops_loaded():
    r = client.get("/api/bulk/ops")
    assert r.status_code == 200
    ops = {o["id"]: o for o in r.json()["ops"]}
    for cid in (
        "bulk.extract_leads",
        "bulk.extract_activities",
        "bulk.import_leads",
        "bulk.import_program_members",
        "bulk.import_custom_objects",
    ):
        assert cid in ops, f"missing bulk op: {cid}"
    for op in ops.values():
        assert "/rest/" not in op["create"]["path"], "bulk paths must not include /rest/"
        if op["kind"] == "extract":
            for k in ("enqueue", "status", "file", "cancel"):
                assert k in op, f"{op['id']} missing endpoint '{k}'"
        else:
            for k in ("status", "failures", "warnings"):
                assert k in op, f"{op['id']} missing endpoint '{k}'"
            assert op["create"].get("multipart")
    el = ops["bulk.extract_leads"]
    assert any(f["name"] == "filterType" and f.get("dynamicFields") for f in el["form"])
    assert any(f["name"] == "fields" and f["type"] == "lookup" for f in el["form"])
    # extract group ships before import group
    order = [o["id"] for o in r.json()["ops"]]
    assert order.index("bulk.extract_leads") < order.index("bulk.import_leads")


def test_bulk_unknown_op_and_job():
    assert client.get("/api/bulk/op/bulk.nope").status_code == 404
    assert client.get("/api/bulk/jobs/99999").status_code == 404
    assert client.post("/api/bulk/op/bulk.nope/start", json={"form": {}}).status_code == 404


def test_bulk_build_lead_extract_body():
    import pytest

    from marketo_util import bulk

    body = bulk._lead_extract_body(
        {
            "filterType": "staticListId",
            "listId": "42",
            "fields": "email,firstName",
            "format": "csv",
            "columnHeaderNames": '{"email": "Email Address"}',
        }
    )
    assert body == {
        "fields": ["email", "firstName"],
        "format": "CSV",
        "columnHeaderNames": {"email": "Email Address"},
        "filter": {"staticListId": 42},
    }
    with pytest.raises(bulk.BulkError, match="31 days"):
        bulk._lead_extract_body(
            {"filterType": "createdAt", "startAt": "2026-01-01T00:00:00Z", "endAt": "2026-03-01T00:00:00Z", "fields": "id"}
        )
    with pytest.raises(bulk.BulkError, match="at least one field"):
        bulk._lead_extract_body({"filterType": "staticListId", "listId": "1", "fields": ""})


def test_bulk_build_activity_extract_body():
    import pytest

    from marketo_util import bulk

    body = bulk._activity_extract_body(
        {
            "startAt": "2026-01-01T00:00:00Z",
            "endAt": "2026-01-31T00:00:00Z",
            "activityTypeIds": "1,12,13",
            "format": "TSV",
            "fields": "leadId,activityDate",
        }
    )
    assert body["filter"]["activityTypeIds"] == [1, 12, 13]
    assert body["fields"] == ["leadId", "activityDate"]
    with pytest.raises(bulk.BulkError, match="activity types"):
        bulk._activity_extract_body(
            {"startAt": "2026-01-01T00:00:00Z", "endAt": "2026-01-02T00:00:00Z", "primaryAttributeValueIds": "5"}
        )
    with pytest.raises(bulk.BulkError, match="not both"):
        bulk._activity_extract_body(
            {
                "startAt": "2026-01-01T00:00:00Z",
                "endAt": "2026-01-02T00:00:00Z",
                "activityTypeIds": "1",
                "primaryAttributeValueIds": "5",
                "primaryAttributeValues": "x",
            }
        )
    with pytest.raises(bulk.BulkError, match="50"):
        bulk._activity_extract_body(
            {
                "startAt": "2026-01-01T00:00:00Z",
                "endAt": "2026-01-02T00:00:00Z",
                "activityTypeIds": "1",
                "primaryAttributeValueIds": ",".join(str(i) for i in range(51)),
            }
        )


def test_bulk_build_import_requests():
    from marketo_util import bulk

    p, q, b, files = bulk._build_create_request(
        bulk.get_op("bulk.import_leads"),
        {"format": "csv", "lookupField": "id", "listId": "9"},
        (b"x", "l.csv"),
    )
    assert p == "/bulk/v1/leads.json"
    assert q == [("format", "csv"), ("lookupField", "id"), ("listId", "9")]
    assert files["file"][0] == "l.csv" and files["file"][2] == "text/csv"
    p2, q2, _, _ = bulk._build_create_request(
        bulk.get_op("bulk.import_program_members"),
        {"programId": "77", "programMemberStatus": "On List", "format": "tsv"},
        (b"x", "m.tsv"),
    )
    assert p2 == "/bulk/v1/program/77/members/import.json"
    assert q2 == [("format", "tsv"), ("programMemberStatus", "On List")]
    p3, q3, _, _ = bulk._build_create_request(
        bulk.get_op("bulk.import_custom_objects"),
        {"apiName": "car_c", "format": "csv"},
        (b"x", "c.csv"),
    )
    assert p3 == "/bulk/v1/customobjects/car_c/import.json"
    assert q3 == [("format", "csv")]


def test_bulk_start_extract_flow(monkeypatch):
    import marketo_util.auth as authmod
    from marketo_util import bulk

    calls = []

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        calls.append((method, path, json))
        if path.endswith("/create.json"):
            return FakeResp(200, {"success": True, "result": [{"exportId": "exp-123", "status": "Created", "format": "CSV"}]})
        if path.endswith("/enqueue.json"):
            return FakeResp(200, {"success": True, "result": [{"exportId": "exp-123", "status": "Queued"}]})
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    p = _make_bulk_profile()
    job = bulk.start_job(
        p,
        bulk.get_op("bulk.extract_leads"),
        {"filterType": "staticListId", "listId": "42", "fields": "email", "format": "CSV"},
    )
    assert job["state"] == "queued" and job["marketoJobId"] == "exp-123"
    assert calls[0][1] == "/bulk/v1/leads/export/create.json"
    assert calls[0][2] == {"fields": ["email"], "format": "CSV", "filter": {"staticListId": 42}}
    assert calls[1][1] == "/bulk/v1/leads/export/exp-123/enqueue.json"
    assert any(j["id"] == job["id"] for j in bulk.list_jobs(p.id))


def test_bulk_refresh_download_and_route(monkeypatch):
    import marketo_util.auth as authmod
    from marketo_util import bulk

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path.endswith("/create.json"):  # self-sufficient fallback (see below)
            return FakeResp(200, {"result": [{"exportId": "exp-123", "status": "Created", "format": "CSV"}]})
        if path.endswith("/enqueue.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-123", "status": "Queued"}]})
        if path.endswith("/status.json"):
            return FakeResp(
                200,
                {"result": [{"exportId": "exp-123", "status": "Completed", "numberOfRecords": 15423, "fileSize": 12342}]},
            )
        if path.endswith("/file.json"):
            return FakeResp(200, content=b"email,firstName\na@b.c,Al")
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    p = _make_bulk_profile()
    existing = [j for j in bulk.list_jobs(p.id) if j["opId"] == "bulk.extract_leads"]
    if existing:
        job = existing[0]  # usually the one from test_bulk_start_extract_flow
    else:  # self-sufficient when run in isolation
        job = bulk.start_job(
            p,
            bulk.get_op("bulk.extract_leads"),
            {"filterType": "staticListId", "listId": "42", "fields": "email", "format": "CSV"},
        )
    refreshed = bulk.refresh_job(p, job["id"])
    assert refreshed["state"] == "completed"
    assert refreshed["counts"] == {"records": 15423, "fileSize": 12342}
    content, mime, fname = bulk.download_file(p, job["id"])
    assert content == b"email,firstName\na@b.c,Al"
    assert mime == "text/csv" and fname.endswith(".csv")
    # routes
    r = client.get(f"/api/bulk/jobs/{job['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = client.get(f"/api/bulk/jobs/{job['id']}/file")
    assert r2.status_code == 200 and r2.content == b"email,firstName\na@b.c,Al"
    assert 'filename="marketo-extract-leads-exp-123.csv"' in r2.headers["content-disposition"]


def test_bulk_import_flow(monkeypatch):
    import pytest

    import marketo_util.auth as authmod
    from marketo_util import bulk

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path == "/bulk/v1/leads.json":
            return FakeResp(200, {"result": [{"batchId": 1040, "importId": "1040", "status": "Queued"}]})
        if path == "/bulk/v1/leads/batch/1040.json":  # lead import status has NO /status suffix
            return FakeResp(
                200,
                {"result": [{"batchId": 1040, "status": "Complete", "numOfLeadsProcessed": 8, "numOfRowsFailed": 1, "numOfRowsWithWarning": 2}]},
            )
        if path == "/bulk/v1/leads/batch/1040/failures.json":
            return FakeResp(200, content=b"email,Import Failure Reason\nbad@x,Invalid")
        if path == "/bulk/v1/leads/batch/1040/warnings.json":
            return FakeResp(404, text="not found")
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    p = _make_bulk_profile()
    job = bulk.start_job(
        p,
        bulk.get_op("bulk.import_leads"),
        {"format": "csv", "lookupField": "email"},
        (b"email,firstName\na@b.c,Al", "leads.csv"),
    )
    assert job["marketoJobId"] == "1040" and job["canFailures"] is False
    refreshed = bulk.refresh_job(p, job["id"])
    assert refreshed["state"] == "completed"
    assert refreshed["counts"] == {"processed": 8, "failed": 1, "warnings": 2}
    content, mime, fname = bulk.download_report(p, job["id"], "failures")
    assert mime == "text/csv" and fname.endswith("failures-1040.csv")
    with pytest.raises(bulk.BulkError, match="No warnings file"):
        bulk.download_report(p, job["id"], "warnings")


def test_bulk_cancel_extract(monkeypatch):
    import pytest

    import marketo_util.auth as authmod
    from marketo_util import bulk

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path.endswith("/create.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-999", "status": "Created"}]})
        if path.endswith("/enqueue.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-999", "status": "Queued"}]})
        if path.endswith("/cancel.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-999", "status": "Canceled"}]})
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    p = _make_bulk_profile()
    job = bulk.start_job(
        p,
        bulk.get_op("bulk.extract_activities"),
        {"startAt": "2026-01-01T00:00:00Z", "endAt": "2026-01-02T00:00:00Z"},
    )
    assert job["state"] == "queued" and job["canCancel"]
    assert bulk.cancel_job(p, job["id"])["state"] == "canceled"
    with pytest.raises(bulk.BulkError, match="already finished"):
        bulk.cancel_job(p, job["id"])


def test_bulk_validation_errors():
    import pytest

    from marketo_util import bulk

    p = _make_bulk_profile()
    with pytest.raises(bulk.BulkError, match="Which leads"):
        bulk.start_job(p, bulk.get_op("bulk.extract_leads"), {"fields": "email"})
    im = bulk.get_op("bulk.import_leads")
    with pytest.raises(bulk.BulkError, match="Choose a data file"):
        bulk.start_job(p, im, {"format": "csv"})
    with pytest.raises(bulk.BulkError, match="10 MB"):
        bulk.start_job(p, im, {"format": "csv"}, (b"x" * (10 * 1024 * 1024 + 1), "big.csv"))
    with pytest.raises(bulk.BulkError, match="Program status"):
        bulk.start_job(
            p,
            bulk.get_op("bulk.import_program_members"),
            {"programId": "5", "format": "csv"},
            (b"email\na@b.c", "m.csv"),
        )


def test_recent_calls_recorded_and_listed():
    from marketo_util import config

    with _isolated_db("recent.db"):
        p = config.create_profile("T", "100-TEST-001", "cid-test", "secret")
        config.set_active(p.id)
        dbmod.record_recent(p.id, "leads.get_by_id", True)
        dbmod.record_recent(p.id, "leads.merge", False)
        rows = dbmod.list_recent(p.id, 10)
        assert [r["call_id"] for r in rows] == ["leads.merge", "leads.get_by_id"]  # newest first
        assert rows[0]["ok"] is False and rows[1]["ok"] is True
        assert rows[0]["created_at"]

        # Endpoint: no profile -> empty list
        with _isolated_db("recent-empty.db"):
            assert client.get("/api/recent").json() == {"recent": []}


# ================= Phase 7 — lead field library =================

P7_BROWSE_PAGE = {
    "requestId": "p7",
    "success": True,
    "moreResult": False,
    "result": [
        {"displayName": "Email Address", "name": "email", "description": None,
         "dataType": "email", "length": 255, "isHidden": False, "isSensitive": True, "isCustom": False},
        {"displayName": "First Name", "name": "firstName", "description": None,
         "dataType": "string", "length": 255, "isHidden": False, "isSensitive": False, "isCustom": False},
        {"displayName": "NPS Score", "name": "npsScore", "description": "Latest NPS",
         "dataType": "integer", "isHidden": False, "isSensitive": False, "isCustom": True},
    ],
}


def _p7_fake_browse():
    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path == "/rest/v1/leads/schema/fields.json":
            return FakeResp(200, P7_BROWSE_PAGE)
        raise AssertionError("unexpected path: " + path)

    return fake_request


def test_p7_map_field_captures_tags():
    from marketo_util.lookups import _map_field

    nps = _map_field(P7_BROWSE_PAGE["result"][2])
    assert nps["name"] == "npsScore" and nps["isCustom"] is True
    assert nps["description"] == "Latest NPS" and nps["fieldLen"] is None

    email = _map_field(P7_BROWSE_PAGE["result"][0])
    assert email["name"] == "email" and email["isCustom"] is False
    assert email["isSensitive"] is True and email["fieldLen"] == 255 and email["readOnly"] is False

    # classic describe.json shape: no isCustom (unknown), readOnly from rest child
    d = _map_field({"id": 2, "displayName": "Company Name", "dataType": "string", "length": 255,
                    "rest": {"name": "company", "readOnly": False}})
    assert d["name"] == "company" and d["isCustom"] is None and d["readOnly"] is False
    assert _map_field({"id": 5}) is None


def test_p7_field_library_flow(monkeypatch):
    """Refresh stores the tagged list; /api/fields + the picker source read the store."""
    import marketo_util.auth as authmod
    from marketo_util import config

    monkeypatch.setattr(authmod, "request", _p7_fake_browse())
    with _isolated_db("p7-fields.db"):
        p = config.create_profile("P7 Co", "100-P7-1", "p7-1", "shh")
        config.set_active(p.id)

        # empty until loaded
        body = client.get("/api/fields").json()
        assert body["fields"] == [] and body["stats"]["count"] == 0

        # one-click load: fetch -> store -> stats
        r = client.post("/api/fields/refresh")
        assert r.status_code == 200 and r.json()["ok"] is True
        stats = r.json()["stats"]
        assert (stats["count"], stats["custom"], stats["system"], stats["unknown"]) == (3, 1, 2, 0)
        assert stats["lastLoaded"]

        # stored list: custom-first, then alphabetical; shape + tags preserved
        fields = client.get("/api/fields").json()["fields"]
        assert [f["name"] for f in fields] == ["npsScore", "email", "firstName"]
        email = next(f for f in fields if f["name"] == "email")
        assert email["isCustom"] is False and email["isSensitive"] is True and email["fieldLen"] == 255

        # picker source must now be served from the store with ZERO network
        def no_network(profile, method, path, **kw):
            raise AssertionError("picker must not hit the network after first load")

        monkeypatch.setattr(authmod, "request", no_network)
        r = client.get("/api/lookup/describe")
        assert r.status_code == 200 and len(r.json()["fields"]) == 3

        # re-refresh is an upsert (no duplicates)
        monkeypatch.setattr(authmod, "request", _p7_fake_browse())
        r = client.post("/api/fields/refresh")
        assert r.json()["stats"]["count"] == 3

        # clear empties the library
        r = client.delete("/api/fields")
        assert r.status_code == 200 and r.json()["cleared"] == 3
        body = client.get("/api/fields").json()
        assert body["fields"] == [] and body["stats"]["count"] == 0


def test_p7_lookup_autostores_on_first_use(monkeypatch):
    """First use of a picker auto-fetches and stores, so later uses are offline."""
    import marketo_util.auth as authmod
    from marketo_util import config, lookups

    monkeypatch.setattr(authmod, "request", _p7_fake_browse())
    with _isolated_db("p7-auto.db"):
        p = config.create_profile("P7 Auto Co", "100-P7-2", "p7-2", "shh")
        config.set_active(p.id)
        assert lookups.stored_fields(p) == []
        r = client.get("/api/lookup/describe")
        assert r.status_code == 200 and len(r.json()["fields"]) == 3
        assert len(lookups.stored_fields(p)) == 3, "first use must store the list"


def test_p7_describe_fallback_unknown_custom(monkeypatch):
    """Browse down -> describe.json fallback works; isCustom becomes unknown."""
    import marketo_util.auth as authmod
    from marketo_util import config, lookups

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path == "/rest/v1/leads/schema/fields.json":
            return FakeResp(404, text="nope")
        if path == "/rest/v1/leads/describe.json":
            return FakeResp(200, {"requestId": "x", "success": True, "result": [
                {"id": 2, "displayName": "Company Name", "dataType": "string", "length": 255,
                 "rest": {"name": "company", "readOnly": False}},
                {"id": 3, "displayName": "First Name", "dataType": "string", "length": 255,
                 "rest": {"name": "firstName", "readOnly": False}},
            ]})
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    with _isolated_db("p7-fb.db"):
        p = config.create_profile("P7 FB Co", "100-P7-3", "p7-3", "shh")
        config.set_active(p.id)
        stats = lookups.load_and_store(p)
        assert stats["count"] == 2 and stats["unknown"] == 2 and stats["custom"] == 0
        fields = lookups.stored_fields(p)
        assert all(f["isCustom"] is None for f in fields)
        assert {f["name"] for f in fields} == {"company", "firstName"}


def test_p7_fields_routes_no_profile():
    import threading

    result = {}

    def run():
        with TestClient(app) as c:
            result["get"] = c.get("/api/fields").status_code
            result["refresh"] = c.post("/api/fields/refresh").status_code
            result["delete"] = c.delete("/api/fields").status_code

    with _isolated_db("p7-empty.db"):
        t = threading.Thread(target=run)
        t.start()
        t.join()
    assert result == {"get": 400, "refresh": 400, "delete": 400}, result


# ================= Phase 8 — settings, call log, error codes, bulk save =========

def _p8_fake_lead_get():
    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path == "/rest/v1/lead/5.json":
            return FakeResp(
                200,
                {"requestId": "p8", "success": True,
                 "result": {"id": 5, "email": "a@b.c", "firstName": "Al"}},
            )
        raise AssertionError("unexpected " + path)

    return fake_request


def _p8_ensure_active_profile():
    from marketo_util import config

    p = config.get_active() or (config.list_profiles() or [None])[0]
    if p is None:
        p = config.create_profile("P8 Fallback", "100-P8-FB", "p8-fb", "shh")
    return config.set_active(p.id)


def _p8_reset_settings():
    """Put the suite DB's settings back to defaults (log off, bulk off, default dir)."""
    import sqlite3

    conn = dbmod.get_conn()
    try:
        conn.execute(
            """INSERT INTO settings (id, export_dir, log_calls, save_bulk_files)
               VALUES (1, '', 0, 0)
               ON CONFLICT(id) DO UPDATE
                 SET export_dir = '', log_calls = 0, save_bulk_files = 0""",
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()


def test_p8_settings_defaults_off():
    """Fresh DB: both toggles OFF (opt-in) and the default export dir."""
    import threading

    result = {}

    def run():
        with TestClient(app) as c:
            r = c.get("/api/settings")
            result["status"] = r.status_code
            result["settings"] = r.json()

    with _isolated_db("p8-fresh.db"):
        t = threading.Thread(target=run)
        t.start()
        t.join()
    assert result["status"] == 200
    s = result["settings"]
    assert s["log_calls"] is False, "log_calls must default OFF"
    assert s["save_bulk_files"] is False, "save_bulk_files must default OFF"
    assert s["export_dir"] == s["default_dir"]
    assert s["default_dir"].endswith("exports")


def test_p8_settings_put_get_and_validation():
    d = os.path.join(_TMP, "p8-exports")
    r = client.put("/api/settings", json={"export_dir": d, "log_calls": True, "save_bulk_files": True})
    assert r.status_code == 200 and r.json()["ok"] is True
    s = r.json()["settings"]
    assert os.path.isdir(d), "PUT must create the export folder"
    assert s["export_dir"] == d
    assert s["log_calls"] is True and s["save_bulk_files"] is True

    # a partial update keeps the values it doesn't send
    r = client.put("/api/settings", json={"log_calls": False})
    s = r.json()["settings"]
    assert s["log_calls"] is False
    assert s["save_bulk_files"] is True and s["export_dir"] == d

    # relative paths are rejected
    r = client.put("/api/settings", json={"export_dir": "relative\\dir"})
    assert r.status_code == 400 and r.json()["ok"] is False

    # the test-dir helper reports writability
    r = client.post("/api/settings/test-dir", json={"path": os.path.join(_TMP, "p8-testdir")})
    assert r.json()["ok"] is True
    r = client.post("/api/settings/test-dir", json={"path": "relative"})
    assert r.json()["ok"] is False


def test_p8_error_code_mapping():
    from marketo_util import errors
    from marketo_util.executor import _friendly

    # known code: official description + docs comment + Marketo's own message
    m = _friendly(429, "606", "Rate limit exceeded")
    assert "Max rate limit exceeded" in m
    assert "100 calls" in m
    assert "Rate limit exceeded" in m

    # 1003 is overloaded: officially "Invalid data", but also "filter matched too many leads"
    m = _friendly(200, "1003", "Too many leads matched")
    assert "Invalid data" in m
    assert "narrow the filter" in m.lower()
    assert "Too many leads matched" in m

    # unknown code: HTTP status + Marketo's raw message
    m = _friendly(500, "9999", "mystery failure")
    assert m.startswith("Marketo returned HTTP 500")
    assert "mystery failure" in m

    assert errors.doc_url().endswith("/error-codes")


def test_p8_interpret_adds_doc():
    from marketo_util import errors
    from marketo_util.executor import _interpret

    r = _interpret(FakeResp(400, {"error": {"code": "1003", "message": "bad data"}}), 12)
    assert r["ok"] is False and r["status"] == 400
    e = r["error"]
    assert e["code"] == "1003"
    assert e["doc"] == errors.doc_url()
    assert "Invalid data" in e["friendly"] and "bad data" in e["friendly"]

    # no code at all -> no doc link, raw message still surfaced
    r = _interpret(FakeResp(500, text="server blew up"), 5)
    assert r["error"]["code"] == "" and r["error"]["doc"] is None
    assert "server blew up" in r["error"]["friendly"]


def test_p8_calllog_writes_daily_file(monkeypatch):
    """log_calls ON -> one JSONL line in marketo-calls-YYYY-MM-DD.jsonl."""
    import datetime

    import marketo_util.auth as authmod

    from marketo_util import config

    monkeypatch.setattr(authmod, "request", _p8_fake_lead_get())
    p = config.create_profile("P8 Log Co", "100-P8-LOG", "p8-log", "shh")
    config.set_active(p.id)
    d = os.path.join(_TMP, "p8-log-on")
    r = client.put("/api/settings", json={"export_dir": d, "log_calls": True})
    assert r.status_code == 200

    r = client.post("/api/call/leads.get_by_id", json={"params": {"path": {"id": "5"}}})
    assert r.status_code == 200 and r.json()["ok"] is True

    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    f = os.path.join(d, f"marketo-calls-{day}.jsonl")
    assert os.path.exists(f), "the daily call log was not written"
    lines = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    e = lines[-1]
    assert e["call_id"] == "leads.get_by_id"
    assert e["profile"] == "P8 Log Co"
    assert e["ok"] is True and e["status"] == 200
    assert e["params"] == {"path": {"id": "5"}}
    assert e["data"]["result"]["email"] == "a@b.c"


def test_p8_calllog_off_writes_nothing(monkeypatch):
    import datetime

    import marketo_util.auth as authmod

    monkeypatch.setattr(authmod, "request", _p8_fake_lead_get())
    _p8_ensure_active_profile()
    d = os.path.join(_TMP, "p8-log-off")
    r = client.put("/api/settings", json={"export_dir": d, "log_calls": False})
    assert r.status_code == 200

    r = client.post("/api/call/leads.get_by_id", json={"params": {"path": {"id": "5"}}})
    assert r.status_code == 200 and r.json()["ok"] is True

    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert not os.path.exists(os.path.join(d, f"marketo-calls-{day}.jsonl"))


def test_p8_bulk_save_toggled(monkeypatch):
    """save_bulk_files ON -> download also writes the file to the export dir (+header);
    OFF -> no header."""
    import marketo_util.auth as authmod

    from marketo_util import bulk, config

    def fake_request(profile, method, path, *, params=None, json=None, files=None, data=None):
        if path.endswith("/create.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-p8", "status": "Created", "format": "CSV"}]})
        if path.endswith("/enqueue.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-p8", "status": "Queued"}]})
        if path.endswith("/status.json"):
            return FakeResp(200, {"result": [{"exportId": "exp-p8", "status": "Completed", "numberOfRecords": 10, "fileSize": 30}]})
        if path.endswith("/file.json"):
            return FakeResp(200, content=b"email,firstName\na@b.c,Al")
        raise AssertionError("unexpected " + path)

    monkeypatch.setattr(authmod, "request", fake_request)
    p = _make_bulk_profile()
    existing = [j for j in bulk.list_jobs(p.id) if j["opId"] == "bulk.extract_leads" and j["state"] == "completed"]
    if existing:
        job = existing[0]
    else:  # self-sufficient when run in isolation
        job = bulk.start_job(
            p, bulk.get_op("bulk.extract_leads"),
            {"filterType": "staticListId", "listId": "42", "fields": "email", "format": "CSV"},
        )
        bulk.refresh_job(p, job["id"])

    d = os.path.join(_TMP, "p8-bulk-exports")
    r = client.put("/api/settings", json={"export_dir": d, "save_bulk_files": True})
    assert r.status_code == 200

    r = client.get(f"/api/bulk/jobs/{job['id']}/file")
    assert r.status_code == 200 and r.content == b"email,firstName\na@b.c,Al"
    saved_to = r.headers.get("x-marketo-util-saved-to")
    assert saved_to, "expected X-Marketo-Util-Saved-To when saving is ON"
    assert os.path.isfile(saved_to), f"saved file missing: {saved_to}"
    with open(saved_to, "rb") as f:
        assert f.read() == b"email,firstName\na@b.c,Al"

    # toggle off -> same download, but no save header
    r = client.put("/api/settings", json={"save_bulk_files": False})
    assert r.status_code == 200
    r = client.get(f"/api/bulk/jobs/{job['id']}/file")
    assert r.status_code == 200
    assert "x-marketo-util-saved-to" not in {k.lower() for k in r.headers}


def test_p8_log_write_failure_never_breaks(monkeypatch):
    """An unwritable log location must never break the actual call."""
    import marketo_util.auth as authmod

    monkeypatch.setattr(authmod, "request", _p8_fake_lead_get())
    _p8_ensure_active_profile()

    # point the log at a path that cannot be created: a regular file as its parent
    blocker = os.path.join(_TMP, "p8-blocker.txt")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("i am a file, not a folder")
    bad_dir = os.path.join(blocker, "sub")
    conn = dbmod.get_conn()
    try:
        cur = conn.execute("UPDATE settings SET export_dir = ?, log_calls = 1 WHERE id = 1", (bad_dir,))
        if cur.rowcount == 0:
            conn.execute("INSERT INTO settings (id, export_dir, log_calls) VALUES (1, ?, 1)", (bad_dir,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:
        r = client.post("/api/call/leads.get_by_id", json={"params": {"path": {"id": "5"}}})
        assert r.status_code == 200 and r.json()["ok"] is True, "a logging failure must not break the call"
        assert not os.path.isdir(bad_dir), "the broken path must not be created"
    finally:
        _p8_reset_settings()


def test_p8_settings_restored_after_suite():
    """Housekeeping: the suite DB is left at defaults for anything that runs after."""
    _p8_reset_settings()
    s = client.get("/api/settings").json()
    assert s["log_calls"] is False and s["save_bulk_files"] is False
    assert s["export_dir"] == s["default_dir"]


# ================= P10 — auth alignment + access-token visibility =================


def test_p10_error_table_has_601_602():
    """The official token error codes (601 invalid / 602 expired) must be mapped."""
    p = pathlib.Path(__file__).resolve().parent.parent / "catalog" / "error_codes.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["codes"]["601"]["desc"] == "Access token invalid"
    assert d["codes"]["602"]["desc"] == "Access token expired"
    assert d["codes"]["601"]["cat"] == "auth" and d["codes"]["602"]["cat"] == "auth"


def test_p10_extract_error_parses_errors_array():
    """Marketo's response-level shape {"success": false, "errors": [{code, message}]}."""
    from marketo_util import executor

    body = json.dumps(
        {"requestId": "e42b", "success": False,
         "errors": [{"code": "602", "message": "Access token expired"}]})
    code, msg = executor._extract_error(body)
    assert code == "602" and msg == "Access token expired"
    # the older single-error shape still parses
    code2, msg2 = executor._extract_error(json.dumps({"error": {"code": "1003", "message": "x"}}))
    assert code2 == "1003" and msg2 == "x"
    # friendly text now uses the official 602 description
    assert "expired" in executor._friendly(401, "602", "Access token expired").lower()


def test_p10_request_retries_on_601(monkeypatch):
    """401 or body-level 601/602 must force one token refresh + retry."""
    import marketo_util.auth as authmod
    from marketo_util import config

    authmod._token_cache.clear()
    fetches: list[str] = []

    def fake_fetch(profile):
        tok = "tok-A" if not fetches else "tok-B"
        fetches.append(tok)
        return authmod.TokenInfo(token=tok, expires_in=3600, scope="apis@test")

    def fake_httpx_request(method, url, **kw):
        authz = kw.get("headers", {}).get("Authorization", "")
        if authz.endswith("tok-A"):
            return FakeResp(401, {"requestId": "r1", "success": False,
                                  "errors": [{"code": "601", "message": "Unauthorized"}]})
        return FakeResp(200, {"result": []})

    monkeypatch.setattr(authmod, "fetch_token", fake_fetch)
    monkeypatch.setattr(authmod.httpx, "request", fake_httpx_request)
    try:
        p = config.Profile(id=99998, name="t", instance="100-T.mktorest.com",
                           client_id="i", client_secret="s", is_active=True)
        resp = authmod.request(p, "GET", "/rest/v1/leads.json")
        assert resp.status_code == 200
        assert fetches == ["tok-A", "tok-B"], "a 601 must force exactly one refresh+retry"
    finally:
        authmod._token_cache.clear()


def test_p10_invalid_client_message(monkeypatch):
    """Identity 401 invalid_client → actionable secret-rotation remediation."""
    import marketo_util.auth as authmod
    from marketo_util import config

    monkeypatch.setattr(
        authmod.httpx, "post",
        lambda *a, **kw: FakeResp(401, {"error": "invalid_client",
                                        "error_description": "Bad client credentials"}))
    p = config.Profile(id=99997, name="t", instance="100-T.mktorest.com",
                       client_id="i", client_secret="s", is_active=True)
    try:
        authmod.fetch_token(p)
        assert False, "expected AuthError"
    except authmod.AuthError as e:
        m = str(e)
        assert "invalid_client" in m
        assert "secret" in m.lower()
        assert "Integration" in m  # points at Marketo Admin → Integration → Web Services


def test_p10_auth_status_endpoint(monkeypatch):
    """GET /api/auth/status: masked token + scope + expiry; full token never sent."""
    import marketo_util.auth as authmod
    from marketo_util import config

    authmod._token_cache.clear()
    p = config.create_profile("p10-status", "100-T.mktorest.com", "cid-p10", "sec-p10")
    config.set_active(p.id)
    try:
        # state: none (fresh cache)
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        body = r.json()
        assert body["profile"]["id"] == p.id
        assert body["token"]["state"] == "none"

        # warm the cache with a known token, then verify visibility + masking
        monkeypatch.setattr(
            authmod, "fetch_token",
            lambda prof: authmod.TokenInfo(
                token="abcd1234efgh5678ijkl9012", expires_in=3600, scope="apis@joesj.com"))
        authmod.get_token(p)
        r = client.get("/api/auth/status")
        t = r.json()["token"]
        assert t["state"] == "valid"
        assert t["scope"] == "apis@joesj.com"
        assert t["tokenMasked"] == "abcd1234…9012"
        assert t["expiresAt"] > t["obtainedAt"]
        assert "abcd1234efgh5678ijkl9012" not in r.text, "full token must never reach the client"
    finally:
        config.delete_profile(p.id)
        authmod._token_cache.clear()
