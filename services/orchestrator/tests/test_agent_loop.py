"""Multi-step search behaviour: following leads, keeping later evidence visible, and time budgeting."""
import asyncio
import json

from handoff.contracts import Decision
from handoff.engine import Orchestrator
from handoff.models import OfflineModel
from handoff.providers import McpProvider
from test_engine import Provider, records, request
from test_mcp_provider import payload


def trace_events(tmp_path):
    return json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))['events']


class LeadFollower(OfflineModel):
    """Keeps searching new keywords, recording what the loop tells it about earlier searches."""

    QUERIES = ['행사비 정산', '결재 규정', '법인 계좌', '가을 행사', '정산 스프레드시트', '벤더 견적']

    def __init__(self):
        super().__init__()
        self.seen = []

    async def decide(self, state):
        self.seen.append(([h['query'] for h in state['history']], state['searchesRemaining']))
        return Decision(action='search', query=self.QUERIES[len(state['history'])], sources=['notion'], reason='follow lead')


def test_ask_can_follow_three_leads_after_the_first_search(tmp_path):
    model = LeadFollower()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([records()[1]]), model=model)
                .run(request('ask', '행사비 정산 기준은?')))
    events = trace_events(tmp_path)
    assert [e['event'] for e in events].count('retrieve') == 4
    assert any(e.get('reason') == 'search_budget' for e in events)
    assert model.seen[3] == (['행사비 정산', '결재 규정', '법인 계좌'], 1)
    assert '결재 규정' not in json.dumps(events, ensure_ascii=False)


class ByQuery(Provider):
    def __init__(self, table):
        super().__init__()
        self.table = table

    async def search(self, query, sources, cursor, scope, request_id):
        self.values = self.table[query]
        return await super().search(query, sources, cursor, scope, request_id)


class BroadThenNarrow(OfflineModel):
    async def decide(self, state):
        if len(state['history']) < 2:
            return Decision(action='search', query=['넓은 검색', '좁은 검색'][len(state['history'])], sources=['notion'], reason='x')
        return Decision(action='finish', query='', sources=[], reason='done')

    async def synthesize(self, state):
        self.visible = [row['content'] for row in state['records']]
        return await super().synthesize(state)


def test_follow_up_results_stay_visible_after_a_broad_first_search(tmp_path):
    base = records()[1]
    broad = [base.model_copy(update={'sourceId': f'notion:wide:{i}', 'content': f'넓은 자료 {i}'}) for i in range(30)]
    narrow = [base.model_copy(update={'sourceId': 'notion:rule:0', 'content': '결재 규정: 100만 원 미만은 운영팀장이 결재합니다.'})]
    model = BroadThenNarrow()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=ByQuery({'넓은 검색': broad, '좁은 검색': narrow}), model=model)
                .run(request('ask', '결재 기준은?')))
    assert '결재 규정: 100만 원 미만은 운영팀장이 결재합니다.' in model.visible


class SlowProvider(Provider):
    async def search(self, query, sources, cursor, scope, request_id):
        await asyncio.sleep(0.6)
        return await super().search(query, sources, cursor, scope, request_id)


class EndlessSearcher(OfflineModel):
    async def decide(self, state):
        return Decision(action='search', query=f'검색 {len(state["history"])}', sources=['notion'], reason='more')


def test_searching_stops_early_enough_to_synthesize_before_the_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv('HANDOFF_GENERATE_TIMEOUT_SECONDS', '2')
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=SlowProvider([records()[1]]), model=EndlessSearcher())
                         .run(request()))
    events = trace_events(tmp_path)
    assert output['runId']
    assert [e['event'] for e in events].count('retrieve') == 2
    assert any(e.get('reason') == 'search_budget' for e in events)


def test_fetch_context_keeps_the_blocks_around_the_hit_not_the_whole_page():
    page = [records()[1].model_copy(update={'sourceId': f'notion:page:b{i}', 'content': f'블록 {i}',
                                            'contentOrigin': 'source_excerpt'}) for i in range(30)]

    async def call_tool(name, arguments):
        if name == 'search_evidence':
            return payload([page[20]], sources=('notion',))
        return payload(page, sources=('notion',))

    provider = McpProvider('http://127.0.0.1:8000/mcp', call_tool=call_tool)
    result = asyncio.run(provider.search('블록', ['notion'], None, request().scope, 'orch-request-id'))
    ids = [record.sourceId for record in result.records]
    assert 'notion:page:b20' in ids and 'notion:page:b19' in ids and 'notion:page:b21' in ids
    assert len(ids) <= 12 and 'notion:page:b0' not in ids
    assert result.coverage[0].recordCount == len(ids)


class TwoQueries(OfflineModel):
    def __init__(self):
        super().__init__()
        self.history = None

    async def decide(self, state):
        if len(state['history']) < 2:
            return Decision(action='search', query=f'검색 {len(state["history"])}', sources=['notion'], reason='x')
        self.history = state['history']
        return Decision(action='finish', query='', sources=[], reason='done')


def test_model_is_told_when_a_search_found_nothing_new(tmp_path):
    model = TwoQueries()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([records()[1]]), model=model).run(request('ask', '기준은?')))
    assert [h['newRecords'] for h in model.history] == [1, 0]


class LeadWatcher(OfflineModel):
    def __init__(self):
        super().__init__()
        self.leads = []

    async def decide(self, state):
        self.leads.append(state['unsearchedLeads'])
        queries = ['행사비 정산', '결재 규정']
        if len(state['history']) < len(queries):
            return Decision(action='search', query=queries[len(state['history'])], sources=['notion'], reason='x')
        return Decision(action='finish', query='', sources=[], reason='done')


def test_quoted_document_names_are_offered_as_follow_up_leads_until_searched(tmp_path):
    row = records()[1].model_copy(update={'content': "정산 승인 기준은 '결재 규정' 문서를 확인하세요."})
    model = LeadWatcher()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([row]), model=model).run(request('ask', '승인은?')))
    assert model.leads == [[], ['결재 규정'], []]


class ContextProbe(OfflineModel):
    async def decide(self, state):
        self.decide_lengths = [len(row['content']) for row in state['records']]
        return await super().decide(state)

    async def synthesize(self, state):
        self.synthesis_lengths = [len(row['content']) for row in state['records']]
        return await super().synthesize(state)


def test_decisions_see_a_short_digest_while_synthesis_sees_the_full_text(tmp_path):
    row = records()[1].model_copy(update={'content': "원문 앞부분. '결재 규정' 문서를 보세요.\n" + '세부 내용이 이어집니다. ' * 200})
    model = ContextProbe()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([row]), model=model).run(request('ask', '규정은?')))
    assert model.decide_lengths and max(model.decide_lengths) <= 401
    assert max(model.synthesis_lengths) > 1000


def test_packed_evidence_leaves_room_for_the_synthesis_request(tmp_path, monkeypatch):
    import httpx
    from test_model_adapter import model as nemotron_stub
    titles = []

    def handler(request):
        schema = json.loads(request.content)['guided_json']['title']
        titles.append(schema)
        data = ({'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'} if schema == 'Decision'
                else {'facts': [], 'tasks': [], 'conflicts': [], 'gaps': [], 'suggestions': []})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    model = nemotron_stub()
    model.count_tokens = len  # about one token per Korean character, like the real tokenizer
    model.timeout, model.synthesis_timeout = 60, 150
    rows = [records()[1].model_copy(update={'sourceId': f'drive:file{i}:chunk:0', 'sourceType': 'drive_document',
                                            'content': f'{i}번 규정 조각. ' + '회비와 예산 관리 기준을 설명합니다. ' * 70})
            for i in range(30)]
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider(rows), model=model).run(request('ask', '규정은?')))
    assert output['runId'] and 'Synthesis' in titles


class NarrowsSameQuery(OfflineModel):
    async def decide(self, state):
        sources = ['notion', 'drive'] if not state['history'] else ['drive']
        return Decision(action='search', query='총무  규정' if state['history'] else '총무 규정', sources=sources, reason='x')


def test_same_query_on_fewer_sources_counts_as_a_repeat(tmp_path):
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([records()[1]]), model=NarrowsSameQuery())
                .run(request('ask', '총무?')))
    events = trace_events(tmp_path)
    assert [e['event'] for e in events].count('retrieve') == 1
    assert any(e.get('reason') == 'repeated_search' for e in events)
