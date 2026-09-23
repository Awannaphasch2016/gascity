"""Tests for city/bin/bd, the bd-compatible shim over the Gas City API.

`gc hook --claim` and the other store paths in gc shell out to a `bd` binary
even when the city runs on the file-backed bead store. The shim answers the
handful of verbs those paths use by talking to the supervisor's HTTP API.
These tests drive the shim's command layer against a fake API so the exact
verbs, exit codes, and JSON shapes gc depends on are pinned.
"""

import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path

import pytest

SHIM_PATH = Path(__file__).resolve().parent.parent / "city" / "bin" / "bd"


def load_shim():
    spec = importlib.util.spec_from_loader(
        "bd_shim", importlib.machinery.SourceFileLoader("bd_shim", str(SHIM_PATH))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shim = load_shim()


class FakeApi:
    """In-memory stand-in for the bead endpoints the shim uses."""

    def __init__(self, beads):
        self.beads = {b["id"]: dict(b) for b in beads}
        self.calls = []

    def get_bead(self, bead_id):
        self.calls.append(("get", bead_id))
        if bead_id not in self.beads:
            raise shim.ApiError(404, "bead %s not found" % bead_id)
        return dict(self.beads[bead_id])

    def update_bead(self, bead_id, body):
        self.calls.append(("update", bead_id, body))
        if bead_id not in self.beads:
            raise shim.ApiError(404, "bead %s not found" % bead_id)
        bead = self.beads[bead_id]
        for key in ("status", "assignee", "title", "description", "type", "priority", "parent"):
            if key in body and body[key] is not None:
                bead["issue_type" if key == "type" else key] = body[key]
        if body.get("metadata"):
            bead.setdefault("metadata", {}).update(body["metadata"])
        labels = set(bead.get("labels") or [])
        labels |= set(body.get("labels") or [])
        labels -= set(body.get("remove_labels") or [])
        bead["labels"] = sorted(labels)
        return dict(bead)

    def close_bead(self, bead_id):
        self.calls.append(("close", bead_id))
        if bead_id not in self.beads:
            raise shim.ApiError(404, "bead %s not found" % bead_id)
        self.beads[bead_id]["status"] = "closed"

    def list_beads(self, params):
        self.calls.append(("list", dict(params)))
        out = []
        for bead in self.beads.values():
            if params.get("status") and bead.get("status") != params["status"]:
                continue
            if params.get("assignee") and bead.get("assignee") != params["assignee"]:
                continue
            if params.get("type") and bead.get("issue_type") != params["type"]:
                continue
            if params.get("rig") and not bead["id"].startswith(params["rig"][:2] + "-"):
                continue
            if not params.get("all") and bead.get("status") == "closed":
                continue
            out.append(dict(bead))
        return out


def run(argv, api, env=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = shim.main(argv, api, env or {}, stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


STEP = {
    "id": "de-2",
    "title": "Discover the requirements",
    "status": "open",
    "issue_type": "task",
    "metadata": {"gc.routed_to": "demo/factory.discoverer", "gc.root_bead_id": "de-1"},
    "dependencies": [],
}


def test_show_prints_bead_as_single_element_array():
    api = FakeApi([STEP])
    code, out, err = run(["show", "--json", "de-2"], api)
    assert code == 0, err
    assert json.loads(out) == [STEP]


def test_show_unknown_bead_reports_not_found_on_stderr():
    api = FakeApi([])
    code, out, err = run(["show", "--json", "de-9"], api)
    assert code == 1
    assert "not found" in err
    assert out == ""


def test_claim_assigns_actor_and_moves_to_in_progress():
    api = FakeApi([STEP])
    code, out, err = run(["update", "de-2", "--claim", "--json"], api, {"BEADS_ACTOR": "demo--factory__discoverer-1-pool"})
    assert code == 0, err
    claimed = json.loads(out)
    assert claimed["assignee"] == "demo--factory__discoverer-1-pool"
    assert claimed["status"] == "in_progress"
    assert ("update", "de-2", {"status": "in_progress", "assignee": "demo--factory__discoverer-1-pool"}) in api.calls


def test_claim_without_actor_is_refused():
    api = FakeApi([STEP])
    code, out, err = run(["update", "de-2", "--claim", "--json"], api, {})
    assert code == 2
    assert "BEADS_ACTOR" in err
    assert not [c for c in api.calls if c[0] == "update"]


def test_claim_of_bead_held_by_someone_else_reports_conflict():
    held = dict(STEP, status="in_progress", assignee="other-seat")
    api = FakeApi([held])
    code, out, err = run(["update", "de-2", "--claim", "--json"], api, {"BEADS_ACTOR": "me"})
    assert code == 1
    assert "already claimed by other-seat" in err
    assert not [c for c in api.calls if c[0] == "update"]


def test_claim_of_own_in_progress_bead_is_idempotent():
    held = dict(STEP, status="in_progress", assignee="me")
    api = FakeApi([held])
    code, out, err = run(["update", "de-2", "--claim", "--json"], api, {"BEADS_ACTOR": "me"})
    assert code == 0, err
    assert json.loads(out)["assignee"] == "me"


def test_claim_of_closed_bead_is_refused():
    api = FakeApi([dict(STEP, status="closed")])
    code, out, err = run(["update", "de-2", "--claim", "--json"], api, {"BEADS_ACTOR": "me"})
    assert code == 1
    assert "closed" in err


def test_update_fans_flags_into_one_api_body():
    api = FakeApi([STEP])
    code, out, err = run(
        [
            "update", "--json", "de-2",
            "--status", "closed",
            "--set-metadata", "gc.outcome=pass",
            "--set-metadata", "factory.note=done=yes",
            "--add-label", "reviewed",
            "--remove-label", "wip",
            "--title", "New title",
        ],
        api,
    )
    assert code == 0, err
    body = [c for c in api.calls if c[0] == "update"][0][2]
    assert body == {
        "status": "closed",
        "metadata": {"gc.outcome": "pass", "factory.note": "done=yes"},
        "labels": ["reviewed"],
        "remove_labels": ["wip"],
        "title": "New title",
    }
    assert json.loads(out)["title"] == "New title"


def test_update_with_no_fields_is_rejected_like_bd():
    api = FakeApi([STEP])
    code, out, err = run(["update", "--json", "de-2"], api)
    assert code == 2
    assert "nothing to update" in err


def test_conditional_release_matches_then_releases():
    held = dict(STEP, status="in_progress", assignee="me")
    api = FakeApi([held])
    code, out, err = run(
        ["update", "de-2", "--if-assignee", "me", "--if-status", "in_progress", "--status", "open", "--assignee", ""],
        api,
    )
    assert code == 0, err
    assert api.beads["de-2"]["status"] == "open"
    assert api.beads["de-2"]["assignee"] == ""


def test_conditional_release_precondition_failure_exits_13():
    held = dict(STEP, status="in_progress", assignee="someone-else")
    api = FakeApi([held])
    code, out, err = run(
        ["update", "de-2", "--if-assignee", "me", "--if-status", "in_progress", "--status", "open", "--assignee", ""],
        api,
    )
    assert code == 13
    assert not [c for c in api.calls if c[0] == "update"]


def test_close_closes_every_id_and_records_reason():
    api = FakeApi([STEP, dict(STEP, id="de-3")])
    code, out, err = run(["close", "--force", "--json", "--reason", "done", "de-2", "de-3"], api)
    assert code == 0, err
    assert api.beads["de-2"]["status"] == "closed"
    assert api.beads["de-3"]["status"] == "closed"
    assert api.beads["de-2"]["metadata"]["close_reason"] == "done"
    assert [b["id"] for b in json.loads(out)] == ["de-2", "de-3"]


def test_dep_list_down_returns_blockers_with_dependency_type():
    blocker = dict(STEP, id="de-1", title="root")
    step = dict(STEP, dependencies=[{"issue_id": "de-2", "depends_on_id": "de-1", "type": "blocks"}])
    api = FakeApi([blocker, step])
    code, out, err = run(["dep", "list", "de-2", "--json"], api)
    assert code == 0, err
    deps = json.loads(out)
    assert [d["id"] for d in deps] == ["de-1"]
    assert deps[0]["dependency_type"] == "blocks"


def test_dep_list_without_dependencies_is_empty_array():
    api = FakeApi([STEP])
    code, out, err = run(["dep", "list", "de-2", "--json"], api)
    assert code == 0, err
    assert json.loads(out) == []


def test_list_scopes_to_rig_and_maps_filters():
    mine = dict(STEP, status="in_progress", assignee="seat-1")
    other = dict(STEP, id="de-3", status="in_progress", assignee="seat-2")
    api = FakeApi([mine, other])
    code, out, err = run(
        ["list", "--status", "in_progress", "--assignee=seat-1", "--json", "--limit=1"],
        api,
        {"GC_RIG": "demo"},
    )
    assert code == 0, err
    assert [b["id"] for b in json.loads(out)] == ["de-2"]
    params = [c for c in api.calls if c[0] == "list"][0][1]
    assert params["rig"] == "demo"
    assert params["status"] == "in_progress"
    assert params["assignee"] == "seat-1"
    assert params["all"] == "true"


def test_list_without_rig_is_city_wide_and_hides_closed_by_default():
    api = FakeApi([STEP, dict(STEP, id="de-3", status="closed")])
    code, out, err = run(["list", "--json"], api, {})
    assert code == 0, err
    assert [b["id"] for b in json.loads(out)] == ["de-2"]
    params = [c for c in api.calls if c[0] == "list"][0][1]
    assert "rig" not in params
    assert "all" not in params


def test_list_honours_limit():
    api = FakeApi([STEP, dict(STEP, id="de-3")])
    code, out, err = run(["list", "--json", "--limit", "1"], api, {})
    assert code == 0, err
    assert len(json.loads(out)) == 1


def test_query_filters_ephemeral_and_status_clauses():
    wisp = dict(STEP, id="de-9", ephemeral=True, status="open")
    api = FakeApi([STEP, wisp])
    code, out, err = run(["query", "--json", "ephemeral=true AND status=open", "--limit=0"], api, {"GC_RIG": "demo"})
    assert code == 0, err
    assert [b["id"] for b in json.loads(out)] == ["de-9"]


def test_query_with_unknown_key_is_refused():
    api = FakeApi([STEP])
    code, out, err = run(["query", "--json", "colour=blue"], api, {})
    assert code == 2
    assert "colour" in err


def test_unknown_verb_is_refused_with_exit_64():
    api = FakeApi([STEP])
    code, out, err = run(["sql", "--json", "SELECT 1"], api)
    assert code == 64
    assert "file bead store" in err
    assert api.calls == []


def test_ready_is_forwarded_to_gc_ready(monkeypatch):
    seen = {}

    def fake_exec(binary, argv):
        seen["binary"] = binary
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(shim, "exec_gc", fake_exec)
    api = FakeApi([])
    with pytest.raises(SystemExit):
        run(["ready", "--json", "--metadata-field", "gc.routed_to=x"], api, {"GC_BIN": "/opt/gc"})
    assert seen == {"binary": "/opt/gc", "argv": ["ready", "--json", "--metadata-field", "gc.routed_to=x"]}


def test_http_api_builds_city_urls_and_request_header():
    api = shim.HttpApi("http://127.0.0.1:8372/v0/city/factory/")
    req = api.request("POST", "/bead/de-2/update", {"status": "closed"})
    assert req.full_url == "http://127.0.0.1:8372/v0/city/factory/bead/de-2/update"
    assert req.get_header("X-gc-request") == "1"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data.decode()) == {"status": "closed"}


def test_api_base_comes_from_factory_api_env():
    assert shim.api_base({"FACTORY_API": "http://x/v0/city/c"}) == "http://x/v0/city/c"
    with pytest.raises(shim.ShimError):
        shim.api_base({})
