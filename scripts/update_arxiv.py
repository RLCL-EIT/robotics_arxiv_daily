#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import arxiv
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
GITHUB_SEARCH_REPOS = "https://api.github.com/search/repositories"
HTTP_HEADERS = {"User-Agent": "robotics-arxiv-daily/0.1"}
AGGREGATOR_REPO_PATTERNS = (
    "arxiv",
    "daily",
    "awesome",
    "hub",
    "paper",
    "papers",
    "survey",
    "reading",
    "literature",
)


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {"papers": {}}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def normalize_arxiv_id(short_id: str) -> str:
    return re.sub(r"v\d+$", "", short_id)


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def authors_to_text(authors: list[arxiv.Result.Author], max_authors: int = 4) -> str:
    names = [str(author) for author in authors]
    if len(names) <= max_authors:
        return ", ".join(names)
    return ", ".join(names[:max_authors]) + " et al."


def http_get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 5) -> Any | None:
    headers = dict(HTTP_HEADERS)
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()
    except requests.RequestException:
        return None


def normalized_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 3}


def title_signature(title: str) -> set[str]:
    tokens = normalized_tokens(title)
    signatures = set(tokens)
    for match in re.finditer(r"\b[A-Z][A-Z0-9-]{2,}\b", title):
        signatures.add(match.group(0).lower().replace("-", ""))
    return signatures


def repo_name_matches_title(full_name: str, title: str) -> bool:
    repo_name = full_name.split("/")[-1]
    repo_tokens = normalized_tokens(repo_name)
    signatures = title_signature(title)
    if repo_tokens & signatures:
        return True
    compact_repo = re.sub(r"[^a-z0-9]", "", repo_name.lower())
    return bool(compact_repo and compact_repo in signatures)


def is_probable_code_repo(full_name: str, description: str = "", title: str = "") -> bool:
    full_name_lower = full_name.lower()
    description_lower = description.lower()
    if any(pattern in full_name_lower for pattern in AGGREGATOR_REPO_PATTERNS):
        return False
    if "daily dose" in description_lower or "paper list" in description_lower:
        return False
    if title and not repo_name_matches_title(full_name, title):
        return False
    return True


def is_valid_code_url(url: str, title: str = "") -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return False
    return is_probable_code_repo("/".join(parts[:2]), title=title)


def clean_code_links(data: dict[str, Any]) -> None:
    for paper in data.get("papers", {}).values():
        if not is_valid_code_url(paper.get("code_url", ""), paper.get("title", "")):
            paper["code_url"] = ""
            paper.pop("code_url_source", None)


def find_github_code_link(title: str, arxiv_id: str) -> str:
    title_query = normalize_text(title)
    title_head = " ".join(title_query.split()[:8])
    queries = [
        f"\"{title_query}\" in:readme,description",
        f"\"{title_head}\" \"{arxiv_id}\" in:readme,description",
        f"\"{arxiv_id}\" in:readme,description",
    ]
    for query in queries:
        data = http_get_json(
            GITHUB_SEARCH_REPOS,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": 5},
        )
        if not isinstance(data, dict):
            continue
        for item in data.get("items", []) or []:
            full_name = item.get("full_name", "")
            description = item.get("description") or ""
            url = item.get("html_url", "")
            if is_probable_code_repo(full_name, description, title) and is_valid_code_url(url, title):
                return url
    return ""


def enrich_code_links(data: dict[str, Any], max_papers: int, max_github_queries: int) -> None:
    github_queries = 0
    checked_papers = 0
    papers = sorted(
        data.get("papers", {}).values(),
        key=lambda paper: (paper.get("updated", ""), paper.get("id", "")),
        reverse=True,
    )
    for paper in papers:
        if paper.get("code_url"):
            continue
        if checked_papers >= max_papers:
            break
        checked_papers += 1

        arxiv_id = paper["id"]
        paper["code_url"] = ""
        if paper["code_url"]:
            continue

        if github_queries >= max_github_queries:
            continue
        paper["code_url"] = find_github_code_link(paper["title"], arxiv_id)
        if paper["code_url"]:
            paper["code_url_source"] = "github_search"
        github_queries += 3


def paper_to_record(result: arxiv.Result, topic: str, unknown: str) -> dict[str, Any]:
    arxiv_id = normalize_arxiv_id(result.get_short_id())
    published = result.published.date().isoformat() if result.published else ""
    updated = result.updated.date().isoformat() if result.updated else published
    authors = [str(author) for author in result.authors]

    return {
        "id": arxiv_id,
        "topic_hits": [topic],
        "title": normalize_text(result.title),
        "authors": authors,
        "authors_display": authors_to_text(result.authors),
        "first_author": authors[0] if authors else "",
        "corresponding_author": unknown,
        "first_affiliation": unknown,
        "code_url": "",
        "summary": normalize_text(result.summary),
        "primary_category": result.primary_category,
        "categories": list(result.categories or []),
        "published": published,
        "updated": updated,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "comment": normalize_text(result.comment),
    }


def fetch_topic(topic: dict[str, Any], max_results: int, unknown: str) -> list[dict[str, Any]]:
    client = arxiv.Client(page_size=min(max_results, 100), delay_seconds=3, num_retries=3)
    search = arxiv.Search(
        query=topic["query"],
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )
    records = []
    for result in client.results(search):
        records.append(paper_to_record(result, topic["name"], unknown))
    return records


def merge_records(existing: dict[str, Any], new_records: list[dict[str, Any]]) -> dict[str, Any]:
    papers = existing.setdefault("papers", {})
    for record in new_records:
        paper_id = record["id"]
        if paper_id in papers:
            old_topics = set(papers[paper_id].get("topic_hits", []))
            old_topics.update(record.get("topic_hits", []))
            record["topic_hits"] = sorted(old_topics)
            # Keep manually curated metadata if it was filled in later.
            for key in ("corresponding_author", "first_affiliation", "code_url"):
                old_value = papers[paper_id].get(key)
                if old_value and old_value != "TBD":
                    record[key] = old_value
        papers[paper_id] = record
    return existing


def prune_old(data: dict[str, Any], keep_days: int) -> dict[str, Any]:
    if keep_days <= 0:
        return data
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    papers = data.get("papers", {})
    data["papers"] = {
        paper_id: paper
        for paper_id, paper in papers.items()
        if dt.date.fromisoformat(paper.get("updated") or paper.get("published")) >= cutoff
    }
    return data


def markdown_escape(value: str) -> str:
    return html.escape(value or "", quote=False).replace("|", "\\|")


def compact_text(value: str, limit: int) -> str:
    value = normalize_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def topic_table(
    topic: str,
    papers: list[dict[str, Any]],
    show_corr: bool,
    show_aff: bool,
    abstract_chars: int,
) -> str:
    columns = ["Date", "Title"]
    if show_corr:
        columns.append("Corresponding")
    if show_aff:
        columns.append("First Affiliation")
    columns.extend(["Paper", "Code", "Category", "Abstract"])

    lines = [
        f"## {topic}",
        "",
        "|" + "|".join(columns) + "|",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]

    for paper in papers:
        row = [
            f"**{paper.get('updated') or paper.get('published', '')}**",
            f"**{markdown_escape(paper['title'])}**",
        ]
        if show_corr:
            row.append(markdown_escape(paper.get("corresponding_author", "")))
        if show_aff:
            row.append(markdown_escape(paper.get("first_affiliation", "")))
        code_url = paper.get("code_url") or ""
        row.extend(
            [
                f"[abs]({paper['arxiv_url']}) / [pdf]({paper['pdf_url']})",
                f"[repo]({code_url})" if code_url else "",
                markdown_escape(paper.get("primary_category", "")),
                markdown_escape(compact_text(paper.get("summary", ""), abstract_chars)),
            ]
        )
        lines.append("|" + "|".join(row) + "|")

    lines.append("")
    return "\n".join(lines)


def render_markdown(config: dict[str, Any], data: dict[str, Any], docs: bool = False) -> str:
    title = config["site"]["title"]
    description = config["site"]["description"]
    updated = dt.date.today().isoformat()
    show_corr = bool(config["metadata"].get("show_corresponding_author", True))
    show_aff = bool(config["metadata"].get("show_first_affiliation", True))
    abstract_chars = int(config["site"].get("abstract_chars", 320))
    topics = [topic["name"] for topic in config["topics"]]
    papers = list(data.get("papers", {}).values())
    papers.sort(key=lambda paper: (paper.get("updated", ""), paper.get("id", "")), reverse=True)

    lines = []
    if docs:
        lines.extend(["---", "layout: default", f"title: {title}", "---", ""])
    lines.extend(
        [
            f"# {title}",
            "",
            description,
            "",
            f"Updated on **{updated}**.",
            "",
            "## Topics",
            "",
        ]
    )
    for topic in topics:
        count = sum(1 for paper in papers if topic in paper.get("topic_hits", []))
        lines.append(f"- [{topic}](#{topic.lower().replace(' ', '-').replace('/', '')}) ({count})")
    lines.append("")
    lines.extend(
        [
            "## Metadata Note",
            "",
            "arXiv metadata does not reliably provide corresponding authors or affiliations. "
            "Those columns are intentionally marked `TBD` unless manually curated or enriched by a later parser. "
            "The code column is a conservative best-effort GitHub lookup and may be blank when no likely official repository is found.",
            "",
        ]
    )

    for topic in topics:
        topic_papers = [paper for paper in papers if topic in paper.get("topic_hits", [])]
        if topic_papers:
            lines.append(topic_table(topic, topic_papers, show_corr, show_aff, abstract_chars))

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config_path = ROOT / args.config
    config = load_config(config_path)
    data_path = ROOT / config["output"]["data"]
    data = read_json(data_path)

    max_results = int(config["site"].get("max_results_per_topic", 50))
    unknown = config["metadata"].get("unknown_value", "TBD")
    fetched: list[dict[str, Any]] = []

    for topic in config["topics"]:
        print(f"Fetching {topic['name']}...")
        fetched.extend(fetch_topic(topic, max_results, unknown))

    data = merge_records(data, fetched)
    clean_code_links(data)
    if config.get("code_search", {}).get("enabled", True):
        enrich_code_links(
            data,
            int(config.get("code_search", {}).get("max_papers_per_run", 30)),
            int(config.get("code_search", {}).get("github_max_queries_per_run", 20)),
        )
    data["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    data = prune_old(data, int(config["site"].get("keep_days", 90)))
    write_json(data_path, data)

    readme_path = ROOT / config["output"]["readme"]
    readme_path.write_text(render_markdown(config, data), encoding="utf-8")

    docs_path = ROOT / config["output"]["docs_index"]
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_markdown(config, data, docs=True), encoding="utf-8")


if __name__ == "__main__":
    main()
