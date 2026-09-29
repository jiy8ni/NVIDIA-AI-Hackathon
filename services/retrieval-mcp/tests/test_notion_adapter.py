from __future__ import annotations

import asyncio
import json
import re
import time
import unittest

import httpx

from retrieval_mcp import adapters
from retrieval_mcp.adapters import AdapterFailure, NotionAdapter
from retrieval_mcp.mock_workspace import MOCK_TOKEN, _HttpxWithTransport
from retrieval_mcp.models import SearchRequest
from retrieval_mcp.registry import SearchHints


def page(page_id: str, title: str, parent: dict) -> dict:
    return {"object": "page", "id": page_id, "url": f"https://mock-notion.atlas.test/{page_id}",
            "created_time": "2026-09-01T00:00:00Z", "last_edited_time": "2026-09-02T00:00:00Z", "created_by": {"id": "u"},
            "parent": parent, "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}}}


def in_source(source_id: str) -> dict:
    return {"type": "data_source_id", "data_source_id": source_id}


PAGES = [page(f"p{i}", f"페이지 {i}", in_source("ds-people" if i >= 6 else "ds-guides")) for i in range(8)]
DATA_SOURCES = [{"object": "data_source", "id": "ds-guides", "title": [{"plain_text": "Guides"}]},
                {"object": "data_source", "id": "ds-people", "title": [{"plain_text": "People"}]}]
# A sub-page of a Guides row and a standalone workspace page, for scope tests.
NESTED = [page("q0", "가이드 하위 문서", {"type": "page_id", "page_id": "p0"}),
          page("r0", "메모", {"type": "workspace", "workspace": True})]
REQUEST = SearchRequest(query="본문", sourceScope=["notion"])
HINTS = SearchHints(["본문"], [], [], [])


class FakeNotion:
    """Notion API stand-in: paginated page listing, data sources, block parents and 0.2s block reads."""

    def __init__(self, pages: list | None = None, sources: list | None = None, blocks: dict | None = None, *,
                 read_delay: float = 0.2, page_delay: float = 0.0, rate_limit_first: bool = False,
                 retry_after: str = "0", failing_pages: tuple = (), page_rate_limited: bool = False) -> None:
        self.pages = PAGES if pages is None else pages
        self.sources = DATA_SOURCES if sources is None else sources
        self.blocks = blocks or {}
        self.read_delay, self.page_delay, self.retry_after = read_delay, page_delay, retry_after
        self.rate_limit_first, self.failing_pages, self.unauthorized = rate_limit_first, failing_pages, False
        self.page_rate_limited = page_rate_limited
        self.in_flight = self.peak = self.rate_limited = 0
        self.block_reads: list[str] = []
        self.listing_sizes: list[int] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.unauthorized:
            return httpx.Response(401, json={"object": "error", "code": "unauthorized"})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            return await self._answer(request.url.path, json.loads(request.content or b"{}"))
        finally:
            self.in_flight -= 1

    async def _answer(self, path: str, body: dict) -> httpx.Response:
        if path.endswith("/search"):
            if body.get("filter", {}).get("value") == "data_source":
                return httpx.Response(200, json={"results": self.sources, "has_more": False})
            size, start = int(body.get("page_size", 100)), int(body.get("start_cursor") or 0)
            self.listing_sizes.append(size)
            more = start + size < len(self.pages)
            return httpx.Response(200, json={"results": self.pages[start:start + size], "has_more": more,
                                             "next_cursor": str(start + size) if more else None})
        if path.startswith("/v1/pages/"):
            if self.page_rate_limited:
                return httpx.Response(429, headers={"Retry-After": self.retry_after}, json={"code": "rate_limited"})
            await asyncio.sleep(self.page_delay)
            return httpx.Response(200, json=next(p for p in self.pages if p["id"] == path.rsplit("/", 1)[1]))
        if children := re.fullmatch(r"/v1/blocks/([^/]+)/children", path):
            if self.rate_limit_first and not self.rate_limited:
                self.rate_limited += 1
                return httpx.Response(429, headers={"Retry-After": self.retry_after}, json={"code": "rate_limited"})
            owner = children.group(1)
            if owner in self.failing_pages:
                return httpx.Response(404, json={"object": "error", "code": "object_not_found"})
            await asyncio.sleep(self.read_delay)
            self.block_reads.append(owner)
            return httpx.Response(200, json={"results": [{"id": f"{owner}-b0", "type": "paragraph", "has_children": False,
                                                          "paragraph": {"rich_text": [{"plain_text": f"{owner} 본문"}]}}],
                                             "has_more": False})
        block = re.fullmatch(r"/v1/blocks/([^/]+)", path)
        if block and block.group(1) in self.blocks:
            return httpx.Response(200, json={"object": "block", "id": block.group(1), "parent": self.blocks[block.group(1)]})
        return httpx.Response(404, json={"object": "error", "code": "object_not_found"})


class NotionIndexTest(unittest.TestCase):
    def adapter(self, notion: FakeNotion) -> NotionAdapter:
        original = adapters.httpx
        adapters.httpx = _HttpxWithTransport(httpx.MockTransport(notion))
        self.addCleanup(setattr, adapters, "httpx", original)
        adapter = NotionAdapter()
        adapter.token, adapter.concurrency, adapter.exclude, adapter.roots = MOCK_TOKEN, 4, set(), set()
        return adapter

    def test_pages_are_indexed_concurrently_within_the_limit_and_in_page_order(self) -> None:
        notion = FakeNotion()
        adapter = self.adapter(notion)

        started = time.monotonic()
        count = asyncio.run(adapter.refresh())
        elapsed = time.monotonic() - started

        self.assertEqual(count, 8)
        self.assertEqual([record.content for record in adapter._index], [f"p{i} 본문" for i in range(8)])
        self.assertLess(elapsed, 1.0)  # eight sequential 0.2s reads take 1.6s
        self.assertLessEqual(notion.peak, 4)

    def test_rate_limited_read_is_retried_instead_of_failing_the_index(self) -> None:
        notion = FakeNotion(rate_limit_first=True)

        self.assertEqual(asyncio.run(self.adapter(notion).refresh()), 8)
        self.assertEqual(notion.rate_limited, 1)

    def test_http_date_retry_after_is_honoured(self) -> None:
        notion = FakeNotion(rate_limit_first=True, retry_after="Wed, 21 Oct 2015 07:28:00 GMT")

        self.assertEqual(asyncio.run(self.adapter(notion).refresh()), 8)

    def test_all_notion_requests_share_the_concurrency_limit(self) -> None:
        notion = FakeNotion(page_delay=0.1)
        adapter = self.adapter(notion)
        adapter.concurrency = 2

        async def burst():
            await asyncio.gather(*(adapter._request("GET", "pages/p0") for _ in range(6)))

        asyncio.run(burst())
        self.assertEqual(notion.peak, 2)

    def test_search_time_page_check_does_not_wait_out_a_rate_limit(self) -> None:
        notion = FakeNotion(page_rate_limited=True, retry_after="30")
        adapter = self.adapter(notion)

        async def scenario():
            await adapter.refresh()
            started = time.monotonic()
            result = await adapter.search(REQUEST, HINTS, None)
            return result, time.monotonic() - started

        result, elapsed = asyncio.run(scenario())
        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(result.records), 8)
        self.assertTrue(all(record.access_status == "accessible" for record in result.records))
        self.assertIn("page_recheck_skipped", [error.code for error in result.errors])

    def test_unreadable_page_is_skipped_and_reported_instead_of_failing_the_build(self) -> None:
        adapter = self.adapter(FakeNotion(failing_pages=("p3",)))

        async def scenario():
            count = await adapter.refresh()
            return count, await adapter.search(REQUEST, HINTS, None)

        count, result = asyncio.run(scenario())
        self.assertEqual(count, 7)
        self.assertEqual(len(result.records), 7)
        self.assertIn("index_pages_skipped", [error.code for error in result.errors])

    def test_first_search_answers_with_partial_results_while_the_index_builds(self) -> None:
        adapter = self.adapter(FakeNotion())
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

    def test_later_searches_do_not_wait_again_while_the_first_build_runs(self) -> None:
        adapter = self.adapter(FakeNotion())
        adapter.concurrency, adapter.wait_seconds = 1, 0.3  # the build takes 1.6s

        # Terms that match nothing, so no search-time page checks queue behind the build's requests.
        no_match = SearchHints(["일치 없음"], [], [], [])

        async def scenario():
            timings = []
            for _ in range(2):
                started = time.monotonic()
                result = await adapter.search(REQUEST, no_match, None)
                timings.append((time.monotonic() - started, [error.code for error in result.errors]))
            adapter._building.cancel()
            return timings

        (first, first_codes), (second, second_codes) = asyncio.run(scenario())
        self.assertGreaterEqual(first, 0.25)
        self.assertLess(second, 0.1)
        self.assertEqual((first_codes, second_codes), (["index_building"], ["index_building"]))

    def test_completed_empty_index_is_not_rebuilt_or_reported_as_building(self) -> None:
        adapter = self.adapter(FakeNotion())
        adapter.roots = {"없는 문서"}

        async def scenario():
            count = await adapter.refresh()
            return count, await adapter.search(REQUEST, HINTS, None)

        count, result = asyncio.run(scenario())
        self.assertEqual((count, result.errors, adapter._building), (0, [], None))

    def test_stale_index_keeps_answering_while_it_is_rebuilt_in_the_background(self) -> None:
        adapter = self.adapter(FakeNotion())

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

    def test_failed_rebuild_of_a_stale_index_is_reported(self) -> None:
        notion = FakeNotion()
        adapter = self.adapter(notion)

        async def scenario():
            await adapter.refresh()
            notion.unauthorized = True
            adapter._indexed_at -= adapter.ttl_seconds + 1
            await adapter.search(REQUEST, HINTS, None)
            with self.assertRaises(AdapterFailure):
                await adapter._building
            return await adapter.search(REQUEST, HINTS, None)

        result = asyncio.run(scenario())
        self.assertEqual(len(result.records), 8)
        self.assertIn("index_refresh_failed", [error.code for error in result.errors])

    def test_refresh_index_request_joins_a_running_build_instead_of_queueing_another(self) -> None:
        notion = FakeNotion()
        adapter = self.adapter(notion)

        async def scenario():
            adapter.start_background_refresh()
            await asyncio.sleep(0.05)  # the background build is now running
            return await adapter.search(SearchRequest(query="본문", sourceScope=["notion"], refreshIndex=True), HINTS, None)

        result = asyncio.run(scenario())
        self.assertEqual(len(result.records), 8)
        self.assertEqual(len(notion.block_reads), 8)

    def test_listing_stops_at_the_page_cap_when_no_scope_is_set(self) -> None:
        notion = FakeNotion()
        adapter = self.adapter(notion)
        adapter.max_pages = 3

        self.assertEqual(asyncio.run(adapter.refresh()), 3)
        self.assertEqual(notion.listing_sizes, [3])

    def test_excluded_database_is_neither_read_nor_indexed(self) -> None:
        notion = FakeNotion()
        adapter = self.adapter(notion)
        adapter.exclude = {"People"}

        self.assertEqual(asyncio.run(adapter.refresh()), 6)
        self.assertNotIn("p6", notion.block_reads)
        self.assertNotIn("p7", notion.block_reads)

    def test_exclusion_also_covers_sub_pages_of_an_excluded_database(self) -> None:
        notion = FakeNotion(pages=PAGES + [page("s0", "하위", {"type": "page_id", "page_id": "p6"})])
        adapter = self.adapter(notion)
        adapter.exclude = {"People"}

        self.assertEqual(asyncio.run(adapter.refresh()), 6)
        self.assertNotIn("s0", notion.block_reads)

    def test_page_inside_a_block_of_an_excluded_row_is_excluded(self) -> None:
        notion = FakeNotion(pages=PAGES + [page("s1", "토글 안 하위", {"type": "block_id", "block_id": "blk-6"})],
                            blocks={"blk-6": {"type": "page_id", "page_id": "p6"}})
        adapter = self.adapter(notion)
        adapter.exclude = {"People"}

        self.assertEqual(asyncio.run(adapter.refresh()), 6)
        self.assertNotIn("s1", notion.block_reads)

    def test_allowlisted_database_and_its_sub_pages_are_the_only_pages_indexed(self) -> None:
        notion = FakeNotion(pages=PAGES + NESTED)
        adapter = self.adapter(notion)
        adapter.roots = {"Guides"}

        self.assertEqual(asyncio.run(adapter.refresh()), 7)
        self.assertEqual(sorted(notion.block_reads), ["p0", "p1", "p2", "p3", "p4", "p5", "q0"])

    def test_allowlist_accepts_a_page_title(self) -> None:
        notion = FakeNotion(pages=PAGES + NESTED)
        adapter = self.adapter(notion)
        adapter.roots = {"메모"}

        self.assertEqual(asyncio.run(adapter.refresh()), 1)
        self.assertEqual(notion.block_reads, ["r0"])

    def test_database_inside_an_allowed_page_is_indexed(self) -> None:
        inline = {"object": "data_source", "id": "ds-inline", "title": [{"plain_text": "인라인 업무"}],
                  "database_parent": {"type": "page_id", "page_id": "r0"}}
        notion = FakeNotion(pages=PAGES + NESTED + [page("t0", "인라인 행", in_source("ds-inline"))],
                            sources=DATA_SOURCES + [inline])
        adapter = self.adapter(notion)
        adapter.roots = {"메모"}

        self.assertEqual(asyncio.run(adapter.refresh()), 2)
        self.assertEqual(sorted(notion.block_reads), ["r0", "t0"])

    def test_names_with_stray_spaces_still_match_the_scope(self) -> None:
        contacts = {"object": "data_source", "id": "ds-contacts", "title": [{"plain_text": "PM 컨택 "}],
                    "database_parent": {"type": "page_id", "page_id": "r0"}}
        notion = FakeNotion(pages=PAGES + NESTED + [page("c0", "연락처 행", in_source("ds-contacts"))],
                            sources=DATA_SOURCES + [contacts])
        adapter = self.adapter(notion)
        adapter.roots, adapter.exclude = {"메모"}, {"PM 컨택"}

        self.assertEqual(asyncio.run(adapter.refresh()), 1)
        self.assertEqual(notion.block_reads, ["r0"])

    def test_ambiguous_root_name_is_warned(self) -> None:
        notion = FakeNotion(pages=PAGES + [page("g1", "Guides", {"type": "workspace", "workspace": True})])
        adapter = self.adapter(notion)
        adapter.roots = {"Guides"}

        with self.assertLogs("retrieval_mcp.adapters", level="WARNING") as logs:
            asyncio.run(adapter.refresh())
        self.assertTrue(any("Guides" in line for line in logs.output))

    def test_evidence_title_uses_the_title_property_not_the_first_text_column(self) -> None:
        row = page("m0", "3차 회의록", in_source("ds-guides"))
        row["properties"] = {"상태": {"type": "rich_text", "rich_text": [{"plain_text": "진행 중"}]}, **row["properties"]}
        block = {"id": "m0-b0", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "안건"}]}}

        self.assertEqual(NotionAdapter()._evidence(row, block).title, "3차 회의록")

    def test_failed_index_build_is_reported_instead_of_empty_results(self) -> None:
        notion = FakeNotion()
        notion.unauthorized = True
        adapter = self.adapter(notion)

        with self.assertRaises(AdapterFailure) as failure:
            asyncio.run(adapter.search(REQUEST, HINTS, None))
        self.assertEqual(failure.exception.code, "unauthorized")


if __name__ == "__main__":
    unittest.main()
