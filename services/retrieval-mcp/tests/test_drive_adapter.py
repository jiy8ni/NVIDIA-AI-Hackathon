from __future__ import annotations

import asyncio
import time
import unittest

import httpx

from retrieval_mcp import adapters
from retrieval_mcp.adapters import DriveAdapter
from retrieval_mcp.mock_workspace import MOCK_TOKEN, _HttpxWithTransport
from retrieval_mcp.models import SearchRequest
from retrieval_mcp.registry import SearchHints

FILES = [{"id": f"doc-{i}", "name": f"규정 {i}", "mimeType": "application/vnd.google-apps.document",
          "webViewLink": f"https://mock-drive.atlas.test/{i}", "createdTime": "2026-09-01T00:00:00Z",
          "modifiedTime": "2026-09-02T00:00:00Z", "owners": [{"displayName": "총무", "emailAddress": "t@atlas.test"}]}
         for i in range(4)]


async def slow_drive(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/export"):
        await asyncio.sleep(0.4)
        return httpx.Response(200, text=f"{request.url.path} 회비 규정 본문")
    if request.url.path.endswith("/files/nope/export"):
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED", "message": "denied"}})
    return httpx.Response(200, json={"files": FILES, "incompleteSearch": False})


class DriveAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        original = adapters.httpx
        adapters.httpx = _HttpxWithTransport(httpx.MockTransport(slow_drive))
        self.addCleanup(setattr, adapters, "httpx", original)
        self.adapter = DriveAdapter()
        self.adapter.access_token = MOCK_TOKEN

    def test_matched_files_are_downloaded_concurrently_in_result_order(self) -> None:
        started = time.monotonic()
        result = asyncio.run(self.adapter.search(SearchRequest(query="회비", sourceScope=["drive"]),
                                                 SearchHints(["회비"], [], [], []), None))
        elapsed = time.monotonic() - started

        self.assertEqual([record.title for record in result.records], ["규정 0", "규정 1", "규정 2", "규정 3"])
        self.assertLess(elapsed, 1.0)  # four sequential 0.4s exports would take 1.6s


if __name__ == "__main__":
    unittest.main()
