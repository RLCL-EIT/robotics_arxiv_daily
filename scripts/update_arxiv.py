#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

import arxiv
import yaml


ROOT = Path(__file__).resolve().parents[1]


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
            for key in ("corresponding_author", "first_affiliation"):
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
    columns = ["Date", "Title", "Authors", "PDF"]
    if show_corr:
        columns.append("Corresponding")
    if show_aff:
        columns.append("First Affiliation")
    columns.extend(["Category", "Abstract"])

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
            markdown_escape(paper.get("authors_display", "")),
            f"[{paper['id']}]({paper['arxiv_url']}) / [pdf]({paper['pdf_url']})",
        ]
        if show_corr:
            row.append(markdown_escape(paper.get("corresponding_author", "")))
        if show_aff:
            row.append(markdown_escape(paper.get("first_affiliation", "")))
        row.extend(
            [
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
            "Those columns are intentionally marked `TBD` unless manually curated or enriched by a later parser.",
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
