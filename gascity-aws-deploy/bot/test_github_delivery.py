"""Publishing a finished project to the owner's GitHub account.

The bridge holds the credential. It creates the repository and pushes the
project's commits there. The token must never land in the project's git
config or on the git command line, because the agents can read both.

    Run: python3 -m pytest bot/test_github_delivery.py
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import github_delivery as gd  # noqa: E402


class FakeAPI:
    def __init__(self, exists: bool = False) -> None:
        self.exists = exists
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, body: dict | None) -> tuple[int, dict]:
        self.calls.append((method, path, body))
        if method == "GET" and path == "/user":
            return 200, {"login": "you"}
        if method == "POST" and path == "/user/repos":
            if self.exists:
                return 422, {"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}
            return 201, {"full_name": "you/bakery", "html_url": "https://github.com/you/bakery"}
        if method == "GET" and path == "/repos/you/bakery":
            return 200, {"full_name": "you/bakery", "html_url": "https://github.com/you/bakery"}
        return 500, {"message": f"unexpected {method} {path}"}


class FakeRun:
    def __init__(self, code: int = 0, stderr: str = "") -> None:
        self.code = code
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], env: dict) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), dict(env)))
        return subprocess.CompletedProcess(argv, self.code, "", self.stderr)


def repo(tmp_path):
    project = tmp_path / "bakery"
    project.mkdir()
    (project / ".git").mkdir()
    return project


def test_publish_creates_a_private_repo_and_pushes_without_exposing_the_token(tmp_path):
    api, run = FakeAPI(), FakeRun()
    delivery = gd.GitHubDelivery(token="ghp_secret", projects_dir=str(tmp_path), api=api, run=run)
    repo(tmp_path)

    url = delivery.publish("bakery", "private")

    assert url == "https://github.com/you/bakery"
    created = api.calls[0]
    assert created[0:2] == ("POST", "/user/repos")
    assert created[2]["name"] == "bakery" and created[2]["private"] is True and created[2]["auto_init"] is False
    argv, env = run.calls[0]
    assert argv == ["git", "-C", str(tmp_path / "bakery"), "push", "https://github.com/you/bakery.git", "HEAD:main"]
    assert "ghp_secret" not in " ".join(argv)
    header = env["GIT_CONFIG_VALUE_0"]
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert header.lower().startswith("authorization: basic ")
    assert "ghp_secret" not in header


def test_an_existing_repository_is_reused(tmp_path):
    api, run = FakeAPI(exists=True), FakeRun()
    delivery = gd.GitHubDelivery(token="ghp_secret", projects_dir=str(tmp_path), api=api, run=run)
    repo(tmp_path)

    assert delivery.publish("bakery", "public") == "https://github.com/you/bakery"
    assert ("GET", "/repos/you/bakery", None) in api.calls
    assert api.calls[0][2]["private"] is False


def test_a_visibility_other_than_private_or_public_is_refused(tmp_path):
    delivery = gd.GitHubDelivery(token="ghp_secret", projects_dir=str(tmp_path), api=FakeAPI(), run=FakeRun())
    with pytest.raises(gd.DeliveryError, match="visibility"):
        delivery.publish("bakery", "internal")


def test_a_project_without_a_git_directory_is_refused(tmp_path):
    (tmp_path / "bakery").mkdir()
    delivery = gd.GitHubDelivery(token="ghp_secret", projects_dir=str(tmp_path), api=FakeAPI(), run=FakeRun())
    with pytest.raises(gd.DeliveryError, match="no git repository"):
        delivery.publish("bakery", "private")


def test_a_failed_push_is_reported_without_the_token(tmp_path):
    run = FakeRun(code=1, stderr="remote rejected HEAD: protected branch ghp_secret")
    delivery = gd.GitHubDelivery(token="ghp_secret", projects_dir=str(tmp_path), api=FakeAPI(), run=run)
    repo(tmp_path)
    with pytest.raises(gd.DeliveryError, match="git push failed") as raised:
        delivery.publish("bakery", "private")
    assert "ghp_secret" not in str(raised.value)
