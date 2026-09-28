"""Search adapters. No external write tool is registered."""
import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .contracts import AgentError, Evidence, RetrievalResponse

ROOT = Path(__file__).resolve().parents[3]


def provider_of(e: Evidence):
    if e.sourceType.startswith('slack_'):
        return 'slack'
    if e.sourceType.startswith('notion_'):
        return 'notion'
    if e.sourceType.startswith('drive_'):
        return 'drive'
    # pdf is a format, not a provider. Only an explicit, agreed source ID prefix maps it.
    if e.sourceType == 'pdf_chunk' and e.sourceId.startswith('drive:'):
        return 'drive'
    return None


def safe_url(value):
    try:
        p = urlparse(value)
        return p.scheme in ('https', 'http') and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


class FixtureProvider:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / 'fixtures/evidence.json')

    async def search(self, query, sources, cursor, scope, request_id):
        records = [Evidence.model_validate(x) for x in json.loads(self.path.read_text(encoding='utf-8'))]
        selected = set(sources) & set(scope.sources)
        # Demo indexes are server-bound to one configured team. They are never a multi-tenant ACL substitute.
        if scope.teamId != os.getenv('HANDOFF_TEAM_ID', 'atlas'):
            raise AgentError('SCOPE_DENIED', '이 팀의 자료에 접근할 수 없습니다.', 403)
        terms = set(re.findall(r'[\w가-힣]{2,}', query.lower()))
        def score(e):
            text = (e.content + ' ' + (e.title if e.titleOrigin == 'source' and e.title else '')).lower()
            return sum(1 for t in terms if t in text or any(w.startswith(t[:2]) for w in text.split()))
        eligible = [e for e in records if provider_of(e) in selected]
        matches = sorted([(score(e), i, e) for i, e in enumerate(eligible)], key=lambda x: (-x[0], x[1]))
        matches = [e for n, _, e in matches if n > 0 or query == '*']
        signature = hashlib.sha256((query + '|'.join(sorted(selected))).encode()).hexdigest()[:12]
        offset = 0
        if cursor:
            try:
                sig, position = cursor.split(':')
                offset = int(position)
                if sig != signature or offset < 0:
                    raise ValueError()
            except ValueError as exc:
                raise AgentError('INVALID_CURSOR', '검색 조건과 cursor가 일치하지 않습니다.', 400) from exc
        chosen = matches[offset:offset + 8]
        now = datetime.now(timezone.utc).isoformat()
        chosen = [e.model_copy(update={'retrievedAt': now}) for e in chosen]
        return RetrievalResponse.model_validate({
            'requestId': request_id, 'status': 'ok' if chosen else 'empty',
            'records': [e.model_dump() for e in chosen],
            'nextCursor': f'{signature}:{offset+8}' if offset+8 < len(matches) else None,
            'coverage': [{'source': s, 'status': 'searched',
                          'recordCount': sum(provider_of(e) == s for e in chosen)} for s in sorted(selected)],
            'errors': []})


class HttpProvider:
    def __init__(self):
        self.url = os.environ['HANDOFF_RETRIEVAL_URL']
        if not safe_url(self.url) or (urlparse(self.url).scheme != 'https' and urlparse(self.url).hostname not in ('localhost', '127.0.0.1')):
            raise ValueError('Invalid retrieval URL')

    async def search(self, query, sources, cursor, scope, request_id):
        payload = {'requestId': request_id, 'query': query, 'sources': sources,
                   'cursor': cursor, 'scope': scope.model_dump()}
        headers = {'Authorization': 'Bearer ' + os.getenv('HANDOFF_RETRIEVAL_TOKEN', '')}
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            for attempt in range(2):
                try:
                    response = await client.post(self.url, json=payload, headers=headers)
                    response.raise_for_status()
                    result = RetrievalResponse.model_validate(response.json())
                    if result.requestId != request_id:
                        raise AgentError('RETRIEVAL_INVALID', '검색 응답 requestId가 일치하지 않습니다.')
                    return result
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    if attempt:
                        raise AgentError('UPSTREAM_ERROR', '검색 서버에 연결하지 못했습니다.') from exc
                except (httpx.HTTPStatusError, ValueError) as exc:
                    raise AgentError('RETRIEVAL_INVALID', '검색 응답이 계약을 만족하지 않습니다.') from exc


class McpProvider:
    """Read-only streamable-HTTP bridge to the team's Retrieval MCP.

    The public HandoffOS API and the frozen Evidence contract never see MCP
    protocol details.  ``call_tool`` is injectable so this boundary can be
    verified without an MCP server or source credentials.
    """

    def __init__(self, url=None, call_tool=None):
        self.url = url or os.getenv('RETRIEVAL_MCP_URL', '')
        parsed = urlparse(self.url)
        if not self.url or not safe_url(self.url) or (parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1')):
            raise ValueError('Invalid RETRIEVAL_MCP_URL')
        self._injected_call_tool = call_tool
        self._events = []
        self._context_fetches = 0

    def drain_events(self):
        events, self._events = self._events, []
        return events

    @staticmethod
    def _response_with(result, **updates):
        value = result.model_dump()
        value.update(updates)
        return RetrievalResponse.model_validate(value)

    async def _call_tool(self, name, arguments):
        if self._injected_call_tool:
            return await self._injected_call_tool(name, arguments)
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise AgentError('MCP_NOT_CONFIGURED', 'MCP 연동 환경에 .[mcp] 의존성을 설치하세요.', 503) from exc
        headers = {}
        token = os.getenv('RETRIEVAL_MCP_TOKEN', '')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            async with streamablehttp_client(self.url, headers=headers or None) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except Exception as exc:
            # Tool and protocol details can include source/service information.
            raise AgentError('MCP_UNAVAILABLE', 'Retrieval MCP에 연결하지 못했습니다.', 502) from exc
        if getattr(result, 'isError', False):
            raise AgentError('MCP_TOOL_FAILED', 'Retrieval MCP 도구 호출이 실패했습니다.', 502)
        structured = getattr(result, 'structuredContent', None)
        if isinstance(structured, dict):
            return structured
        for block in getattr(result, 'content', []) or []:
            text = getattr(block, 'text', None)
            if isinstance(text, str):
                try:
                    value = json.loads(text)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    return value
        raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP 도구 응답이 JSON 객체가 아닙니다.')

    @staticmethod
    def _adapt_response(value, request_id, requested_sources, *, allow_subset=False):
        if not isinstance(value, dict):
            raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP 응답 형식이 올바르지 않습니다.')
        adapted = deepcopy(value)
        coverage = adapted.get('coverage')
        if not isinstance(coverage, list):
            raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP coverage가 없습니다.')
        # Retrieval MCP uses coverage=partial for a source with recoverable
        # errors.  The frozen HandoffOS contract expresses that information via
        # response.status=partial and errors, while coverage remains searched.
        had_partial_coverage = False
        for item in coverage:
            if isinstance(item, dict) and item.get('status') == 'partial':
                had_partial_coverage = True
                item['status'] = 'searched'
        if had_partial_coverage and adapted.get('status') != 'partial' and not adapted.get('errors'):
            raise AgentError('RETRIEVAL_INVALID', 'MCP coverage partial에는 partial 상태 또는 오류가 필요합니다.')
        adapted['requestId'] = request_id
        try:
            result = RetrievalResponse.model_validate(adapted)
        except ValueError as exc:
            raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP 응답이 Evidence 계약을 만족하지 않습니다.') from exc
        sources = {item.source for item in result.coverage}
        expected = set(requested_sources)
        if not sources or not sources.issubset(expected) or (not allow_subset and sources != expected):
            raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP coverage 범위가 요청과 다릅니다.')
        if any(provider_of(record) not in expected for record in result.records):
            raise AgentError('SCOPE_DENIED', 'Retrieval MCP가 허용 범위 밖 자료를 반환했습니다.', 403)
        for item in result.coverage:
            if item.recordCount != sum(provider_of(record) == item.source for record in result.records):
                raise AgentError('RETRIEVAL_INVALID', 'Retrieval MCP coverage.recordCount가 records와 다릅니다.')
        return result

    @staticmethod
    def _recount(records, sources):
        return [{'source': source, 'status': 'searched',
                 'recordCount': sum(provider_of(record) == source for record in records)} for source in sources]

    async def search(self, query, sources, cursor, scope, request_id):
        selected = list(dict.fromkeys(sources))
        if not selected or not set(selected).issubset(scope.sources):
            raise AgentError('SCOPE_DENIED', 'MCP 검색 범위가 서버 권한 범위를 벗어났습니다.', 403)
        raw = await self._call_tool('search_evidence', {
            'query': query,
            'sourceScope': selected,
            'cursor': cursor,
        })
        self._events.append({'event': 'mcp_tool', 'tool': 'search_evidence'})
        result = self._adapt_response(raw, request_id, selected)

        # ``read_more`` remains a local context-window action.  This separate,
        # bounded provider-side enrichment is only for a result that the MCP
        # itself labelled as an excerpt/partial extraction.  It is not an LLM
        # action and cannot widen query or source scope.
        candidate = next((record for record in result.records
                          if record.contentOrigin == 'source_excerpt' or record.extractionStatus == 'partial'), None)
        if not candidate or self._context_fetches >= 1:
            return result
        self._context_fetches += 1
        try:
            raw_context = await self._call_tool('fetch_context', {'sourceId': candidate.sourceId})
        except AgentError:
            self._events.append({'event': 'mcp_tool_failed', 'tool': 'fetch_context'})
            return self._response_with(
                result,
                status='partial',
                errors=[*result.errors, {'source': provider_of(candidate) or 'unknown', 'code': 'context_unavailable',
                                          'message': '선택한 자료의 전체 문맥을 확인하지 못했습니다.'}],
                coverage=self._recount(result.records, selected),
            )
        self._events.append({'event': 'mcp_tool', 'tool': 'fetch_context', 'reason': 'excerpt_or_partial'})
        context = self._adapt_response(raw_context, request_id, selected, allow_subset=True)
        source = provider_of(candidate)
        if context.status in ('failed', 'empty') or not context.records:
            errors = result.errors + context.errors + [
                {'source': source or 'unknown', 'code': 'context_unavailable',
                 'message': '선택한 자료의 전체 문맥을 확인하지 못했습니다.'}
            ]
            return self._response_with(result, status='partial', errors=errors,
                                       coverage=self._recount(result.records, selected))
        expanded = [record for record in result.records if record.sourceId != candidate.sourceId]
        expanded.extend(context.records)
        errors = result.errors + context.errors
        return self._response_with(result, status='partial' if errors else result.status,
                                   records=expanded, coverage=self._recount(expanded, selected), errors=errors)


def make_provider():
    mode = os.getenv('HANDOFF_RETRIEVAL_MODE', 'fixture')
    if mode == 'fixture':
        return FixtureProvider()
    if mode == 'http':
        return HttpProvider()
    if mode == 'mcp':
        return McpProvider()
    raise ValueError('HANDOFF_RETRIEVAL_MODE must be fixture, http or mcp')
