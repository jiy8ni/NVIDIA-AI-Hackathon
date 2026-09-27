"""Search adapters. No external write tool is registered."""
import hashlib
import json
import os
import re
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


def make_provider():
    mode = os.getenv('HANDOFF_RETRIEVAL_MODE', 'fixture')
    if mode == 'fixture':
        return FixtureProvider()
    if mode == 'http':
        return HttpProvider()
    raise ValueError('HANDOFF_RETRIEVAL_MODE must be fixture or http')
