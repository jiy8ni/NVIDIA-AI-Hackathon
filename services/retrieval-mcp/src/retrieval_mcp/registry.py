"""A small navigation map for query expansion, not a source-of-truth store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import SearchRequest


@dataclass(frozen=True)
class SearchHints:
    """Search routing hints, never source content or inferred facts."""

    terms: list[str]
    slack_channels: list[str]
    notion_roots: list[str]
    drive_folders: list[str]


class SourceRegistry:
    def __init__(self, path: Path):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.domains: dict[str, dict] = payload.get("domains", {})

    def _matching_domains(self, request: SearchRequest) -> list[dict]:
        query = request.query.strip()
        corpus = " ".join([query, *request.entities]).lower()
        matches: list[dict] = []

        for domain in self.domains.values():
            aliases = [str(item).strip() for item in domain.get("aliases", [])]
            if any(alias.lower() in corpus for alias in aliases):
                matches.append(domain)
        return matches

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in items if item))

    def hints(self, request: SearchRequest) -> SearchHints:
        query = request.query.strip()
        matches = self._matching_domains(request)
        terms = [
            query,
            *[entity.strip() for entity in request.entities if entity.strip()],
            *(item.strip() for item in (request.role, request.project) if item and item.strip()),
        ]
        for domain in matches:
            terms.extend(str(item).strip() for item in domain.get("aliases", []))

        return SearchHints(
            terms=self._unique(terms)[:8],
            slack_channels=self._unique([str(item) for domain in matches for item in domain.get("slack_channels", [])]),
            notion_roots=self._unique([str(item) for domain in matches for item in domain.get("notion_roots", [])]),
            drive_folders=self._unique([str(item) for domain in matches for item in domain.get("drive_folders", [])]),
        )
