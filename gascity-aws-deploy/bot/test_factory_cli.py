"""The intake command: `factory new` and `factory restart`.

Both are sequences of things that already exist — git, `gc rig add`,
`gc sling`, the bridge's /projects — so the tests check the sequence and what
each step is handed, with the commands and HTTP calls recorded instead of run.

    Run: python3 -m pytest bot/test_factory_cli.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory  # noqa: E402

ROSTER = {"members": {"you": ["requirements", "code_review"]}, "admins": ["you"]}


class FakeRunner:
    """Records commands; answers `gc sling --json` with a workflow id."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.fail_on: str | None = None
        self.workflow_ids = iter(["ba-1", "ba-40"])

    def __call__(self, argv: list[str], cwd: str | None = None) -> str:
        self.commands.append(list(argv))
        if self.fail_on and self.fail_on in argv:
            raise subprocess.CalledProcessError(1, argv, output="", stderr=f"{self.fail_on} exploded")
        if argv[:1] == ["gc"] and "sling" in argv:
            return json.dumps({"ok": True, "success": True, "target": argv[argv.index("sling") + 1],
                               "workflow_id": next(self.workflow_ids), "routed": True, "queued": False, "dry_run": False})
        if argv[:1] == ["gc"] and "add" in argv:
            return json.dumps({"ok": True, "name": "bakery"})
        return ""


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.projects: dict[str, dict[str, Any]] = {}
        self.down = False

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if self.down:
            raise ConnectionError("bridge down")
        if (method, path) == ("POST", "/projects"):
            assert body is not None
            self.projects[body["name"]] = dict(body)
            return dict(body, topics={})
        if (method, path) == ("GET", "/projects"):
            return {"projects": list(self.projects.values())}
        if method == "POST" and path.endswith("/restart"):
            name = path.split("/")[2]
            self.projects[name]["workflow_id"] = (body or {})["workflow_id"]
            return self.projects[name]
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def setup(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("# Bakery\nA site for a bakery in Perth.\n", encoding="utf-8")
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(ROSTER), encoding="utf-8")
    runner, bridge = FakeRunner(), FakeBridge()
    fac = factory.Factory(
        city_dir=str(tmp_path / "city"), pack_dir=str(tmp_path / "packs/factory"),
        projects_dir=str(tmp_path / "projects"), run=runner, bridge=bridge,
    )
    return fac, runner, bridge, str(brief), str(roster), tmp_path


def commands_named(runner: FakeRunner, *head: str) -> list[list[str]]:
    return [c for c in runner.commands if c[:len(head)] == list(head)]


# --- new -----------------------------------------------------------------------

def test_new_lays_out_the_project_registers_the_rig_slings_and_tells_the_bridge(setup):
    fac, runner, bridge, brief, roster, tmp_path = setup

    result = fac.new("bakery", brief_path=brief, roster_path=roster)

    project_dir = tmp_path / "projects" / "bakery"
    assert (project_dir / "docs" / "brief.md").read_text(encoding="utf-8").startswith("# Bakery")
    assert json.loads((project_dir / "roster.json").read_text(encoding="utf-8")) == ROSTER
    assert (project_dir / ".gitignore").exists()

    git = commands_named(runner, "git")
    assert git[0][:4] == ["git", "init", "-b", "main"]
    assert any("commit" in c for c in git)

    add = commands_named(runner, "gc")[0]
    assert add[1:3] == ["--city", str(tmp_path / "city")]
    assert add[3:5] == ["rig", "add"] and str(project_dir) in add
    assert add[add.index("--name") + 1] == "bakery"
    assert add[add.index("--include") + 1] == str(tmp_path / "packs/factory")

    sling = commands_named(runner, "gc")[1]
    assert sling[3:] == ["sling", "bakery/factory.discoverer", "website-factory", "--formula", "--json"]

    assert bridge.calls[-1] == ("POST", "/projects", {"name": "bakery", "rig": "bakery", "workflow_id": "ba-1", "roster": ROSTER})
    assert result == {"name": "bakery", "rig": "bakery", "workflow_id": "ba-1", "project_dir": str(project_dir)}


def test_new_refuses_a_name_that_cannot_be_a_rig_or_a_topic(setup):
    fac, *_ , brief, roster, _ = setup
    for bad in ("Bakery", "a b", "-x", "x" * 50, ""):
        with pytest.raises(factory.IntakeError):
            fac.new(bad, brief_path=brief, roster_path=roster)


def test_new_refuses_an_existing_project_dir_before_touching_anything(setup):
    fac, runner, bridge, brief, roster, tmp_path = setup
    (tmp_path / "projects" / "bakery").mkdir(parents=True)

    with pytest.raises(factory.IntakeError, match="exists"):
        fac.new("bakery", brief_path=brief, roster_path=roster)
    assert runner.commands == [] and bridge.calls == []


def test_new_refuses_a_roster_without_members(setup, tmp_path):
    fac, runner, *_ , brief, _, _ = setup
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"members": {}}), encoding="utf-8")
    with pytest.raises(factory.IntakeError, match="members"):
        fac.new("bakery", brief_path=brief, roster_path=str(empty))
    assert runner.commands == []


def test_a_failed_gc_step_surfaces_its_stderr(setup):
    fac, runner, bridge, brief, roster, _ = setup
    runner.fail_on = "sling"
    with pytest.raises(factory.IntakeError, match="sling exploded"):
        fac.new("bakery", brief_path=brief, roster_path=roster)
    assert bridge.calls == []


def test_a_bridge_outage_after_the_sling_is_reported_with_the_workflow_id(setup):
    fac, runner, bridge, brief, roster, _ = setup
    bridge.down = True
    with pytest.raises(factory.IntakeError, match="ba-1"):
        fac.new("bakery", brief_path=brief, roster_path=roster)


# --- restart -------------------------------------------------------------------

def test_restart_closes_the_old_run_slings_anew_and_rotates_the_rooms(setup):
    fac, runner, bridge, brief, roster, _ = setup
    fac.new("bakery", brief_path=brief, roster_path=roster)
    runner.commands.clear()

    result = fac.restart("bakery")

    gc = commands_named(runner, "gc")
    assert gc[0][3:] == ["convoy", "delete", "ba-1", "--force"]
    assert gc[1][3:] == ["sling", "bakery/factory.discoverer", "website-factory", "--formula", "--json"]
    assert bridge.calls[-1] == ("POST", "/projects/bakery/restart", {"workflow_id": "ba-40"})
    assert result["workflow_id"] == "ba-40"


def test_restart_of_an_unknown_project_is_refused(setup):
    fac, *_ = setup
    with pytest.raises(factory.IntakeError, match="no project"):
        fac.restart("ghost")


# --- CLI -----------------------------------------------------------------------

def test_main_parses_new_and_prints_the_result(setup, monkeypatch, capsys):
    fac, *_ , brief, roster, _ = setup
    monkeypatch.setattr(factory, "factory_from_env", lambda: fac)

    code = factory.main(["new", "bakery", "--brief", brief, "--roster", roster])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["workflow_id"] == "ba-1"


def test_main_reports_intake_errors_without_a_traceback(setup, monkeypatch, capsys):
    fac, *_ , brief, roster, _ = setup
    monkeypatch.setattr(factory, "factory_from_env", lambda: fac)
    assert factory.main(["restart", "ghost"]) == 1
    assert "no project" in capsys.readouterr().err
