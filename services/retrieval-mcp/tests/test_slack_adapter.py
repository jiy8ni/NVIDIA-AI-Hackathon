from __future__ import annotations

import unittest

from retrieval_mcp.adapters import SlackAdapter


class SlackAdapterTest(unittest.TestCase):
    def test_realtime_search_message_becomes_evidence(self) -> None:
        record = SlackAdapter()._evidence(
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000100",
                "content": "행사비는 법인 계좌로 정산합니다.",
                "permalink": "https://example.test/archives/C123/p1710000000000100",
                "author_user_id": "U123",
                "author_name": "총무",
            }
        )

        self.assertEqual(record.source_id, "slack:C123:1710000000.000100")
        self.assertEqual(record.content, "행사비는 법인 계좌로 정산합니다.")
        self.assertEqual(record.author.name, "총무")


if __name__ == "__main__":
    unittest.main()
