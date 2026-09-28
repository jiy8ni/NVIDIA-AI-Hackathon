"""Mock Notion, Slack and Google Drive HTTP APIs for local agent-loop tests.

The MCP server, service, registry and source adapters run unchanged; only the adapters' outbound
HTTP calls are answered from a fictional JSON workspace. Unknown hosts fail instead of reaching the
network, and no real credential is read.

Run: ``uv run retrieval-mcp-mock`` (or ``python -m retrieval_mcp.mock_workspace``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from . import adapters
from .adapters import DriveAdapter, NotionAdapter, SlackAdapter
from .models import SourceName
from .service import RetrievalService

DEFAULT_WORKSPACE = Path(__file__).with_name("mock_data") / "atlas_workspace.json"
MOCK_TOKEN = "mock-token"
# Keyword search approximates Slack's: particles are stripped so "모임통장을" still finds "모임통장".
PARTICLES = ("으로", "에서", "에게", "까지", "부터", "인데", "은", "는", "이", "가", "을", "를", "에", "의", "로", "와", "과", "도", "만")


def keywords(query: str) -> list[str]:
    words = []
    for word in re.findall(r"[\w가-힣]+", query.lower()):
        for particle in PARTICLES:
            if len(word) > len(particle) + 1 and word.endswith(particle):
                word = word[: -len(particle)]
                break
        if len(word) >= 2:
            words.append(word)
    return list(dict.fromkeys(words))


def slack_ts(value: str) -> str:
    return f"{datetime.fromisoformat(value).timestamp():.6f}"


class MockWorkspace:
    def __init__(self, path: Path | str = DEFAULT_WORKSPACE) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.pages = {page["id"]: page for page in data["notion"]["pages"]}
        slack = data["slack"]
        self.slack_workspace, self.channels, self.slack_users = slack["workspace"], slack["channels"], slack["users"]
        ts = {message["id"]: slack_ts(message["at"]) for message in slack["messages"]}
        parents = {message["thread"] for message in slack["messages"] if message.get("thread")}
        self.messages = [
            {**message, "ts": ts[message["id"]],
             "thread_ts": ts[message["thread"]] if message.get("thread") else ts[message["id"]] if message["id"] in parents else None}
            for message in slack["messages"]
        ]
        self.files = {file["id"]: file for file in data["drive"]["files"]}
        self.calls: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append((host, path))
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(401, json={"ok": False, "error": "not_authed"})
        if host == "api.notion.com" and path.startswith("/v1/"):
            return self._notion(request, path.removeprefix("/v1/"))
        if host == "slack.com" and path.startswith("/api/"):
            return self._slack(request, path.removeprefix("/api/"))
        if host == "www.googleapis.com" and path.startswith("/drive/v3/"):
            return self._drive(request, path.removeprefix("/drive/v3/"))
        return httpx.Response(404, json={"error": "mock_host_not_found"})

    # Notion -----------------------------------------------------------------------------------
    @staticmethod
    def _notion_page(page: dict[str, Any]) -> dict[str, Any]:
        return {
            "object": "page", "id": page["id"], "url": f"https://mock-notion.atlas.test/{page['id']}",
            "created_time": page["created"], "last_edited_time": page["edited"],
            "created_by": {"object": "user", "id": page["author"]},
            "properties": {"title": {"id": "title", "type": "title", "title": [{"plain_text": page["title"]}]}},
        }

    @staticmethod
    def _notion_block(page_id: str, index: int, block: str | dict[str, str]) -> dict[str, Any]:
        kind, text = ("paragraph", block) if isinstance(block, str) else (block["type"], block["text"])
        return {"object": "block", "id": f"{page_id}-b{index}", "type": kind, "has_children": False,
                kind: {"rich_text": [{"plain_text": text}]}}

    def _notion(self, request: httpx.Request, path: str) -> httpx.Response:
        missing = httpx.Response(404, json={"object": "error", "code": "object_not_found"})
        if path == "search" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            start, size = int(body.get("start_cursor") or 0), int(body.get("page_size") or 100)
            pages = list(self.pages.values())
            chunk = pages[start : start + size]
            more = start + size < len(pages)
            return httpx.Response(200, json={"object": "list", "results": [self._notion_page(p) for p in chunk],
                                             "has_more": more, "next_cursor": str(start + size) if more else None})
        if match := re.fullmatch(r"blocks/([^/]+)/children", path):
            page = self.pages.get(match.group(1))
            if not page:
                return missing
            blocks = [self._notion_block(page["id"], i, block) for i, block in enumerate(page["blocks"])]
            return httpx.Response(200, json={"object": "list", "results": blocks, "has_more": False, "next_cursor": None})
        if match := re.fullmatch(r"pages/([^/]+)", path):
            page = self.pages.get(match.group(1))
            return httpx.Response(200, json=self._notion_page(page)) if page else missing
        return missing

    # Slack ------------------------------------------------------------------------------------
    def _permalink(self, message: dict[str, Any]) -> str:
        return f"https://{self.slack_workspace}.slack.test/archives/{message['channel']}/p{message['ts'].replace('.', '')}"

    def _slack(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "assistant.search.context":
            body = json.loads(request.content or b"{}")
            words = keywords(re.sub(r"\b(?:after|before):\S+", " ", body.get("query", "")))
            scored = sorted(((sum(word in m["text"].lower() for word in words), m) for m in self.messages),
                            key=lambda item: (-item[0], -float(item[1]["ts"])))
            hits = [m for score, m in scored if score]
            start, limit = int(body.get("cursor") or 0), int(body.get("limit") or 10)
            page = hits[start : start + limit]
            results = [{
                "channel_id": m["channel"], "channel_name": self.channels[m["channel"]], "message_ts": m["ts"],
                **({"thread_ts": m["thread_ts"]} if m["thread_ts"] else {}),
                "content": m["text"], "permalink": self._permalink(m),
                "author_user_id": m["user"], "author_name": self.slack_users.get(m["user"]),
            } for m in page]
            more = start + limit < len(hits)
            return httpx.Response(200, json={"ok": True, "results": {"messages": results},
                                             "response_metadata": {"next_cursor": str(start + limit) if more else ""}})
        if path == "conversations.replies":
            channel, ts = request.url.params.get("channel"), request.url.params.get("ts")
            thread = [m for m in self.messages if m["channel"] == channel and ts in (m["ts"], m["thread_ts"])]
            if not thread:
                return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
            # Like the real API, replies carry user IDs but no display names.
            return httpx.Response(200, json={"ok": True, "messages": [
                {"type": "message", "ts": m["ts"], "user": m["user"], "text": m["text"],
                 **({"thread_ts": m["thread_ts"]} if m["thread_ts"] else {})}
                for m in thread]})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    # Google Drive -----------------------------------------------------------------------------
    @staticmethod
    def _drive_file(file: dict[str, Any]) -> dict[str, Any]:
        return {"id": file["id"], "name": file["name"], "mimeType": file["mimeType"],
                "webViewLink": f"https://mock-drive.atlas.test/document/d/{file['id']}",
                "createdTime": file["created"], "modifiedTime": file["modified"],
                "owners": [{"displayName": file["owner"]["name"], "emailAddress": file["owner"]["email"]}]}

    def _drive(self, request: httpx.Request, path: str) -> httpx.Response:
        missing = httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND", "message": "File not found."}})
        if path == "files":
            terms = [term.replace("\\'", "'").replace("\\\\", "\\").lower()
                     for term in re.findall(r"fullText contains '((?:[^'\\]|\\.)*)'", request.url.params.get("q", ""))]
            hits = sorted((f for f in self.files.values() if any(t in (f["name"] + " " + f["text"]).lower() for t in terms)),
                          key=lambda f: f["modified"], reverse=True)
            start, size = int(request.url.params.get("pageToken") or 0), int(request.url.params.get("pageSize") or 10)
            more = start + size < len(hits)
            return httpx.Response(200, json={"files": [self._drive_file(f) for f in hits[start : start + size]],
                                             "nextPageToken": str(start + size) if more else None, "incompleteSearch": False})
        match = re.fullmatch(r"files/([^/]+)(/export)?", path)
        file = self.files.get(match.group(1)) if match else None
        if not file:
            return missing
        if match.group(2) or request.url.params.get("alt") == "media":
            return httpx.Response(200, text=file["text"])
        return httpx.Response(200, json=self._drive_file(file))


class _HttpxWithTransport:
    """Stand-in for the ``httpx`` module inside ``adapters``: every AsyncClient uses the mock transport."""

    def __init__(self, transport: httpx.MockTransport) -> None:
        self._transport = transport

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:  # noqa: N802 - mirrors httpx
        return httpx.AsyncClient(*args, **{**kwargs, "transport": self._transport})


def install(workspace: MockWorkspace) -> Callable[[], None]:
    """Route the source adapters' HTTP calls to ``workspace``; returns a function that undoes it."""
    original = adapters.httpx
    adapters.httpx = _HttpxWithTransport(workspace.transport())

    def restore() -> None:
        adapters.httpx = original

    return restore


def mock_adapters() -> dict[SourceName, Any]:
    slack, notion, drive = SlackAdapter(), NotionAdapter(), DriveAdapter()
    slack.token, notion.token = MOCK_TOKEN, MOCK_TOKEN
    drive.access_token = MOCK_TOKEN
    return {SourceName.SLACK: slack, SourceName.NOTION: notion, SourceName.DRIVE: drive}


def mock_service(workspace: MockWorkspace) -> tuple[RetrievalService, Callable[[], None]]:
    restore = install(workspace)
    return RetrievalService(adapters=mock_adapters()), restore


def main() -> None:
    from . import server

    workspace = MockWorkspace(os.getenv("MOCK_WORKSPACE_PATH", DEFAULT_WORKSPACE))
    server.service, _ = mock_service(workspace)
    print("MOCK WORKSPACE: fictional Slack/Notion/Drive data, no real credentials or network. "
          f"{len(workspace.pages)} pages, {len(workspace.messages)} messages, {len(workspace.files)} files.", flush=True)
    server.mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
