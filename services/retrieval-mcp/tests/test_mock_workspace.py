from __future__ import annotations

import asyncio
import unittest

from retrieval_mcp.mock_workspace import MockWorkspace, mock_service
from retrieval_mcp.models import SearchRequest


class MockWorkspaceTest(unittest.TestCase):
    """The real service, registry and source adapters run against mocked Notion/Slack/Drive HTTP APIs."""

    def setUp(self) -> None:
        self.workspace = MockWorkspace()
        self.service, restore = mock_service(self.workspace)
        self.addCleanup(restore)

    def search(self, query: str, sources: list[str]):
        return asyncio.run(self.service.search(SearchRequest(query=query, sourceScope=sources)))

    def test_real_adapters_read_every_source_without_leaving_the_mock(self) -> None:
        result = self.search("행사비 정산", ["notion", "slack", "drive"])

        self.assertEqual(result.status, "ok")
        self.assertEqual({item.source.value for item in result.coverage if item.record_count}, {"notion", "slack", "drive"})
        self.assertTrue(self.workspace.calls)
        self.assertEqual({host for host, _ in self.workspace.calls}, {"api.notion.com", "slack.com", "www.googleapis.com"})

    def test_coverage_counts_slack_thread_messages(self) -> None:
        result = self.search("법인 계좌", ["slack"])

        self.assertTrue(any(record.source_type == "slack_thread" for record in result.records))
        self.assertEqual(result.coverage[0].record_count, len(result.records))

    def test_approval_rule_is_reachable_only_by_following_its_lead(self) -> None:
        first = self.search("행사비 정산 계좌", ["notion"])
        lead = self.search("결재 규정", ["notion"])

        self.assertTrue(any("결재 규정" in record.content for record in first.records))
        self.assertFalse(any("100만 원" in record.content for record in first.records))
        self.assertTrue(any("100만 원" in record.content for record in lead.records))

    def test_fetch_context_expands_a_slack_thread(self) -> None:
        hit = next(record for record in self.search("법인 계좌", ["slack"]).records if record.source_type == "slack_thread")

        context = asyncio.run(self.service.context(hit.source_id))

        self.assertEqual(context.status, "ok")
        self.assertEqual(len(context.records), 3)

    def test_unknown_page_is_a_source_error_not_a_crash(self) -> None:
        context = asyncio.run(self.service.context("notion:page-missing"))

        self.assertEqual(context.status, "failed")
        self.assertEqual(context.errors[0].code, "object_not_found")


if __name__ == "__main__":
    unittest.main()
