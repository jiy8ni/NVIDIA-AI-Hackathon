from __future__ import annotations

import unittest
from datetime import UTC, datetime

from retrieval_mcp.adapters import AdapterFailure
from retrieval_mcp.models import Evidence, PersonRef, SearchRequest, SourceName, SourceResult
from retrieval_mcp.registry import SearchHints
from retrieval_mcp.service import RetrievalService


def evidence(source_id: str) -> Evidence:
    return Evidence(
        sourceId=source_id,
        sourceType="slack_message",
        title=None,
        titleOrigin="unavailable",
        content="행사비는 법인 계좌로 정산합니다.",
        contentOrigin="source_excerpt",
        url="https://example.test/source",
        createdAt=datetime(2026, 9, 1, tzinfo=UTC),
        updatedAt=datetime(2026, 9, 1, tzinfo=UTC),
        retrievedAt=datetime.now(UTC),
        author=PersonRef(id="user-1", name="총무"),
        owner=PersonRef(),
    )


class Registry:
    def hints(self, request: SearchRequest) -> SearchHints:
        return SearchHints([request.query, *request.entities], [], [], [])


class WorkingAdapter:
    async def search(self, *_: object) -> SourceResult:
        return SourceResult(records=[evidence("slack:C1:1.0")])

    async def fetch_context(self, _: str) -> SourceResult:
        return SourceResult(records=[evidence("slack:C1:1.0")])


class FailingAdapter:
    async def search(self, *_: object) -> SourceResult:
        raise AdapterFailure(SourceName.NOTION, "permission_denied", "Notion access was denied.")

    async def fetch_context(self, _: str) -> SourceResult:
        raise AdapterFailure(SourceName.NOTION, "permission_denied", "Notion access was denied.")


class EmptyAdapter:
    async def search(self, *_: object) -> SourceResult:
        return SourceResult()

    async def fetch_context(self, _: str) -> SourceResult:
        return SourceResult()


class ManyRecordsAdapter:
    def __init__(self, source: SourceName, count: int) -> None:
        self.source = source
        self.count = count

    async def search(self, *_: object) -> SourceResult:
        return SourceResult(
            records=[evidence(f"{self.source.value}:C1:{index}") for index in range(self.count)]
        )

    async def fetch_context(self, _: str) -> SourceResult:
        return SourceResult()


class RetrievalServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_partial_search_preserves_successful_evidence(self) -> None:
        service = RetrievalService(
            adapters={SourceName.SLACK: WorkingAdapter(), SourceName.NOTION: FailingAdapter()},
            registry=Registry(),
        )
        response = await service.search(SearchRequest(query="행사비", sourceScope=[SourceName.SLACK, SourceName.NOTION]))

        self.assertEqual(response.status, "partial")
        self.assertEqual(response.records[0].source_id, "slack:C1:1.0")
        self.assertEqual(response.coverage[0].status, "searched")
        self.assertEqual(response.coverage[1].status, "failed")
        self.assertEqual(response.errors[0].code, "permission_denied")

    async def test_context_returns_a_tool_contract(self) -> None:
        service = RetrievalService(adapters={SourceName.SLACK: WorkingAdapter()}, registry=Registry())
        payload = (await service.context("slack:C1:1.0")).as_tool_result()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["records"][0]["sourceId"], "slack:C1:1.0")

    async def test_empty_search_is_not_reported_as_a_source_failure(self) -> None:
        service = RetrievalService(adapters={SourceName.SLACK: EmptyAdapter()}, registry=Registry())
        response = await service.search(SearchRequest(query="없는 자료", sourceScope=[SourceName.SLACK]))

        self.assertEqual(response.status, "empty")
        self.assertEqual(response.coverage[0].status, "searched")
        self.assertEqual(response.errors, [])

    async def test_coverage_counts_only_records_returned_after_global_limit(self) -> None:
        service = RetrievalService(
            adapters={
                SourceName.SLACK: ManyRecordsAdapter(SourceName.SLACK, 3),
                SourceName.NOTION: ManyRecordsAdapter(SourceName.NOTION, 2),
            },
            registry=Registry(),
        )

        response = await service.search(
            SearchRequest(
                query="행사비",
                sourceScope=[SourceName.SLACK, SourceName.NOTION],
                limit=2,
            )
        )

        self.assertEqual(len(response.records), 2)
        counts = {item.source: item.record_count for item in response.coverage}
        self.assertEqual(counts[SourceName.SLACK], 1)
        self.assertEqual(counts[SourceName.NOTION], 1)
        self.assertEqual(sum(counts.values()), len(response.records))

    async def test_unsupported_context_has_an_explicit_error(self) -> None:
        service = RetrievalService(adapters={}, registry=Registry())
        response = await service.context("email:123")

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.errors[0].source, "unknown")


if __name__ == "__main__":
    unittest.main()
