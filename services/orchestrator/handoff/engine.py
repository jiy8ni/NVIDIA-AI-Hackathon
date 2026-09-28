"""Bounded, read-only agent loop shared by generate, ask and the optional NAT adapter."""
import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .contracts import AgentError, Decision, Gap, RetrievalResponse
from .models import make_model
from .providers import ROOT, make_provider, provider_of, safe_url
from .projection import project, repair_synthesis, validate_synthesis

INJECTION = re.compile(r'ignore.{0,40}(?:instruction|previous)|시스템.{0,30}무시|외부.{0,30}전송|upload.{0,30}secret', re.I)


def broad_query(request):
    """Role-scoped stand-in for '*', which means "the whole virtual org" only to the fixture provider."""
    return ' '.join(part for part in (request.role.strip(), '온보딩') if part)


def extracted(result):
    return len(result.facts) + len(result.tasks) + len(result.conflicts) + len(result.people) + len(result.milestones)


# Per-request loop budgets. Deadlines (seconds) are defaults for HANDOFF_*_TIMEOUT_SECONDS; searching stops
# early enough to leave `reserve` seconds for synthesis, one repair round and validation.
BUDGETS = {
    'ask': {'searches': 4, 'memory': 1, 'candidates': 40, 'deadline': 180, 'reserve': 70},
    'generate': {'searches': 8, 'memory': 2, 'candidates': 64, 'deadline': 360, 'reserve': 160},
}
VISIBLE_RECORDS = 24


def search_cutoff(mode, deadline):
    return max(deadline / 2, deadline - BUDGETS[mode]['reserve'])


DIGEST_CHARS = 400


def digest(rows):
    """What a routing decision needs: the opening of each record. Synthesis still receives the full rows.

    With real Drive chunks (up to 1400 chars each) full rows made every decision prompt ~20k tokens and
    hosted calls timed out at 60s on 2026-09-28; leads are still extracted from the full rows.
    """
    return [{**row, 'content': row['content'][:DIGEST_CHARS] + ('…' if len(row['content']) > DIGEST_CHARS else '')}
            for row in rows]


QUOTED_NAME = re.compile(r"['‘\"“「『]([^'’\"”」』\n]{2,40})['’\"”」』]")


def unsearched_leads(rows, history):
    """Names quoted in the visible records (e.g. "'결재 규정' 문서를 확인하세요") that no query has covered.

    They are offered to the model as follow-up candidates; the model still decides whether to search.
    """
    searched = ' '.join(h['query'] for h in history)
    leads = []
    for row in rows:
        for name in QUOTED_NAME.findall(row['content']):
            name = name.strip()
            if name and name not in searched and name not in leads:
                leads.append(name)
    return leads[:8]


def fair_order(registry, origin):
    """Interleave records by the search that found them, so a broad first search cannot hide later ones."""
    groups = {}
    for key in registry:
        groups.setdefault(origin.get(key, 0), []).append(key)
    ordered = []
    for rank in range(max((len(keys) for keys in groups.values()), default=0)):
        ordered += [keys[rank] for keys in groups.values() if rank < len(keys)]
    return ordered


class Orchestrator:
    def __init__(self, provider=None, model=None, trace_dir=None):
        self.provider = provider or make_provider()
        self.model = model or make_model()
        self.trace_dir = Path(trace_dir) if trace_dir else ROOT / '.runtime' / 'traces'

    async def run(self, request):
        run_id = str(uuid.uuid4())
        trace = {'runId': run_id, 'mode': request.mode, 'modelMode': self.model.mode, 'events': []}
        try:
            name = 'HANDOFF_ASK_TIMEOUT_SECONDS' if request.mode == 'ask' else 'HANDOFF_GENERATE_TIMEOUT_SECONDS'
            deadline = float(os.getenv(name, str(BUDGETS[request.mode]['deadline'])))
            async with asyncio.timeout(deadline):
                return await self._run(request, trace, deadline)
        except TimeoutError as exc:
            trace['events'].append({'event': 'stop', 'reason': 'deadline'})
            raise AgentError('AGENT_TIMEOUT', '처리 시간 한도를 넘었습니다. 검색 범위를 줄여 다시 시도하세요.', 504) from exc
        except AgentError as exc:
            trace['events'].append({'event': 'error', 'code': exc.code, 'message': exc.message})
            raise
        finally:
            # Counts and decision categories only: no document text, JWTs, model reasoning or user question.
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / (run_id + '.json')).write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')

    def pack(self, registry, offsets=None, origin=None):
        rows, visible, truncated = [], {}, False
        budget = max(1000, self.model.input_budget())
        # Fair per-record allowance, so a long Notion page cannot crowd out a short conflicting thread.
        allowance = max(100, min(3000, budget // max(1, min(len(registry), VISIBLE_RECORDS))))
        for key in fair_order(registry, origin or {})[:VISIBLE_RECORDS]:
            record = registry[key]
            offset = (offsets or {}).get(key, 0)
            content = record.content[offset:]
            low, high = 0, len(content)
            while low < high:
                mid = (low + high + 1) // 2
                if self.model.count_tokens(content[:mid]) <= allowance:
                    low = mid
                else:
                    high = mid - 1
            if low < len(content):
                boundary = content.rfind('\n', 0, low)
                if boundary > low // 2:
                    low = boundary + 1
            text = content[:low]
            row = {'recordKey': key, 'sourceId': record.sourceId, 'content': text,
                   'updatedAt': record.updatedAt, 'retrievedAt': record.retrievedAt,
                   'extractionStatus': record.extractionStatus, 'hasMore': low < len(content)}
            if self.model.count_tokens(json.dumps(rows + [row], ensure_ascii=False)) > budget:
                truncated = True
                break
            if text:
                rows.append(row)
                visible[key] = record.model_copy(update={'content': text})
            truncated |= offset > 0 or low < len(content)
        return rows, visible, truncated or len(registry) > VISIBLE_RECORDS

    def _drain_model(self, trace):
        drain = getattr(self.model, 'drain_events', None)
        if drain:
            trace['events'].extend(drain())

    async def _run(self, request, trace, deadline):
        registry, warnings, history, seen, offsets, origin = {}, [], [], set(), {}, {}
        cursor, last_query, last_sources = None, '', []
        budget = BUDGETS[request.mode]
        limit, memory_limit, candidate_limit = budget['searches'], budget['memory'], budget['candidates']
        cutoff = search_cutoff(request.mode, deadline)
        search_count, memory_count = 0, 0
        started = time.monotonic()
        state = {'mode': request.mode, 'question': request.question, 'role': request.role,
                 'context': request.context, 'allowedSources': request.scope.sources}
        for step in range(limit + memory_limit + 1):
            rows, visible, truncated = self.pack(registry, offsets, origin)
            state.update(records=digest(rows), history=history, nextCursor=cursor, searchesRemaining=limit - search_count,
                         unsearchedLeads=unsearched_leads(rows, history))
            if search_count >= limit or time.monotonic() - started > cutoff:
                warnings.append('추가 검색 한도에 도달했습니다. 현재 확보한 근거만 사용합니다.')
                trace['events'].append({'event': 'stop', 'reason': 'search_budget'})
                break
            decision = await self.model.decide(state)
            self._drain_model(trace)
            # An empty initial state cannot support a grounded synthesis. Some
            # reasoning models conservatively choose ``finish`` before seeing
            # any records, so the orchestrator guarantees one bounded search
            # within the caller's allowed source scope before accepting finish.
            if decision.action == 'finish' and not history and not registry:
                decision = Decision(action='search', query=request.question or '*',
                                    sources=list(request.scope.sources),
                                    reason='초기 근거를 조회한 뒤 합성합니다.')
                trace['events'].append({'event': 'policy', 'reason': 'initial_search_required'})
            if decision.action == 'finish':
                trace['events'].append({'event': 'stop', 'reason': 'finish'})
                break
            if decision.action == 'read_more':
                row = next((r for r in rows if r['recordKey'] == decision.recordKey and r['hasMore']), None)
                if not row or memory_count >= memory_limit:
                    warnings.append('추가 문맥 검토 한도에 도달했거나 해당 구간이 없습니다.')
                    break
                offsets[decision.recordKey] = offsets.get(decision.recordKey, 0) + len(row['content'])
                memory_count += 1
                trace['events'].append({'event': 'read_memory', 'window': memory_count})
                continue
            if decision.action == 'next_page':
                if not cursor:
                    warnings.append('유효한 다음 페이지가 없어 검색을 종료했습니다.')
                    break
                query, sources, next_cursor = last_query, last_sources, cursor
            else:
                query, sources, next_cursor = decision.query.strip(), list(dict.fromkeys(decision.sources)), None
                if query == '*' and not getattr(self.provider, 'supports_wildcard', False):
                    # A real index would search for a literal asterisk and return unrelated pages.
                    query = broad_query(request)
                    trace['events'].append({'event': 'policy', 'reason': 'wildcard_replaced'})
            if not query or not sources or not set(sources).issubset(request.scope.sources):
                raise AgentError('SCOPE_DENIED', '허용 범위 밖이거나 유효하지 않은 검색을 차단했습니다.', 403)
            signature = (' '.join(query.split()).lower(), tuple(sorted(sources)), next_cursor)
            # The same query on a subset of already-searched sources cannot find anything new.
            if signature in seen or (next_cursor is None and any(
                    q == signature[0] and c is None and set(signature[1]) <= set(s) for q, s, c in seen)):
                warnings.append('같은 검색의 반복을 차단했습니다.')
                trace['events'].append({'event': 'stop', 'reason': 'repeated_search'})
                break
            seen.add(signature)
            rid = str(uuid.uuid4())
            result = await self.provider.search(query, sources, next_cursor, request.scope, rid)
            search_count += 1
            drain_events = getattr(self.provider, 'drain_events', None)
            if drain_events:
                trace['events'].extend(drain_events())
            result = RetrievalResponse.model_validate(result)
            if result.requestId != rid:
                raise AgentError('RETRIEVAL_INVALID', '검색 응답 requestId가 일치하지 않습니다.')
            if result.status == 'failed':
                raise AgentError('RETRIEVAL_FAILED', '자료 검색에 실패했습니다. 자료가 없는 상태와 구분해 다시 시도하세요.')
            if result.status == 'empty' and result.records:
                raise AgentError('RETRIEVAL_INVALID', 'empty 응답에 자료가 포함되어 있습니다.')
            coverage = {c.source: c for c in result.coverage}
            if len(coverage) != len(result.coverage) or set(coverage) != set(sources):
                raise AgentError('RETRIEVAL_INVALID', '요청한 소스와 coverage가 일치하지 않습니다.')
            for source in sources:
                if coverage[source].recordCount != sum(provider_of(r) == source for r in result.records):
                    raise AgentError('RETRIEVAL_INVALID', 'coverage.recordCount는 이번 응답 records 수와 같아야 합니다.')
            if result.status == 'partial' or result.errors or any(c.status != 'searched' for c in result.coverage):
                warnings.append('일부 자료를 조회하지 못했습니다. 검색 범위의 공백을 고려하세요.')
            accepted, new_records = 0, 0
            for record in result.records:
                if provider_of(record) not in sources:
                    raise AgentError('SCOPE_DENIED', '범위 밖 자료를 검색 응답에서 차단했습니다.', 403)
                if record.accessStatus != 'accessible' or record.extractionStatus == 'failed' or not safe_url(record.url):
                    warnings.append('접근·파싱·링크를 검증할 수 없는 자료를 제외했습니다.')
                    continue
                if INJECTION.search(record.content):
                    trace['events'].append({'event': 'security', 'code': 'UNTRUSTED_INSTRUCTION_QUARANTINED'})
                    warnings.append('외부 전송 또는 지시 무시를 요구한 자료를 격리했습니다.')
                    continue
                if record.extractionStatus == 'partial':
                    warnings.append('일부만 추출된 자료가 포함되어 있습니다.')
                key = hashlib.sha256((record.sourceId + '\0' + record.content).encode()).hexdigest()[:24]
                if key not in registry and len(registry) >= candidate_limit:
                    warnings.append('원문 후보 수 한도로 일부 자료를 검토하지 못했습니다.')
                    continue
                new_records += key not in registry
                registry[key] = record  # Different chunks sharing a sourceId remain distinct.
                origin.setdefault(key, search_count)
                accepted += 1
            # The model sees its earlier queries, and whether they found anything new, so it can follow
            # leads instead of rephrasing. The trace below records sources and counts only, never the query.
            history.append({'query': query, 'sources': sources, 'status': result.status, 'recordCount': accepted,
                            'newRecords': new_records, 'coverage': [c.model_dump() for c in result.coverage]})
            trace['events'].append({'event': 'retrieve', 'sources': sources, 'status': result.status, 'accepted': accepted})
            cursor, last_query, last_sources = result.nextCursor, query, sources
        rows, visible, truncated = self.pack(registry, offsets, origin)
        if cursor:
            warnings.append('추가 검색 결과가 남아 있습니다. 전체 자료를 검토한 답변이 아닙니다.')
        if truncated:
            warnings.append('문맥 예산으로 원문의 일부만 검토했습니다. 질문을 좁히면 관련 부분을 더 조회할 수 있습니다.')
        state.update(records=rows, history=history, warnings=warnings)
        best = None
        for attempt in range(2):
            # One unverifiable quote removes only that item. Another synthesis round (30-60s on a hosted
            # model) is spent only when nothing verifiable is left.
            candidate, dropped = repair_synthesis(await self.model.synthesize(state), visible, request.role)
            validate_synthesis(candidate, visible, request.role)
            if best is None or extracted(candidate) > extracted(best[0]):
                best = (candidate, dropped)
            if not dropped or attempt or extracted(candidate):
                break
            state['validationFeedback'] = 'Remove unsupported fields and quotes. Use exact visible quotes only.'
            trace['events'].append({'event': 'repair', 'code': 'EVIDENCE_VALIDATION', 'dropped': dropped})
        self._drain_model(trace)
        result, dropped = best
        if dropped:
            warnings.append(f'원문과 일치하지 않는 인용·필드 {dropped}건을 제외했습니다.')
        if not extracted(result):
            warnings.append('조회한 자료에서 이 요청과 관련된 근거를 찾지 못했습니다. 자료 공유 범위나 검색 범위를 확인하세요.')
            if not result.gaps:
                result.gaps.append(Gap(question='관련 원문이나 담당자를 알려주실 수 있나요?',
                                       whyItMatters='현재 조회한 자료만으로 확인할 수 없습니다.'))
        generated_at = datetime.now(timezone.utc).isoformat()
        output = project(result, visible, request.mode, request.context, warnings, self.model.mode, generated_at)
        trace['events'].append({'event': 'validated', 'records': len(visible), 'facts': len(result.facts),
                                'tasks': len(result.tasks), 'dropped': dropped})
        return {'result': output, 'runId': trace['runId'], 'modelMode': self.model.mode, 'generatedAt': generated_at}
