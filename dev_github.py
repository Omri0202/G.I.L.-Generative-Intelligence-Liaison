"""
dev_github.py — G.I.L. developer tools
GitHub REST API — PRs, issues, CI status.
Set GITHUB_TOKEN in .env for authenticated requests (5000 req/hr vs 60 unauthenticated).
"""

import os
import re
import subprocess
import requests
from logger import get as _get_log

log = _get_log("dev.github")
_BASE = "https://api.github.com"


def _headers() -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "GIL-Assistant/1.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(endpoint: str, params: dict | None = None):
    try:
        r = requests.get(f"{_BASE}{endpoint}", headers=_headers(),
                         params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 401:
            return {"_error": "GitHub token invalid or missing. Add GITHUB_TOKEN to your .env"}
        if exc.response.status_code == 403:
            return {"_error": "GitHub rate limit hit. Add GITHUB_TOKEN for more requests."}
        log.error("GitHub HTTP error: %s", exc)
        return None
    except Exception as exc:
        log.error("GitHub API error: %s", exc)
        return None


def _current_repo() -> tuple[str, str] | None:
    """Detect the GitHub owner/repo from the current git remote."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = r.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/.\s]+?)(?:\.git)?$", url)
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None


def my_prs() -> str:
    data = _get("/search/issues", {"q": "is:pr is:open author:@me", "sort": "updated", "per_page": 5})
    if isinstance(data, dict) and "_error" in data:
        return data["_error"]
    if not data or not data.get("items"):
        return "No open pull requests found."
    prs = data["items"][:5]
    lines = [f"#{p['number']}: {p['title']} ({p['repository_url'].split('/')[-1]})" for p in prs]
    return f"{len(prs)} open PRs: " + " | ".join(lines)


def repo_prs(owner: str = None, repo: str = None, state: str = "open") -> str:
    if not owner or not repo:
        detected = _current_repo()
        if detected:
            owner, repo = detected
        else:
            return "Could not detect GitHub repo. Specify owner/repo."
    data = _get(f"/repos/{owner}/{repo}/pulls", {"state": state, "per_page": 5})
    if data is None:
        return f"Could not fetch PRs for {owner}/{repo}."
    if not data:
        return f"No {state} PRs in {owner}/{repo}."
    lines = [f"#{p['number']}: {p['title']} by {p['user']['login']}" for p in data[:5]]
    return f"{len(data)} {state} PRs: " + " | ".join(lines)


def repo_issues(owner: str = None, repo: str = None, state: str = "open") -> str:
    if not owner or not repo:
        detected = _current_repo()
        if detected:
            owner, repo = detected
        else:
            return "Could not detect GitHub repo."
    data = _get(f"/repos/{owner}/{repo}/issues", {"state": state, "per_page": 5})
    if data is None:
        return f"Could not fetch issues for {owner}/{repo}."
    issues = [i for i in data if "pull_request" not in i][:5]
    if not issues:
        return f"No {state} issues in {owner}/{repo}."
    lines = [f"#{i['number']}: {i['title']}" for i in issues]
    return " | ".join(lines)


def ci_status(owner: str = None, repo: str = None, branch: str = "main") -> str:
    if not owner or not repo:
        detected = _current_repo()
        if detected:
            owner, repo = detected
        else:
            return "Could not detect GitHub repo."
    data = _get(f"/repos/{owner}/{repo}/commits/{branch}/check-runs")
    if data is None:
        return "Could not fetch CI status."
    runs = data.get("check_runs", [])
    if not runs:
        return f"No CI runs found for {owner}/{repo} on {branch}."
    failed  = [r for r in runs if r["conclusion"] == "failure"]
    passing = [r for r in runs if r["conclusion"] == "success"]
    pending = [r for r in runs if r["status"] == "in_progress"]
    if failed:
        names = ", ".join(r["name"] for r in failed[:3])
        return f"CI failing: {names}"
    if pending:
        return f"CI running — {len(pending)} check(s) in progress."
    return f"CI passing — all {len(passing)} checks green."


def repo_info(owner: str = None, repo: str = None) -> str:
    if not owner or not repo:
        detected = _current_repo()
        if detected:
            owner, repo = detected
        else:
            return "Could not detect GitHub repo."
    data = _get(f"/repos/{owner}/{repo}")
    if not data:
        return f"Could not fetch info for {owner}/{repo}."
    return (f"{owner}/{repo} — {data.get('description','no description')}. "
            f"Stars: {data.get('stargazers_count',0)}, "
            f"Open issues: {data.get('open_issues_count',0)}, "
            f"Language: {data.get('language','unknown')}.")
