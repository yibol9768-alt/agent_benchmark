"""GitHub Search API wrapper for fresh-repo harvesting.

Uses the unauthenticated REST API (5k req/hr with GITHUB_TOKEN, 60/hr without).
We query per language with filters from configs/harvester.yaml, rank by stars,
resolve each result's HEAD commit (the repo's current default-branch SHA),
and return RepoManifest objects with status=CANDIDATE.

Baseline gating + env-build + test detection happen downstream in the
envbuild stage — the harvester only confirms the repo is *a priori* eligible.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Iterable

import requests
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

from ..schema import Language, RepoManifest, RepoStatus

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bug-exam-bench/0.1",
        })
        tok = token or os.environ.get("GITHUB_TOKEN")
        if tok:
            self.session.headers["Authorization"] = f"Bearer {tok}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20))
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            wait_s = max(0, reset - int(time.time())) + 2
            log.warning("rate limited, sleeping %ds", wait_s)
            time.sleep(min(wait_s, 60))
            r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def search_repos(self, query: str, per_page: int = 100, max_pages: int = 3) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            data = self._get("/search/repositories", {
                "q": query, "sort": "stars", "order": "desc",
                "per_page": per_page, "page": page,
            })
            items = data.get("items", [])
            out.extend(items)
            if len(items) < per_page:
                break
        return out

    def head_commit(self, owner: str, name: str, branch: str) -> str:
        data = self._get(f"/repos/{owner}/{name}/branches/{branch}")
        return data["commit"]["sha"]


def _item_to_manifest(item: dict, lang: Language, cutoff: datetime, client: GitHubClient) -> RepoManifest | None:
    owner = item["owner"]["login"]
    name = item["name"]
    try:
        head_sha = client.head_commit(owner, name, item.get("default_branch", "main"))
    except Exception as e:
        log.warning("could not resolve HEAD for %s/%s: %r", owner, name, e)
        return None
    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    pushed = datetime.fromisoformat(item["pushed_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    lic = (item.get("license") or {}).get("spdx_id")
    return RepoManifest(
        id=f"{owner}__{name}",
        url=item["html_url"],
        owner=owner,
        name=name,
        language=lang,
        stars=item.get("stargazers_count", 0),
        size_kb=item.get("size", 0),
        license=lic,
        created_at=created,
        pushed_at=pushed,
        base_commit=head_sha,
        default_branch=item.get("default_branch", "main"),
        status=RepoStatus.CANDIDATE,
        post_cutoff=created >= cutoff,
    )


def _passes_prefilters(item: dict, filters: dict) -> bool:
    if item.get("stargazers_count", 0) < filters["min_stars"]:
        return False
    if item.get("size", 0) > filters["max_size_kb"]:
        return False
    lic = (item.get("license") or {}).get("spdx_id")
    if lic not in filters["licenses"]:
        return False
    if item.get("fork") or item.get("archived") or item.get("disabled"):
        return False
    return True


def harvest(
    harvester_cfg_path: str,
    language: str | None = None,
    max_candidates: int | None = None,
    token: str | None = None,
) -> Iterable[RepoManifest]:
    """Yield RepoManifest objects for a single language (or all if None)."""
    with open(harvester_cfg_path) as f:
        cfg = yaml.safe_load(f)
    filters = cfg["filters"]
    cutoff = datetime.fromisoformat(filters["created_after"])
    client = GitHubClient(token=token)

    langs = [language] if language else list(cfg["github_search_queries"].keys())
    total = 0
    seen: set[str] = set()
    for lang in langs:
        lang_enum = Language(lang)
        queries = cfg["github_search_queries"].get(lang, [])
        for q in queries:
            log.info("harvest query [%s]: %s", lang, q)
            try:
                items = client.search_repos(q)
            except Exception as e:
                log.warning("search failed: %r", e)
                continue
            for item in items:
                if item["full_name"] in seen:
                    continue
                seen.add(item["full_name"])
                if not _passes_prefilters(item, filters):
                    continue
                manifest = _item_to_manifest(item, lang_enum, cutoff, client)
                if manifest is None:
                    continue
                yield manifest
                total += 1
                if max_candidates and total >= max_candidates:
                    return
