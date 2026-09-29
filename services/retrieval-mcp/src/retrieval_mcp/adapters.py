"""Read-only source adapters. They return evidence, never generated summaries."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Any

import httpx
from pypdf import PdfReader

from .models import Evidence, PersonRef, RetrievalError, SearchRequest, SourceName, SourceResult
from .registry import SearchHints


logger = logging.getLogger(__name__)


def retry_after_seconds(value: str | None) -> float:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date), clamped to 0-60."""
    if not value:
        return 1.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            seconds = 1.0
    return min(max(seconds, 0.0), 60.0)


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(value), tz=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OSError):
        return None


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class AdapterFailure(RuntimeError):
    def __init__(self, source: SourceName, code: str, message: str):
        super().__init__(message)
        self.source = source
        self.code = code
        self.message = message


class SlackAdapter:
    source = SourceName.SLACK

    def __init__(self) -> None:
        self.token = os.getenv("SLACK_USER_TOKEN")

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise AdapterFailure(self.source, "missing_credentials", "SLACK_USER_TOKEN is not configured.")
        return {"Authorization": f"Bearer {self.token}"}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"https://slack.com/api/{path}", headers=self._headers(), params=params)
        payload = response.json()
        if response.status_code >= 400 or not payload.get("ok"):
            raise AdapterFailure(self.source, payload.get("error", "slack_api_error"), "Slack search or context lookup failed.")
        return payload

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"https://slack.com/api/{path}", headers=self._headers(), json=payload)
        body = response.json()
        if response.status_code >= 400 or not body.get("ok"):
            raise AdapterFailure(self.source, body.get("error", "slack_api_error"), "Slack search or context lookup failed.")
        return body

    @staticmethod
    def _source_id(channel_id: str, ts: str, is_thread: bool) -> str:
        prefix = "slack-thread" if is_thread else "slack"
        return f"{prefix}:{channel_id}:{ts}"

    @staticmethod
    def _permalink(channel_id: str, ts: str, supplied: str | None) -> str:
        return supplied or f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"

    def _evidence(self, message: dict[str, Any], *, thread: bool = False) -> Evidence:
        channel = message.get("channel")
        channel_id = (channel.get("id") if isinstance(channel, dict) else channel) or message.get("channel_id") or "unknown"
        ts = str(message.get("message_ts") or message.get("ts") or message.get("thread_ts") or "unknown")
        thread_ts = str(message.get("thread_ts") or ts)
        is_thread = thread or bool(message.get("thread_ts"))
        return Evidence(
            sourceId=self._source_id(channel_id, thread_ts if is_thread else ts, is_thread),
            sourceType="slack_thread" if is_thread else "slack_message",
            title=None,
            titleOrigin="unavailable",
            content=clean_text(message.get("content") or message.get("text")),
            contentOrigin="source_excerpt",
            url=self._permalink(channel_id, ts, message.get("permalink")),
            createdAt=parse_time(ts),
            updatedAt=parse_time(message.get("edited", {}).get("ts")) or parse_time(ts),
            retrievedAt=utc_now(),
            author=PersonRef(id=message.get("author_user_id") or message.get("user"), name=message.get("author_name") or message.get("username")),
            owner=PersonRef(),
            extractionStatus="complete",
            accessStatus="accessible",
        )

    async def search(self, request: SearchRequest, hints: SearchHints, cursor: str | None) -> SourceResult:
        query = request.query
        if request.time_range and request.time_range.start:
            query += f" after:{request.time_range.start.date().isoformat()}"
        if request.time_range and request.time_range.end:
            query += f" before:{request.time_range.end.date().isoformat()}"

        payload = await self._post(
            "assistant.search.context",
            {
                "query": query,
                "channel_types": ["public_channel", "private_channel"],
                "content_types": ["messages"],
                "include_context_messages": False,
                "limit": request.limit,
                "cursor": cursor or "",
                "sort": "score",
            },
        )
        records = [
            self._evidence(message)
            for message in payload.get("results", {}).get("messages", [])
            if clean_text(message.get("content"))
        ]
        return SourceResult(records=records, next_cursor=payload.get("response_metadata", {}).get("next_cursor") or None)

    async def fetch_context(self, source_id: str) -> SourceResult:
        match = re.fullmatch(r"slack(?:-thread)?:([^:]+):(.+)", source_id)
        if not match:
            raise AdapterFailure(self.source, "invalid_source_id", "The Slack sourceId is invalid.")
        channel_id, root_ts = match.groups()
        payload = await self._get("conversations.replies", {"channel": channel_id, "ts": root_ts, "limit": 100})
        messages = sorted(payload.get("messages", []), key=lambda item: float(item.get("ts", 0)))
        records = [self._evidence({**message, "channel_id": channel_id}, thread=index == 0) for index, message in enumerate(messages)]
        return SourceResult(records=records)


class NotionAdapter:
    source = SourceName.NOTION

    def __init__(self) -> None:
        self.token = os.getenv("NOTION_TOKEN")
        self.version = os.getenv("NOTION_VERSION", "2026-03-11")
        self.max_pages = int(os.getenv("NOTION_INDEX_MAX_PAGES", "200"))
        self.ttl_seconds = int(os.getenv("NOTION_INDEX_TTL_SECONDS", "900"))
        # Notion requests in flight at once, shared by index builds and searches. Notion allows about
        # three requests per second on average; bursts above that come back as 429 and are retried.
        self.concurrency = max(1, int(os.getenv("NOTION_INDEX_CONCURRENCY", "4")))
        # Databases or pages (names or IDs) never indexed, e.g. a member directory with contact details.
        self.exclude = {name.strip() for name in os.getenv("NOTION_INDEX_EXCLUDE", "").split(",") if name.strip()}
        # Databases or pages (names or IDs) whose pages, sub-pages and inline databases are indexed. An
        # allowlist keeps new, unrelated or personal databases out by default; empty means every shared page.
        self.roots = {name.strip() for name in os.getenv("NOTION_INDEX_ROOTS", "").split(",") if name.strip()}
        # A large workspace takes minutes to index under the rate limit. Searches wait at most this long
        # after a first build starts, then answer from the pages indexed so far, marked as partial.
        self.wait_seconds = float(os.getenv("NOTION_INDEX_WAIT_SECONDS", "5"))
        self._index: list[Evidence] = []
        self._index_errors: list[RetrievalError] = []
        self._indexed_at = 0.0  # 0 until a build completes; a completed index may be empty
        self._partial: list[Evidence] = []
        self._pages_done = self._pages_total = 0
        self._building: asyncio.Task[int] | None = None
        self._build_started = 0.0
        self._build_error: BaseException | None = None
        self._lock = asyncio.Lock()
        self._slots: asyncio.Semaphore | None = None
        self._slots_loop: asyncio.AbstractEventLoop | None = None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise AdapterFailure(self.source, "missing_credentials", "NOTION_TOKEN is not configured.")
        return {"Authorization": f"Bearer {self.token}", "Notion-Version": self.version}

    def _request_slots(self) -> asyncio.Semaphore:
        # One budget for every Notion call, so search-time page checks queue behind an index build
        # instead of bursting into 429s while the build saturates the rate limit.
        loop = asyncio.get_running_loop()
        if self._slots is None or self._slots_loop is not loop:
            self._slots, self._slots_loop = asyncio.Semaphore(self.concurrency), loop
        return self._slots

    async def _request(self, method: str, path: str, client: Any = None, wait_on_rate_limit: bool = True,
                       **kwargs: Any) -> dict[str, Any]:
        headers = self._headers()
        url = f"https://api.notion.com/v1/{path}"
        for attempt in range(4):
            async with self._request_slots():
                if client is None:
                    async with httpx.AsyncClient(timeout=30) as own:
                        response = await own.request(method, url, headers=headers, **kwargs)
                else:
                    response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code != 429 or attempt == 3 or not wait_on_rate_limit:
                break
            await asyncio.sleep(retry_after_seconds(response.headers.get("Retry-After")))
        if response.status_code >= 400:
            code = "notion_api_error"
            try:
                code = response.json().get("code", code)
            except ValueError:
                pass
            raise AdapterFailure(self.source, code, "Notion search or block lookup failed.")
        return response.json()

    @staticmethod
    def _rich_text(value: dict[str, Any]) -> str:
        for key in ("rich_text", "title"):
            entries = value.get(key, [])
            if isinstance(entries, list) and entries:
                return "".join(item.get("plain_text", "") for item in entries if isinstance(item, dict))
        return ""

    @staticmethod
    def _page_title(page: dict[str, Any] | None) -> str:
        for value in (page or {}).get("properties", {}).values():
            if value.get("type") == "title":
                return "".join(part.get("plain_text", "") for part in value.get("title", [])).strip()
        return ""

    def _title(self, page: dict[str, Any]) -> str | None:
        # The title-typed property is the page name; other text columns (status, notes) are not.
        title = self._page_title(page)
        if title:
            return title
        for property_value in page.get("properties", {}).values():
            text = self._rich_text(property_value)
            if text:
                return text
        return None

    def _block_text(self, block: dict[str, Any]) -> str:
        payload = block.get(block.get("type", ""), {})
        text = self._rich_text(payload)
        if text:
            return text
        return clean_text(payload.get("caption", [{}])[0].get("plain_text") if payload.get("caption") else "")

    async def _children(self, block_id: str, depth: int = 0, client: Any = None) -> list[dict[str, Any]]:
        cursor: str | None = None
        collected: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = await self._request("GET", f"blocks/{block_id}/children", client, params=params)
            results = payload.get("results", [])
            collected.extend(results)
            if depth < 2:
                for block in results:
                    if block.get("has_children"):
                        collected.extend(await self._children(block["id"], depth + 1, client))
            if not payload.get("has_more"):
                return collected
            cursor = payload.get("next_cursor")

    async def _pages(self, client: Any = None, limit: int = 10_000) -> list[dict[str, Any]]:
        cursor: str | None = None
        pages: list[dict[str, Any]] = []
        while len(pages) < limit:
            body: dict[str, Any] = {"page_size": min(100, limit - len(pages))}
            if cursor:
                body["start_cursor"] = cursor
            payload = await self._request("POST", "search", client, json=body)
            pages.extend(item for item in payload.get("results", []) if item.get("object") == "page")
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
        return pages[:limit]

    async def _data_sources(self, client: Any = None) -> dict[str, dict[str, Any]]:
        """Data sources keyed by data source ID and by database ID, with the database's own parent."""
        sources: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100, "filter": {"value": "data_source", "property": "object"}}
            if cursor:
                body["start_cursor"] = cursor
            payload = await self._request("POST", "search", client, json=body)
            for source in payload.get("results", []):
                # Names are compared stripped: real databases are named e.g. "PM 컨택 " with a trailing space.
                info = {"id": source["id"], "name": "".join(part.get("plain_text", "") for part in source.get("title", [])).strip(),
                        "parent": source.get("database_parent") or {}}
                sources[source["id"]] = info
                database = (source.get("parent") or {}).get("database_id")
                if database:
                    sources[database] = info
            if not payload.get("has_more"):
                return sources
            cursor = payload.get("next_cursor")

    async def _block_parents(self, pages: list[dict[str, Any]], sources: dict[str, dict[str, Any]],
                             client: Any = None) -> dict[str, dict[str, Any]]:
        """Parents of the blocks (toggles, columns, synced blocks) that pages or databases sit in."""
        references = [page.get("parent", {}) for page in pages] + [info["parent"] for info in sources.values()]
        pending = {ref["block_id"] for ref in references if ref.get("type") == "block_id" and ref.get("block_id")}
        parents: dict[str, dict[str, Any]] = {}
        for _ in range(10):
            batch = sorted(pending - parents.keys())
            if not batch:
                break
            results = await asyncio.gather(*(self._request("GET", f"blocks/{block}", client) for block in batch),
                                           return_exceptions=True)
            pending = set()
            for block, result in zip(batch, results, strict=True):
                parent = result.get("parent", {}) if isinstance(result, dict) else {}
                parents[block] = parent
                if parent.get("type") == "block_id" and parent.get("block_id"):
                    pending.add(parent["block_id"])
        return parents

    def _scoped(self, pages: list[dict[str, Any]], sources: dict[str, dict[str, Any]],
                blocks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Pages under an allowed root and under no excluded one, matched by name or ID at any ancestor,
        following page, database and block parents up to the workspace."""
        by_id = {page["id"]: page for page in pages}

        def ancestry(page: dict[str, Any]) -> set[str]:
            names, parent = {page["id"], self._page_title(page)}, page.get("parent", {})
            for _ in range(50):
                kind = parent.get("type")
                if kind == "page_id":
                    node = by_id.get(parent["page_id"])
                    names |= {parent["page_id"], self._page_title(node)}
                    parent = node.get("parent", {}) if node else {}
                elif kind in ("data_source_id", "database_id"):
                    key = parent.get("data_source_id") or parent.get("database_id")
                    info = sources.get(key, {})
                    names |= {key, parent.get("database_id", ""), info.get("name", "")}
                    parent = info.get("parent", {})
                elif kind == "block_id":
                    names.add(parent["block_id"])
                    parent = blocks.get(parent["block_id"], {})
                else:
                    break
            names.discard("")
            return names

        self._warn_ambiguous(pages, sources)
        scoped = []
        for page in pages:
            chain = ancestry(page)
            if chain & self.exclude or (self.roots and not chain & self.roots):
                continue
            scoped.append(page)
        return scoped

    def _warn_ambiguous(self, pages: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> None:
        named: dict[str, set[str]] = {}
        for page in pages:
            named.setdefault(self._page_title(page), set()).add(page["id"])
        for info in sources.values():
            named.setdefault(info["name"], set()).add(info["id"])
        for entry in sorted(self.roots | self.exclude):
            if len(named.get(entry, ())) > 1:
                logger.warning("Notion index scope entry %r matches %d pages or databases; use IDs to be specific.",
                               entry, len(named[entry]))

    def _evidence(self, page: dict[str, Any], block: dict[str, Any]) -> Evidence | None:
        content = clean_text(self._block_text(block))
        if not content:
            return None
        page_id, block_id = page["id"], block["id"]
        return Evidence(
            sourceId=f"notion:{page_id}:{block_id}",
            sourceType="notion_block",
            title=self._title(page),
            titleOrigin="source" if self._title(page) else "unavailable",
            content=content,
            contentOrigin="source_excerpt",
            url=page.get("url", ""),
            createdAt=parse_time(page.get("created_time")),
            updatedAt=parse_time(page.get("last_edited_time")),
            retrievedAt=utc_now(),
            author=PersonRef(id=page.get("created_by", {}).get("id"), name=None),
            owner=PersonRef(),
            extractionStatus="complete",
            accessStatus="accessible",
        )

    async def refresh(self) -> int:
        async with self._lock:
            # One pooled client and several pages in flight; a sequential rebuild of about 1,000 pages
            # took over 14 minutes at 2.3 requests per second.
            slots = asyncio.Semaphore(self.concurrency)
            partial: list[Evidence] = []
            skipped: list[str] = []
            self._partial = partial  # searchable while the first build runs
            async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=self.concurrency)) as client:
                scoped = bool(self.roots or self.exclude)
                # Without a scope the page cap bounds the listing; a scope needs every page's ancestry.
                pages = await self._pages(client, 10_000 if scoped else self.max_pages)
                if scoped:
                    sources = await self._data_sources(client)
                    pages = self._scoped(pages, sources, await self._block_parents(pages, sources, client))
                pages = pages[: self.max_pages]
                self._pages_done, self._pages_total = 0, len(pages)

                async def page_records(page: dict[str, Any]) -> list[Evidence]:
                    try:
                        async with slots:
                            blocks = await self._children(page["id"], client=client)
                    except (AdapterFailure, httpx.HTTPError) as failure:
                        # One unreadable page (restricted child, broken synced block, network blip) must not
                        # discard a build that can take many minutes; it is reported with the index instead.
                        skipped.append(getattr(failure, "code", "notion_request_failed"))
                        return []
                    finally:
                        self._pages_done += 1
                    records = [evidence for block in blocks if (evidence := self._evidence(page, block))]
                    partial.extend(records)
                    return records

                batches = await asyncio.gather(*(page_records(page) for page in pages))
            if pages and len(skipped) == len(pages):
                raise AdapterFailure(self.source, skipped[0], "No Notion page in the index scope could be read.")
            self._index = [record for batch in batches for record in batch]
            self._index_errors = [RetrievalError(
                source=self.source, code="index_pages_skipped",
                message=f"{len(skipped)} of {len(pages)} Notion pages could not be read "
                        f"({', '.join(sorted(set(skipped)))}); results may be incomplete.")] if skipped else []
            self._indexed_at = time.monotonic()
            self._build_error = None
            return len(self._index)

    def start_background_refresh(self) -> asyncio.Task[int]:
        if self._building is None or self._building.done():
            self._build_started = time.monotonic()
            self._building = asyncio.create_task(self.refresh())
            self._building.add_done_callback(self._build_finished)
        return self._building

    def _build_finished(self, task: asyncio.Task[int]) -> None:
        # Retrieving the exception also keeps asyncio from reporting it as unhandled.
        if task.cancelled():
            return
        self._build_error = task.exception()
        if self._build_error is not None:
            logger.warning("Notion index build failed: %s",
                           getattr(self._build_error, "code", type(self._build_error).__name__))

    def _index_status(self) -> list[RetrievalError]:
        errors = list(self._index_errors)
        if self._build_error is not None:
            code = getattr(self._build_error, "code", type(self._build_error).__name__)
            errors.append(RetrievalError(source=self.source, code="index_refresh_failed",
                                         message=f"The Notion index could not be refreshed ({code}); results may be stale."))
        return errors

    async def _searchable(self) -> tuple[list[Evidence], list[RetrievalError]]:
        built = self._indexed_at > 0
        if built and time.monotonic() - self._indexed_at <= self.ttl_seconds:
            return self._index, self._index_status()
        building = self.start_background_refresh()
        if built:
            return self._index, self._index_status()  # stale: keep answering while the rebuild runs
        remaining = self.wait_seconds - (time.monotonic() - self._build_started)
        if remaining > 0:
            try:
                await asyncio.wait_for(asyncio.shield(building), remaining)
            except asyncio.TimeoutError:
                pass
        if building.done() and not building.cancelled() and building.exception():
            raise building.exception()
        if self._indexed_at > 0:
            return self._index, self._index_status()
        progress = f"{self._pages_done} of {self._pages_total or '?'} pages"
        return list(self._partial), [RetrievalError(source=self.source, code="index_building",
                                                    message=f"The Notion index is still being built ({progress}); results are partial.")]

    @staticmethod
    def _score(record: Evidence, terms: list[str]) -> int:
        haystack = f"{record.title or ''} {record.content}".lower()
        return sum(1 for term in terms if term.lower() in haystack)

    async def _refresh_selected_pages(self, records: list[Evidence]) -> SourceResult:
        """Re-check selected pages without turning the memory index into the SSOT."""
        page_ids = list(dict.fromkeys(record.source_id.split(":", 2)[1] for record in records))
        # A search must not sit out Retry-After (30s or more was seen) while an index build holds the rate
        # limit: a rate-limited check keeps the indexed record and says it was not re-checked.
        pages = await asyncio.gather(
            *(self._request("GET", f"pages/{page_id}", wait_on_rate_limit=False) for page_id in page_ids),
            return_exceptions=True,
        )
        latest: dict[str, dict[str, Any]] = {}
        unchecked: set[str] = set()
        errors: list[RetrievalError] = []
        for page_id, page in zip(page_ids, pages, strict=True):
            if isinstance(page, AdapterFailure) and page.code == "rate_limited":
                unchecked.add(page_id)
            elif isinstance(page, AdapterFailure):
                errors.append(RetrievalError(source=self.source, code=page.code, message=page.message))
            elif isinstance(page, Exception):
                errors.append(RetrievalError(source=self.source, code="notion_page_refresh_failed", message="Could not refresh a selected Notion page."))
            else:
                latest[page_id] = page

        refreshed = []
        for record in records:
            page_id = record.source_id.split(":", 2)[1]
            if page_id in unchecked:
                refreshed.append(record)
                continue
            page = latest.get(page_id)
            if not page:
                refreshed.append(record.model_copy(update={"access_status": "unknown", "retrieved_at": utc_now()}))
                continue
            refreshed.append(
                record.model_copy(
                    update={
                        "title": self._title(page),
                        "title_origin": "source" if self._title(page) else "unavailable",
                        "updated_at": parse_time(page.get("last_edited_time")),
                        "retrieved_at": utc_now(),
                    }
                )
            )
        if unchecked:
            errors.append(RetrievalError(source=self.source, code="page_recheck_skipped",
                                         message=f"{len(unchecked)} selected Notion pages were not re-checked because Notion "
                                                 "is rate limiting; they are returned as indexed."))
        return SourceResult(records=refreshed, errors=errors)

    async def search(self, request: SearchRequest, hints: SearchHints, _: str | None) -> SourceResult:
        if request.refresh_index:
            # Join a running build rather than queueing a second full build behind it.
            await asyncio.shield(self.start_background_refresh())
            index, errors = self._index, self._index_status()
        else:
            index, errors = await self._searchable()
        records = [record for record in index if self._score(record, hints.terms)]
        if hints.notion_roots:
            roots = set(hints.notion_roots)
            records = [record for record in records if record.source_id.split(":", 2)[1] in roots]
        if request.time_range and request.time_range.start:
            records = [record for record in records if not record.updated_at or record.updated_at >= request.time_range.start]
        if request.time_range and request.time_range.end:
            records = [record for record in records if not record.updated_at or record.updated_at <= request.time_range.end]
        selected = sorted(records, key=lambda record: self._score(record, hints.terms), reverse=True)[: request.limit * 2]
        result = await self._refresh_selected_pages(selected)
        return SourceResult(records=result.records, errors=[*errors, *result.errors])

    async def fetch_context(self, source_id: str) -> SourceResult:
        match = re.fullmatch(r"notion:([^:]+)(?::[^:]+)?", source_id)
        if not match:
            raise AdapterFailure(self.source, "invalid_source_id", "The Notion sourceId is invalid.")
        page_id = match.group(1)
        page = await self._request("GET", f"pages/{page_id}")
        blocks = await self._children(page_id)
        records = [item for block in blocks if (item := self._evidence(page, block))]
        return SourceResult(records=records)


class DriveAdapter:
    source = SourceName.DRIVE

    def __init__(self) -> None:
        self.access_token = os.getenv("GOOGLE_ACCESS_TOKEN")
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
        self.corpora = os.getenv("GOOGLE_DRIVE_CORPORA", "allDrives")

    async def _token(self) -> str:
        if self.access_token:
            return self.access_token
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            raise AdapterFailure(self.source, "missing_credentials", "Google OAuth credentials are not configured.")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if response.status_code >= 400:
            try:
                code = response.json().get("error", "oauth_refresh_failed")
            except ValueError:
                code = "oauth_refresh_failed"
            raise AdapterFailure(self.source, str(code), "Google OAuth token refresh failed.")
        self.access_token = response.json().get("access_token")
        if not self.access_token:
            raise AdapterFailure(self.source, "oauth_refresh_failed", "Google OAuth returned no access token.")
        return self.access_token

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await self._token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                error = response.json().get("error", {})
                code = error.get("status") or error.get("code") or "drive_api_error"
                message = error.get("message") or "Google Drive search or file lookup failed."
            except ValueError:
                code = "drive_api_error"
                message = "Google Drive search or file lookup failed."
            raise AdapterFailure(self.source, str(code).lower(), message)
        return response

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _chunks(text: str, size: int = 1400) -> list[str]:
        text = clean_text(text)
        if not text:
            return []
        return [text[index : index + size] for index in range(0, len(text), size)]

    async def _content(self, file: dict[str, Any]) -> tuple[str, str]:
        file_id = file["id"]
        mime_type = file.get("mimeType", "")
        if mime_type == "application/vnd.google-apps.document":
            response = await self._request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                params={"mimeType": "text/plain"},
            )
            return response.text, "drive_document"
        response = await self._request("GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"alt": "media"})
        if mime_type == "application/pdf" or file.get("name", "").lower().endswith(".pdf"):
            try:
                return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages), "pdf_chunk"
            except Exception as error:
                raise AdapterFailure(self.source, "pdf_parse_failed", f"Could not extract text from {file.get('name', file_id)}.") from error
        return response.text, "drive_file"

    def _evidence(self, file: dict[str, Any], content: str, source_type: str, chunk_index: int) -> Evidence:
        owner = (file.get("owners") or [{}])[0]
        return Evidence(
            sourceId=f"drive:{file['id']}:chunk:{chunk_index}",
            sourceType=source_type,
            title=file.get("name"),
            titleOrigin="source" if file.get("name") else "unavailable",
            content=content,
            contentOrigin="parsed_text" if source_type == "pdf_chunk" else "source_excerpt",
            url=file.get("webViewLink", ""),
            createdAt=parse_time(file.get("createdTime")),
            updatedAt=parse_time(file.get("modifiedTime")),
            retrievedAt=utc_now(),
            author=PersonRef(id=owner.get("emailAddress"), name=owner.get("displayName")),
            owner=PersonRef(id=owner.get("emailAddress"), name=owner.get("displayName")),
            extractionStatus="complete",
            accessStatus="accessible",
        )

    async def _file_records(self, file: dict[str, Any]) -> list[Evidence]:
        content, source_type = await self._content(file)
        chunks = self._chunks(content)
        if not chunks:
            return [
                self._evidence(
                    file,
                    clean_text(file.get("name")),
                    source_type,
                    0,
                ).model_copy(update={"extraction_status": "partial"})
            ]
        return [self._evidence(file, chunk, source_type, index) for index, chunk in enumerate(chunks)]

    async def search(self, request: SearchRequest, hints: SearchHints, cursor: str | None) -> SourceResult:
        clauses = [f"fullText contains '{self._escape(term)}'" for term in hints.terms[:4]]
        query = "trashed = false and (" + " or ".join(clauses) + ")"
        if hints.drive_folders:
            parents = [f"'{self._escape(folder)}' in parents" for folder in hints.drive_folders]
            query += " and (" + " or ".join(parents) + ")"
        if request.time_range and request.time_range.start:
            query += f" and modifiedTime >= '{request.time_range.start.isoformat()}'"
        if request.time_range and request.time_range.end:
            query += f" and modifiedTime <= '{request.time_range.end.isoformat()}'"
        params: dict[str, Any] = {
            "q": query,
            "pageSize": min(max(request.limit, 10), 100),
            "orderBy": "modifiedTime desc",
            "corpora": self.corpora,
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,webViewLink,createdTime,modifiedTime,owners(displayName,emailAddress))",
        }
        if cursor:
            params["pageToken"] = cursor
        response = await self._request("GET", "https://www.googleapis.com/drive/v3/files", params=params)
        payload = response.json()
        records: list[Evidence] = []
        errors: list[RetrievalError] = []
        # Download matched files concurrently: one-by-one exports took ~25s per search on a real Drive.
        files = payload.get("files", [])[: request.limit]
        outcomes = await asyncio.gather(*(self._file_records(file) for file in files), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, AdapterFailure):
                errors.append(RetrievalError(source=self.source, code=outcome.code, message=outcome.message))
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                records.extend(outcome)
        if payload.get("incompleteSearch"):
            errors.append(RetrievalError(source=self.source, code="incomplete_search", message="Drive could not search every shared-drive corpus."))
        return SourceResult(records=records, errors=errors, next_cursor=payload.get("nextPageToken") or None)

    async def fetch_context(self, source_id: str) -> SourceResult:
        match = re.fullmatch(r"drive:([^:]+)(?::chunk:\d+)?", source_id)
        if not match:
            raise AdapterFailure(self.source, "invalid_source_id", "The Drive sourceId is invalid.")
        file_id = match.group(1)
        fields = "id,name,mimeType,webViewLink,createdTime,modifiedTime,owners(displayName,emailAddress)"
        response = await self._request("GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"fields": fields, "supportsAllDrives": "true"})
        return SourceResult(records=await self._file_records(response.json()))
