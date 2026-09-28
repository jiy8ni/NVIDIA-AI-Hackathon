from __future__ import annotations

import asyncio
import json
import time
import unittest

import httpx

from retrieval_mcp import adapters
from retrieval_mcp.adapters import AdapterFailure, NotionAdapter
from retrieval_mcp.mock_workspace import MOCK_TOKEN, _HttpxWithTransport
from retrieval_mcp.models import SearchRequest
from retrieval_mcp.registry import SearchHints

PAGES = [{"object": "page", "id": f"p{i}", "url": f"https://mock-notion.atlas.test/p{i}",
          "created_time": "2026-09-01T00:00:00Z", "last_edited_time": "2026-09-02T00:00:00Z", "created_by": {"id": "u"},
          "parent": {"type": "data_source_id", "data_source_id": "ds-people" if i >= 6 else "ds-guides"},
          "properties": {"title": {"type": "title", "title": [{"plain_text": f"페이지 {i}"}]}}}
         for i in range(8)]
DATA_SOURCES = [{"object": "data_source", "id": "ds-guides", "title": [{"plain_text": "Guides"}]},
                {"object": "data_source", "id": "ds-people", "title": [{"plain_text": "People"}]}]
# A sub-page of a Guides row and a standalone workspace page, for scope tests.
NESTED = [{**PAGES[0], "id": "q0", "parent": {"type": "page_id", "page_id": "p0"},
           "properties": {"title": {"type": "title", "title": [{"plain_text": "가이드 하위 문서"}]}}},
          {**PAGES[0], "id": "r0", "parent": {"type": "workspace", "workspace": True},
           "properties": {"title": {"type": "title", "title": [{"plain_text": "메모"}]}}}]
REQUEST = SearchRequest(query="본문", sourceScope=["notion"])
HINTS = SearchHints(["본문"], [], [], [])


class SlowNotion:
    """Block reads take 0.2s; optionally the first one is rate limited."""

    def __init__(self, rate_limit_first: bool = False, unauthorized: bool = False, pages: list | None = None) -> None:
        self.pages = PAGES if pages is None else pages
        self.in_flight = self.peak = self.rate_limited = 0
        self.rate_limit_first, self.unauthorized = rate_limit_first, unauthorized
        self.block_reads: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.unauthorized:
            return httpx.Response(401, json={"object": "error", "code": "unauthorized"})
        if request.url.path.endswith("/search"):
            kind = json.loads(request.content or b"{}").get("filter", {}).get("value")
            return httpx.Response(200, json={"results": DATA_SOURCES if kind == "data_source" else self.pages, "has_more": False})
        if "/pages/" in request.url.path:
            return httpx.Response(200, json=next(p for p in self.pages if p["id"] == request.url.path.rsplit("/", 1)[1]))
        if self.rate_limit_first and not self.rate_limited:
            self.rate_limited += 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited"})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.2)
            page = request.url.path.split("/")[3]
            self.block_reads.append(page)
            return httpx.Response(200, json={"results": [{"id": f"{page}-b0", "type": "paragraph", "has_children": False,
                                                          "paragraph": {"rich_text": [{"plain_text": f"{page} 본문"}]}}],
                                             "has_more": False})
        finally:
            self.in_flight -= 1


class NotionIndexTest(unittest.TestCase):
    def adapter(self, notion: SlowNotion) -> NotionAdapter:
        original = adapters.httpx
        adapters.httpx = _HttpxWithTransport(httpx.MockTransport(notion))
        self.addCleanup(setattr, adapters, "httpx", original)
        adapter = NotionAdapter()
        adapter.token, adapter.concurrency = MOCK_TOKEN, 4
        return adapter

    def test_pages_are_indexed_concurrently_within_the_limit_and_in_page_order(self) -> None:
        notion = SlowNotion()
        adapter = self.adapter(notion)

        started = time.monotonic()
        count = asyncio.run(adapter.refresh())
        elapsed = time.monotonic() - started

        self.assertEqual(count, 8)
        self.assertEqual([record.content for record in adapter._index], [f"p{i} 본문" for i in range(8)])
        self.assertLess(elapsed, 1.0)  # eight sequential 0.2s reads take 1.6s
        self.assertLessEqual(notion.peak, 4)

    def test_rate_limited_read_is_retried_instead_of_failing_the_index(self) -> None:
        notion = SlowNotion(rate_limit_first=True)
        adapter = self.adapter(notion)

        self.assertEqual(asyncio.run(adapter.refresh()), 8)
        self.assertEqual(notion.rate_limited, 1)

    def test_first_search_answers_with_partial_results_while_the_index_builds(self) -> None:
        adapter = self.adapter(SlowNotion())
        adapter.wait_seconds = 0.05

        async def scenario():
            started = time.monotonic()
            first = await adapter.search(REQUEST, HINTS, None)
            elapsed = time.monotonic() - started
            await adapter._building
            return first, elapsed, await adapter.search(REQUEST, HINTS, None)

        first, elapsed, complete = asyncio.run(scenario())
        self.assertLess(elapsed, 0.3)
        self.assertEqual([error.code for error in first.errors], ["index_building"])
        self.assertEqual((complete.errors, len(complete.records)), ([], 8))

    def test_stale_index_keeps_answering_while_it_is_rebuilt_in_the_background(self) -> None:
        adapter = self.adapter(SlowNotion())

        async def scenario():
            await adapter.refresh()
            adapter._indexed_at -= adapter.ttl_seconds + 1
            started = time.monotonic()
            result = await adapter.search(REQUEST, HINTS, None)
            elapsed = time.monotonic() - started
            rebuilding = adapter._building is not None and not adapter._building.done()
            await adapter._building
            return result, elapsed, rebuilding

        result, elapsed, rebuilding = asyncio.run(scenario())
        self.assertEqual((result.errors, len(result.records)), ([], 8))
        self.assertLess(elapsed, 0.3)
        self.assertTrue(rebuilding)

    def test_excluded_database_is_neither_read_nor_indexed(self) -> None:
        notion = SlowNotion()
        adapter = self.adapter(notion)
        adapter.exclude = {"People"}

        self.assertEqual(asyncio.run(adapter.refresh()), 6)
        self.assertNotIn("p6", notion.block_reads)
        self.assertNotIn("p7", notion.block_reads)

    def test_allowlisted_database_and_its_sub_pages_are_the_only_pages_indexed(self) -> None:
        notion = SlowNotion(pages=PAGES + NESTED)
        adapter = self.adapter(notion)
        adapter.roots = {"Guides"}

        self.assertEqual(asyncio.run(adapter.refresh()), 7)
        self.assertEqual(sorted(notion.block_reads), ["p0", "p1", "p2", "p3", "p4", "p5", "q0"])

    def test_allowlist_accepts_a_page_title(self) -> None:
        notion = SlowNotion(pages=PAGES + NESTED)
        adapter = self.adapter(notion)
        adapter.roots = {"메모"}

        self.assertEqual(asyncio.run(adapter.refresh()), 1)
        self.assertEqual(notion.block_reads, ["r0"])

    def test_exclusion_also_covers_sub_pages_of_an_excluded_database(self) -> None:
        people_child = {**PAGES[6], "id": "s0", "parent": {"type": "page_id", "page_id": "p6"}}
        notion = SlowNotion(pages=PAGES + [people_child])
        adapter = self.adapter(notion)
        adapter.exclude = {"People"}

        self.assertEqual(asyncio.run(adapter.refresh()), 6)
        self.assertNotIn("s0", notion.block_reads)

    def test_failed_index_build_is_reported_instead_of_empty_results(self) -> None:
        adapter = self.adapter(SlowNotion(unauthorized=True))

        with self.assertRaises(AdapterFailure) as failure:
            asyncio.run(adapter.search(REQUEST, HINTS, None))
        self.assertEqual(failure.exception.code, "unauthorized")


if __name__ == "__main__":
    unittest.main()
