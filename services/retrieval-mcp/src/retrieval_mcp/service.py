"""Source fan-out, ranking, status handling, and opaque pagination."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .adapters import AdapterFailure, DriveAdapter, NotionAdapter, SlackAdapter
from .models import Coverage, Evidence, RetrievalError, SearchRequest, SearchResponse, SearchStatus, SourceName, SourceResult
from .registry import SourceRegistry


class RetrievalService:
    def __init__(self, adapters: dict[SourceName, Any] | None = None, registry: SourceRegistry | None = None) -> None:
        registry_path = Path(os.getenv("SOURCE_REGISTRY_PATH", Path(__file__).resolve().parents[2] / "source_registry.yaml"))
        self.registry = registry or SourceRegistry(registry_path)
        self.adapters = adapters or {
            SourceName.SLACK: SlackAdapter(),
            SourceName.NOTION: NotionAdapter(),
            SourceName.DRIVE: DriveAdapter(),
        }

    @staticmethod
    def _decode_cursor(cursor: str | None) -> dict[str, str]:
        if not cursor:
            return {}
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = base64.urlsafe_b64decode(padded.encode()).decode()
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _encode_cursor(cursors: dict[str, str]) -> str | None:
        if not cursors:
            return None
        payload = json.dumps(cursors, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _rank(record: Evidence, terms: list[str]) -> tuple[int, float]:
        haystack = f"{record.title or ''} {record.content}".lower()
        relevance = sum(3 if term.lower() in (record.title or "").lower() else 1 for term in terms if term.lower() in haystack)
        completeness = 1 if record.extraction_status == "complete" else 0
        latest = record.updated_at or record.created_at
        timestamp = latest.timestamp() if latest else 0.0
        return relevance + completeness, timestamp

    def _order(self, records: list[Evidence], terms: list[str], limit: int) -> list[Evidence]:
        deduplicated: list[Evidence] = []
        seen_ids: set[str] = set()
        for record in records:
            if record.source_id not in seen_ids:
                deduplicated.append(record)
                seen_ids.add(record.source_id)
        ranked = sorted(deduplicated, key=lambda item: self._rank(item, terms), reverse=True)

        # Return one high-quality result from each provider first, then fill remaining slots.
        primary: list[Evidence] = []
        remainder: list[Evidence] = []
        seen_provider: set[str] = set()
        for record in ranked:
            provider = record.source_id.split(":", 1)[0]
            if provider not in seen_provider:
                primary.append(record)
                seen_provider.add(provider)
            else:
                remainder.append(record)
        return (primary + remainder)[:limit]

    @staticmethod
    def _returned_record_count(source: SourceName, records: list[Evidence]) -> int:
        """Count records that are present in this response, not upstream hits.

        ``coverage.recordCount`` belongs to the public handoff contract.  The
        service ranks, de-duplicates, and applies a global limit after source
        adapters return their candidate lists, so an adapter's raw hit count
        can be larger than the final ``records`` array.  Reporting the final
        count keeps downstream contract validation deterministic.
        """
        return sum(record.source_id.startswith(f"{source.value}:") for record in records)

    async def search(self, request: SearchRequest) -> SearchResponse:
        hints = self.registry.hints(request)
        cursor_state = self._decode_cursor(request.cursor)
        tasks = {
            source: self.adapters[source].search(request, hints, cursor_state.get(source.value))
            for source in request.source_scope
            if source in self.adapters
        }
        settled = await asyncio.gather(*tasks.values(), return_exceptions=True)

        records: list[Evidence] = []
        errors: list[RetrievalError] = []
        coverage: list[Coverage] = []
        next_cursors: dict[str, str] = {}
        successful_sources = 0

        for source, outcome in zip(tasks, settled, strict=True):
            if isinstance(outcome, AdapterFailure):
                errors.append(RetrievalError(source=source, code=outcome.code, message=outcome.message))
                coverage.append(Coverage(source=source, status="failed", recordCount=0))
                continue
            if isinstance(outcome, Exception):
                errors.append(RetrievalError(source=source, code="unexpected_error", message="The source lookup failed unexpectedly."))
                coverage.append(Coverage(source=source, status="failed", recordCount=0))
                continue

            successful_sources += 1
            records.extend(outcome.records)
            errors.extend(outcome.errors)
            coverage.append(Coverage(source=source, status="partial" if outcome.errors else "searched", recordCount=len(outcome.records)))
            if outcome.next_cursor:
                next_cursors[source.value] = outcome.next_cursor

        if not successful_sources:
            status = SearchStatus.FAILED
        elif errors:
            status = SearchStatus.PARTIAL
        elif not records:
            status = SearchStatus.EMPTY
        else:
            status = SearchStatus.OK

        ordered_records = self._order(records, hints.terms, request.limit)
        final_coverage = [
            item.model_copy(update={"record_count": self._returned_record_count(item.source, ordered_records)})
            for item in coverage
        ]

        return SearchResponse(
            requestId=f"req-{uuid.uuid4()}",
            status=status,
            records=ordered_records,
            nextCursor=self._encode_cursor(next_cursors),
            coverage=final_coverage,
            errors=errors,
        )

    async def context(self, source_id: str) -> SearchResponse:
        if source_id.startswith("slack"):
            source = SourceName.SLACK
        elif source_id.startswith("notion:"):
            source = SourceName.NOTION
        elif source_id.startswith("drive:"):
            source = SourceName.DRIVE
        else:
            error = RetrievalError(source="unknown", code="unsupported_source", message="The sourceId does not belong to a supported provider.")
            return SearchResponse(
                requestId=f"req-{uuid.uuid4()}",
                status=SearchStatus.FAILED,
                records=[],
                coverage=[],
                errors=[error],
            )

        try:
            result: SourceResult = await self.adapters[source].fetch_context(source_id)
        except AdapterFailure as error:
            return SearchResponse(
                requestId=f"req-{uuid.uuid4()}",
                status=SearchStatus.FAILED,
                records=[],
                coverage=[Coverage(source=source, status="failed", recordCount=0)],
                errors=[RetrievalError(source=source, code=error.code, message=error.message)],
            )

        status = SearchStatus.PARTIAL if result.errors else SearchStatus.OK if result.records else SearchStatus.EMPTY
        return SearchResponse(
            requestId=f"req-{uuid.uuid4()}",
            status=status,
            records=result.records,
            coverage=[Coverage(source=source, status="partial" if result.errors else "searched", recordCount=len(result.records))],
            errors=result.errors,
        )
