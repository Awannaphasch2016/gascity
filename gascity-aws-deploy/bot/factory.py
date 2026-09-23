#!/usr/bin/env python3
"""Start a factory project, or start one over.

    factory new <name> --brief brief.md --roster roster.json
    factory restart <name>
    factory list

`new` is the whole intake: a git repository for the project under the
projects directory, the factory pack installed on it as a rig, the
website-factory formula slung at the rig's discoverer, and the bridge told who
is on the roster so it can open a room for each of them. From then on the
orchestrator drives the pipeline and the bridge carries the conversation.

`restart` closes the running workflow, slings the formula again, and has the
bridge throw the project's rooms away and make fresh ones. The repository and
its docs/ are kept: the agents re-read them, so a restart resumes from what
was already settled rather than from nothing. This runs on the city host as
the operator; that is the admin check.

Configuration comes from the environment the deploy script writes:
GC_CITY_DIR, FACTORY_PACK_DIR, FACTORY_PROJECTS_DIR, BRIDGE_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Callable

import requests

# A project name is a rig name, a directory, a conversation id, and a topic
# title. Lowercase slugs satisfy all four without escaping anywhere.
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

FORMULA = "website-factory"
PACK = "factory"
FIRST_AGENT = f"{PACK}.discoverer"

GITIGNORE = "node_modules/\ndist/\n.env\n"

Runner = Callable[..., str]


class IntakeError(Exception):
    """Something the operator has to fix before the project can start."""


def run_command(argv: list[str], cwd: str | None = None) -> str:
    """Run a command and return its stdout; a failure carries stderr along."""
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, argv, completed.stdout, completed.stderr)
    return completed.stdout


class BridgeClient:
    """The bridge's /projects surface."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = requests.request(method, self.base_url + path, json=body, timeout=60)
        if resp.status_code >= 400:
            detail = (resp.json() or {}).get("error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise IntakeError(f"bridge {method} {path}: {resp.status_code} {detail}")
        return resp.json()


class Factory:
    """The intake steps, over a command runner and a bridge client."""

    def __init__(self, city_dir: str, pack_dir: str, projects_dir: str,
                 run: Runner = run_command, bridge: Any = None) -> None:
        self.city_dir = city_dir
        self.pack_dir = pack_dir
        self.projects_dir = projects_dir
        self.run = run
        self.bridge = bridge

    # --- gc ---

    def _gc(self, *argv: str) -> str:
        command = ["gc", "--city", self.city_dir, *argv]
        try:
            return self.run(command)
        except subprocess.CalledProcessError as exc:
            raise IntakeError(f"{' '.join(command)} failed: {(exc.stderr or exc.output or '').strip()}") from exc

    def _sling(self, name: str) -> str:
        out = self._gc("sling", f"{name}/{FIRST_AGENT}", FORMULA, "--formula", "--json")
        workflow_id = ""
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            record = json.loads(line)
            workflow_id = record.get("workflow_id") or record.get("molecule_id") or workflow_id
        if not workflow_id:
            raise IntakeError(f"gc sling returned no workflow id: {out.strip()!r}")
        return workflow_id

    def _close_seats(self, rig: str) -> None:
        """Close the rig's live factory seats so the new run starts on fresh ones.

        Closing the workflow's beads does not end the sessions working them; a
        seat left running holds the pool slot the new run's first step needs
        and goes on working a step that no longer exists. The rig's control
        dispatcher is infrastructure and is not a seat.
        """
        listed = json.loads(self._gc("session", "list", "--json", "--state", "active") or "{}")
        for session in listed.get("sessions", []):
            if session.get("rig") != rig:
                continue
            if not str(session.get("template", "")).startswith(f"{rig}/{PACK}."):
                continue
            self._gc("session", "close", session["id"])

    # --- new ---

    def new(self, name: str, brief_path: str, roster_path: str) -> dict[str, Any]:
        """Create the project, register the rig, start the pipeline, open the rooms."""
        if not NAME.match(name or ""):
            raise IntakeError(f"project name {name!r} must be a lowercase slug: letters, digits, dashes, up to 40 chars")
        project_dir = os.path.join(self.projects_dir, name)
        if os.path.exists(project_dir):
            raise IntakeError(f"{project_dir} already exists; use `factory restart {name}` to start it over")
        roster = self._load_roster(roster_path)
        with open(brief_path, encoding="utf-8") as fh:
            brief = fh.read()
        if not brief.strip():
            raise IntakeError(f"{brief_path} is empty")

        self._lay_out(project_dir, brief, roster)
        self._gc("rig", "add", project_dir, "--name", name, "--include", self.pack_dir, "--json")
        workflow_id = self._sling(name)
        self._tell_bridge("POST", "/projects", {"name": name, "rig": name, "workflow_id": workflow_id, "roster": roster},
                          context=f"project {name} is running as workflow {workflow_id} but the bridge was not told")
        return {"name": name, "rig": name, "workflow_id": workflow_id, "project_dir": project_dir}

    @staticmethod
    def _load_roster(path: str) -> dict[str, Any]:
        with open(path, encoding="utf-8") as fh:
            roster = json.load(fh)
        members = roster.get("members") if isinstance(roster, dict) else None
        if not isinstance(members, dict) or not members:
            raise IntakeError(f"{path}: roster needs a non-empty \"members\" object")
        return {"members": members, "admins": list(roster.get("admins") or [])}

    def _lay_out(self, project_dir: str, brief: str, roster: dict[str, Any]) -> None:
        os.makedirs(os.path.join(project_dir, "docs"))
        with open(os.path.join(project_dir, "docs", "brief.md"), "w", encoding="utf-8") as fh:
            fh.write(brief)
        with open(os.path.join(project_dir, "roster.json"), "w", encoding="utf-8") as fh:
            json.dump(roster, fh, indent=2)
            fh.write("\n")
        with open(os.path.join(project_dir, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(GITIGNORE)
        try:
            self.run(["git", "init", "-b", "main", "-q"], cwd=project_dir)
            self.run(["git", "add", "-A"], cwd=project_dir)
            self.run(["git", "-c", "user.name=factory", "-c", "user.email=factory@localhost",
                      "commit", "-q", "-m", "Project brief and roster"], cwd=project_dir)
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(project_dir, ignore_errors=True)
            raise IntakeError(f"git failed in {project_dir}: {(exc.stderr or '').strip()}") from exc

    def _tell_bridge(self, method: str, path: str, body: dict[str, Any], context: str) -> dict[str, Any]:
        try:
            return self.bridge.request(method, path, body)
        except IntakeError:
            raise
        except Exception as exc:
            raise IntakeError(f"{context}: {exc}. Retry with: curl -X {method} $BRIDGE_URL{path} -d '{json.dumps(body)}'") from exc

    # --- restart / list ---

    def _projects(self) -> dict[str, dict[str, Any]]:
        try:
            listed = self.bridge.request("GET", "/projects")
        except IntakeError:
            raise
        except Exception as exc:
            raise IntakeError(f"cannot reach the bridge: {exc}") from exc
        return {p["name"]: p for p in listed.get("projects", [])}

    def list(self) -> list[dict[str, Any]]:
        """Return the bridge's view of every project."""
        return list(self._projects().values())

    def restart(self, name: str) -> dict[str, Any]:
        """Close the running workflow, start a fresh one, rotate the rooms."""
        project = self._projects().get(name)
        if project is None:
            raise IntakeError(f"no project named {name!r} on the bridge")
        self._gc("convoy", "delete", project["workflow_id"], "--force")
        self._close_seats(project["rig"])
        workflow_id = self._sling(project["rig"])
        return self._tell_bridge("POST", f"/projects/{name}/restart", {"workflow_id": workflow_id},
                                 context=f"project {name} restarted as workflow {workflow_id} but the bridge was not told")


def factory_from_env() -> Factory:
    """Build the intake over the deploy script's environment."""
    return Factory(
        city_dir=os.getenv("GC_CITY_DIR", "/opt/gascity/city"),
        pack_dir=os.getenv("FACTORY_PACK_DIR", "/opt/gascity/packs/factory"),
        projects_dir=os.getenv("FACTORY_PROJECTS_DIR", "/opt/gascity/projects"),
        bridge=BridgeClient(os.getenv("BRIDGE_URL", "http://127.0.0.1:8081")),
    )


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(prog="factory", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="start a project")
    new.add_argument("name")
    new.add_argument("--brief", required=True, help="markdown brief; becomes docs/brief.md")
    new.add_argument("--roster", required=True, help="roster.json: members and their responsibilities")
    restart = sub.add_parser("restart", help="close the run, start over, fresh rooms")
    restart.add_argument("name")
    sub.add_parser("list", help="projects the bridge knows")
    args = parser.parse_args(argv)

    fac = factory_from_env()
    try:
        if args.command == "new":
            result: Any = fac.new(args.name, brief_path=args.brief, roster_path=args.roster)
        elif args.command == "restart":
            result = fac.restart(args.name)
        else:
            result = fac.list()
    except IntakeError as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
