"""Publish a finished factory project to the owner's GitHub account.

The bridge is the only process that holds the credential. It creates the
repository with the token and pushes the project's commits. The token is sent
as a one-shot HTTP header for that push, so it is never written into the
project's git config and never appears on the git command line — both of
which the agents can read.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable

API = "https://api.github.com"


class DeliveryError(Exception):
    """The repository could not be published. The message is safe to relay."""


Api = Callable[[str, str, dict | None], tuple[int, dict]]
Run = Callable[[list[str], dict], subprocess.CompletedProcess]


class GitHubDelivery:
    """Create a repository under the token's account and push a project to it."""

    def __init__(self, token: str, projects_dir: str, api: Api | None = None, run: Run | None = None) -> None:
        self.token = token
        self.projects_dir = projects_dir
        self._api = api or self._request
        self._run = run or _run_git
        self._login: str | None = None

    def publish(self, name: str, visibility: str) -> str:
        """Create or reuse the repository and push HEAD to its main branch.

        Returns the repository's HTML URL.
        """
        if visibility not in ("private", "public"):
            raise DeliveryError(f"visibility must be private or public, got {visibility!r}")
        project_dir = os.path.join(self.projects_dir, name)
        if not os.path.isdir(os.path.join(project_dir, ".git")):
            raise DeliveryError(f"no git repository at {project_dir}")
        repo = self._create(name, private=visibility == "private")
        url = repo["html_url"]
        self._push(project_dir, repo["full_name"])
        return url

    def _create(self, name: str, private: bool) -> dict:
        status, body = self._api("POST", "/user/repos", {"name": name, "private": private, "auto_init": False})
        if status == 201:
            return body
        if status == 422 and _already_exists(body):
            owner = self.login()
            status, body = self._api("GET", f"/repos/{owner}/{name}", None)
            if status == 200:
                return body
            raise DeliveryError(f"repository {name} already exists and this token cannot see it")
        raise DeliveryError(f"GitHub refused to create {name}: {status} {_message(body)}")

    def login(self) -> str:
        if self._login is None:
            status, body = self._api("GET", "/user", None)
            if status != 200 or not body.get("login"):
                raise DeliveryError(f"GitHub rejected the token: {status} {_message(body)}")
            self._login = body["login"]
        return self._login

    def _push(self, project_dir: str, full_name: str) -> None:
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        env = dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
        argv = ["git", "-C", project_dir, "push", f"https://github.com/{full_name}.git", "HEAD:main"]
        result = self._run(argv, env)
        if result.returncode != 0:
            detail = _redact(result.stderr or result.stdout or "no output", self.token)
            raise DeliveryError(f"git push failed: {detail}")

    def _request(self, method: str, path: str, body: dict | None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            API + path, data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "gascity-factory",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                parsed: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"message": raw[:300]}
            return exc.code, parsed if isinstance(parsed, dict) else {"message": str(parsed)}


def _run_git(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)


def _already_exists(body: dict) -> bool:
    text = json.dumps(body).lower()
    return "already exists" in text


def _message(body: dict) -> str:
    return str(body.get("message") or body)[:300]


def _redact(text: str, token: str) -> str:
    cleaned = text.replace(token, "[token]") if token else text
    return " ".join(cleaned.split())[:500]
