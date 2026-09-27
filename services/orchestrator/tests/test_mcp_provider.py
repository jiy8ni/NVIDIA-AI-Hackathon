import asyncio
import json

import pytest

from handoff.contracts import AgentError
from handoff.engine import Orchestrator
from handoff.providers import McpProvider, make_provider
from test_engine import records, request


def payload(values, *, status='ok', coverage_status='searched', errors=None, sources=('notion', 'slack')):
    return {
        # Retrieval MCP owns this protocol-level ID. McpProvider replaces it
        # only after the tool call has completed on the same MCP session.
        'requestId': 'retrieval-owned-id',
        'status': status,
        'records': [value.model_dump() for value in values],
        'nextCursor': None,
        'coverage': [
            {'source': source, 'status': coverage_status,
             'recordCount': sum(value.sourceId.startswith(source + ':') for value in values)}
            for source in sources
        ],
        'errors': errors or [],
    }


def test_mcp_search_calls_search_evidence_and_expands_excerpt(tmp_path):
    excerpt = records()[1].model_copy(update={'content': '행사비 정산에는 모임통장을 사용합니다.', 'contentOrigin': 'source_excerpt'})
    full = excerpt.model_copy(update={'content': '행사비 정산에는 모임통장을 사용합니다. 승인 기준은 별도 확인이 필요합니다.',
                                      'contentOrigin': 'source_full_text'})
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        if name == 'search_evidence':
            return payload([excerpt])
        assert name == 'fetch_context'
        return payload([full])

    provider = McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool)
    result = asyncio.run(provider.search('현재 계좌 기준', ['notion', 'slack'], None, request().scope, 'orch-request-id'))

    assert [name for name, _ in calls] == ['search_evidence', 'fetch_context']
    assert calls[0][1] == {'query': '현재 계좌 기준', 'sourceScope': ['notion', 'slack'], 'cursor': None}
    assert calls[1][1] == {'sourceId': 'notion:account'}
    assert result.requestId == 'orch-request-id'
    assert result.records == [full]
    assert [(item.source, item.recordCount) for item in result.coverage] == [('notion', 1), ('slack', 0)]
    assert [event['tool'] for event in provider.drain_events()] == ['search_evidence', 'fetch_context']


def test_mcp_keeps_partial_status_while_adapting_coverage_enum():
    row = records()[0]

    async def call_tool(name, arguments):
        return payload([row], status='partial', coverage_status='partial',
                       errors=[{'source': 'notion', 'code': 'parse_failed', 'message': 'safe message'}])

    result = asyncio.run(McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool).search(
        '조직', ['notion', 'slack'], None, request().scope, 'request-id'))

    assert result.status == 'partial'
    assert all(item.status == 'searched' for item in result.coverage)
    assert result.errors[0].code == 'parse_failed'


def test_mcp_rejects_partial_coverage_without_partial_status_or_error():
    row = records()[0]

    async def call_tool(name, arguments):
        return payload([row], status='ok', coverage_status='partial')

    with pytest.raises(AgentError) as error:
        asyncio.run(McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool).search(
            '조직', ['notion', 'slack'], None, request().scope, 'request-id'))
    assert error.value.code == 'RETRIEVAL_INVALID'


def test_mcp_fetch_failure_preserves_excerpt_as_partial():
    row = records()[0].model_copy(update={'contentOrigin': 'source_excerpt'})

    async def call_tool(name, arguments):
        if name == 'search_evidence':
            return payload([row], coverage_status='searched')
        raise AgentError('MCP_UNAVAILABLE', 'hidden upstream detail', 502)

    result = asyncio.run(McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool).search(
        '조직', ['notion', 'slack'], None, request().scope, 'request-id'))

    assert result.status == 'partial'
    assert result.records == [row]
    assert result.errors[-1].code == 'context_unavailable'


def test_mcp_rejects_out_of_scope_record():
    row = records()[2]

    async def call_tool(name, arguments):
        return payload([row], sources=('notion',))

    with pytest.raises(AgentError) as error:
        asyncio.run(McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool).search(
            '계좌', ['notion'], None, request().scope, 'request-id'))
    assert error.value.code == 'SCOPE_DENIED'


def test_engine_trace_records_mcp_tools_without_query_or_content(tmp_path):
    row = records()[0]

    async def call_tool(name, arguments):
        return payload([row], coverage_status='searched')

    provider = McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool)
    req = request('ask', '조직은 무엇인가요?')
    req.scope.sources = ['notion', 'slack']
    asyncio.run(Orchestrator(provider=provider, trace_dir=tmp_path).run(req))
    trace = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    tools = [item['tool'] for item in trace['events'] if item['event'] == 'mcp_tool']
    assert tools == ['search_evidence']
    assert '조직은 무엇인가요' not in json.dumps(trace, ensure_ascii=False)
    assert row.content not in json.dumps(trace, ensure_ascii=False)


def test_mcp_configuration_requires_safe_streamable_http_url(monkeypatch):
    monkeypatch.setenv('HANDOFF_RETRIEVAL_MODE', 'mcp')
    monkeypatch.setenv('RETRIEVAL_MCP_URL', 'http://retrieval.example.com/mcp')
    with pytest.raises(ValueError):
        make_provider()
    monkeypatch.setenv('RETRIEVAL_MCP_URL', 'http://127.0.0.1:8000/mcp')
    assert isinstance(make_provider(), McpProvider)
