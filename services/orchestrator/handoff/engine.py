"""Bounded, read-only agent loop shared by generate, ask and the optional NAT adapter."""
import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from .contracts import AgentError, RetrievalResponse
from .models import make_model
from .providers import ROOT, make_provider, provider_of, safe_url
from .projection import project, validate_synthesis

INJECTION = re.compile(r'ignore.{0,40}(?:instruction|previous)|시스템.{0,30}무시|외부.{0,30}전송|upload.{0,30}secret', re.I)


class Orchestrator:
    def __init__(self, provider=None, model=None, trace_dir=None):
        self.provider = provider or make_provider()
        self.model = model or make_model()
        self.trace_dir = trace_dir or ROOT / '.runtime' / 'traces'

    async def run(self, request):
        run_id = str(uuid.uuid4())
        trace = {'runId': run_id, 'mode': request.mode, 'modelMode': self.model.mode, 'events': []}
        try:
            async with asyncio.timeout(42 if request.mode == 'ask' else 170):
                return await self._run(request, trace)
        except TimeoutError as exc:
            trace['events'].append({'event': 'stop', 'reason': 'deadline'})
            raise AgentError('AGENT_TIMEOUT', '처리 시간 한도를 넘었습니다. 검색 범위를 줄여 다시 시도하세요.', 504) from exc
        except AgentError as exc:
            trace['events'].append({'event': 'error', 'code': exc.code})
            raise
        finally:
            # Counts and decision categories only: no document text, JWTs, model reasoning or user question.
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / (run_id + '.json')).write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')

    def pack(self, registry, offsets=None):
        rows, visible, truncated = [], {}, False
        budget = max(1000, self.model.context_limit - 10000)
        # Fair per-record allowance, so a long Notion page cannot crowd out a short conflicting thread.
        allowance = max(100, min(3000, budget // max(1, min(len(registry), 24))))
        for key, record in list(registry.items())[:24]:
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
        return rows, visible, truncated or len(registry) > 24

    async def _run(self, request, trace):
        registry, warnings, history, seen, offsets = {}, [], [], set(), {}
        cursor, last_query, last_sources = None, '', []
        limit = 2 if request.mode == 'ask' else 7
        memory_limit = 1 if request.mode == 'ask' else 2
        search_count, memory_count = 0, 0
        started = time.monotonic()
        candidate_limit = 16 if request.mode == 'ask' else 40
        state = {'mode': request.mode, 'question': request.question, 'role': request.role,
                 'context': request.context, 'allowedSources': request.scope.sources}
        for step in range(limit + memory_limit + 1):
            rows, visible, truncated = self.pack(registry, offsets)
            state.update(records=rows, history=history, nextCursor=cursor)
            if search_count >= limit or time.monotonic() - started > (34 if request.mode == 'ask' else 150):
                warnings.append('추가 검색 한도에 도달했습니다. 현재 확보한 근거만 사용합니다.')
                trace['events'].append({'event': 'stop', 'reason': 'search_budget'})
                break
            decision = await self.model.decide(state)
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
            if not query or not sources or not set(sources).issubset(request.scope.sources):
                raise AgentError('SCOPE_DENIED', '허용 범위 밖이거나 유효하지 않은 검색을 차단했습니다.', 403)
            signature = (query, tuple(sorted(sources)), next_cursor)
            if signature in seen:
                warnings.append('같은 검색의 반복을 차단했습니다.')
                trace['events'].append({'event': 'stop', 'reason': 'repeated_search'})
                break
            seen.add(signature)
            rid = str(uuid.uuid4())
            result = await self.provider.search(query, sources, next_cursor, request.scope, rid)
            search_count += 1
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
            accepted = 0
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
                registry[key] = record  # Different chunks sharing a sourceId remain distinct.
                accepted += 1
            history.append({'sources': sources, 'status': result.status, 'recordCount': accepted,
                            'coverage': [c.model_dump() for c in result.coverage]})
            trace['events'].append({'event': 'retrieve', 'sources': sources, 'status': result.status, 'accepted': accepted})
            cursor, last_query, last_sources = result.nextCursor, query, sources
        rows, visible, truncated = self.pack(registry, offsets)
        if cursor:
            warnings.append('추가 검색 결과가 남아 있습니다. 전체 자료를 검토한 답변이 아닙니다.')
        if truncated:
            warnings.append('문맥 예산으로 원문의 일부만 검토했습니다. 질문을 좁히면 관련 부분을 더 조회할 수 있습니다.')
        state.update(records=rows, history=history, warnings=warnings)
        for attempt in range(2):
            result = await self.model.synthesize(state)
            try:
                validate_synthesis(result, visible, request.role)
                break
            except AgentError:
                if attempt:
                    raise
                state['validationFeedback'] = 'Remove unsupported fields and quotes. Use exact visible quotes only.'
                trace['events'].append({'event': 'repair', 'code': 'EVIDENCE_VALIDATION'})
        generated_at = datetime.now(timezone.utc).isoformat()
        output = project(result, visible, request.mode, request.context, warnings, self.model.mode, generated_at)
        trace['events'].append({'event': 'validated', 'records': len(visible), 'facts': len(result.facts), 'tasks': len(result.tasks)})
        return {'result': output, 'runId': trace['runId'], 'modelMode': self.model.mode, 'generatedAt': generated_at}
