"""Regressions found by running Nemotron against a real Notion workspace (2026-09-28)."""
import asyncio
import json

import pytest

from handoff.contracts import Synthesis
from handoff.engine import Orchestrator
from handoff.models import OfflineModel
from handoff.projection import project, repair_synthesis, validate_synthesis
from test_engine import FinishFirst, Provider, records, request

SOURCE = '행사비는 "법인 계좌"로 정산합니다. 영수증은 금요일까지 올립니다.'
EMPTY = {'value': None, 'evidence': []}


def registry(content=SOURCE):
    return {'r': records()[1].model_copy(update={'content': content})}


def facts(*quotes):
    return Synthesis(facts=[{'recordKey': 'r', 'quote': q, 'sectionId': 'the-job'} for q in quotes],
                     tasks=[], conflicts=[], gaps=[], suggestions=[])


def grounded(value, quote):
    return {'value': value, 'evidence': [{'recordKey': 'r', 'quote': quote}]}


def task(title, owner=EMPTY):
    return Synthesis(facts=[], tasks=[{'title': title, 'objective': EMPTY, 'ownerName': owner, 'dueText': EMPTY,
                                       'nextAction': EMPTY, 'definitionOfDone': [], 'steps': []}],
                     conflicts=[], gaps=[], suggestions=[])


@pytest.mark.parametrize('model_quote', [
    "행사비는 '법인 계좌'로 정산합니다.",
    '행사비는 법인 계좌로 정산합니다.',
    '행사비는  "법인 계좌"로\n정산합니다.',
])
def test_quote_mark_and_whitespace_variants_restore_exact_source_text(model_quote):
    cleaned, dropped = repair_synthesis(facts(model_quote), registry())
    assert dropped == 0
    assert [f.quote for f in cleaned.facts] == ['행사비는 "법인 계좌"로 정산합니다.']
    validate_synthesis(cleaned, registry())


def test_changed_words_are_not_accepted_as_a_quote():
    cleaned, dropped = repair_synthesis(facts('행사비는 "개인 계좌"로 정산합니다.'), registry())
    assert cleaned.facts == [] and dropped == 1


def test_one_unverifiable_fact_is_dropped_without_rejecting_the_rest():
    cleaned, dropped = repair_synthesis(facts('영수증은 금요일까지 올립니다.', '존재하지 않는 근거'), registry())
    assert [f.quote for f in cleaned.facts] == ['영수증은 금요일까지 올립니다.'] and dropped == 1


def test_unlabelled_owner_becomes_unknown_but_task_is_kept():
    content = '업무: 증빙 수집 | 작성자 대협이 정리함'
    result = task(grounded('증빙 수집', '업무: 증빙 수집'), grounded('대협', '작성자 대협이 정리함'))
    cleaned, dropped = repair_synthesis(result, registry(content))
    assert cleaned.tasks[0].title.value == '증빙 수집'
    assert cleaned.tasks[0].ownerName.value is None and cleaned.tasks[0].ownerName.evidence == []
    assert dropped == 1
    validate_synthesis(cleaned, registry(content))


def test_task_with_unverifiable_title_is_dropped():
    cleaned, dropped = repair_synthesis(task(grounded('없는 업무', '업무: 없는 업무')), registry('업무: 증빙 수집'))
    assert cleaned.tasks == [] and dropped == 1


def test_conflict_with_one_verifiable_side_becomes_a_confirmation_gap():
    result = Synthesis(facts=[], tasks=[], gaps=[], suggestions=[], conflicts=[{
        'topic': '계좌 기준', 'question': '현재 계좌 기준은 무엇인가요?',
        'alternatives': [{'recordKey': 'r', 'quote': '영수증은 금요일까지 올립니다.'},
                         {'recordKey': 'r', 'quote': '모임통장을 사용합니다.'}]}])
    cleaned, dropped = repair_synthesis(result, registry())
    assert cleaned.conflicts == [] and dropped == 1
    assert [g.question for g in cleaned.gaps] == ['현재 계좌 기준은 무엇인가요?']


class QuoteRewriter(OfflineModel):
    """Behaves like the live model: right record, but the source's double quotes are swapped."""

    async def synthesize(self, state):
        key = state['records'][0]['recordKey']
        return Synthesis(facts=[{'recordKey': key, 'quote': "행사비는 '법인 계좌'로 정산합니다.", 'sectionId': 'the-job'},
                                {'recordKey': key, 'quote': '원문에 없는 문장입니다.', 'sectionId': 'the-job'}],
                         tasks=[], conflicts=[], gaps=[], suggestions=[])


def test_ask_survives_a_rewritten_quote_and_reports_the_dropped_one(tmp_path):
    row = records()[1].model_copy(update={'content': SOURCE})
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([row]), model=QuoteRewriter())
                         .run(request('ask', '행사비는 어떻게 정산하나요?')))
    answer = output['result']['answer']
    assert '행사비는 "법인 계좌"로 정산합니다.' in answer
    assert '원문에 없는 문장' not in answer and '제외' in answer
    events = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))['events']
    # A verified quote remains, so the cleaned answer is used without another slow model round.
    assert [e['event'] for e in events].count('repair') == 0
    assert next(e for e in events if e['event'] == 'validated')['dropped'] == 1


class FabricatesOnce(OfflineModel):
    def __init__(self):
        super().__init__()
        self.rounds = 0

    async def synthesize(self, state):
        self.rounds += 1
        key = state['records'][0]['recordKey']
        quote = '원문에 없는 문장입니다.' if self.rounds == 1 else '영수증은 금요일까지 올립니다.'
        return Synthesis(facts=[{'recordKey': key, 'quote': quote, 'sectionId': 'the-job'}],
                         tasks=[], conflicts=[], gaps=[], suggestions=[])


def test_answer_with_nothing_verifiable_gets_exactly_one_more_synthesis_round(tmp_path):
    row = records()[1].model_copy(update={'content': SOURCE})
    model = FabricatesOnce()
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([row]), model=model)
                         .run(request('ask', '영수증은 언제까지 올리나요?')))
    assert model.rounds == 2
    assert '영수증은 금요일까지 올립니다.' in output['result']['answer']


class Recorder(Provider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queries = []

    async def search(self, query, sources, cursor, scope, request_id):
        self.queries.append(query)
        return await super().search(query, sources, cursor, scope, request_id)


@pytest.mark.parametrize('model', [FinishFirst(), OfflineModel()], ids=['forced-initial-search', 'offline-decision'])
def test_generate_never_sends_the_fixture_wildcard_to_real_providers(tmp_path, model):
    provider = Recorder()
    asyncio.run(Orchestrator(trace_dir=tmp_path, provider=provider, model=model).run(request()))
    assert provider.queries and '*' not in provider.queries
    assert all('운영 담당자' in query for query in provider.queries)


class NothingRelevant(OfflineModel):
    async def synthesize(self, state):
        return Synthesis(facts=[], tasks=[], conflicts=[], gaps=[], suggestions=[])


def test_empty_generation_is_marked_as_insufficient_evidence(tmp_path):
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([records()[0]]), model=NothingRelevant())
                         .run(request()))
    blocks = [b for s in output['result']['sections'] for b in s['blocks']]
    assert any(b['type'] == 'unknown-card' for b in blocks)
    assert any(b['type'] == 'callout' and '근거를 찾지 못했습니다' in b['body'] for b in blocks)


def test_empty_answer_offers_a_follow_up_question(tmp_path):
    output = asyncio.run(Orchestrator(trace_dir=tmp_path, provider=Provider([records()[0]]), model=NothingRelevant())
                         .run(request('ask', '행사비는 어떻게 정산하나요?')))
    assert output['result']['suggestedQuestions']
    assert '근거를 찾지 못했습니다' in output['result']['answer']


@pytest.mark.parametrize('placeholder', ['미정', '미확정', 'TBD'])
def test_placeholder_owner_and_due_become_unknown_with_a_question(placeholder):
    content = f'업무: 증빙 수집 | 담당자: {placeholder} | 기한: {placeholder}'
    result = task(grounded('증빙 수집', content), grounded(placeholder, content))
    result.tasks[0].dueText = result.tasks[0].ownerName.model_copy(deep=True)
    cleaned, _ = repair_synthesis(result, registry(content))
    assert cleaned.tasks[0].ownerName.value is None and cleaned.tasks[0].dueText.value is None
    out = project(cleaned, registry(content), 'generate', {}, [], 'nemotron', '2026-09-28T00:00:00Z')
    job = next(b['payload'] for s in out['sections'] for b in s['blocks'] if b['type'] == 'job')
    assert any('담당자' in q for q in job['unresolvedQuestions']) and any('기한' in q for q in job['unresolvedQuestions'])


def test_quote_cited_under_the_wrong_chunk_moves_to_the_record_that_contains_it():
    chunks = {'a': records()[1].model_copy(update={'content': '제1조 이 내규는 동아리 운영 기준을 정한다.'}),
              'b': records()[1].model_copy(update={'sourceId': 'drive:rules:chunk:3', 'content': '제33조 총무는 "회비"를 총괄적으로 관리한다.'})}
    result = Synthesis(facts=[{'recordKey': 'a', 'quote': "제33조 총무는 '회비'를 총괄적으로 관리한다.", 'sectionId': 'team-role'}],
                       tasks=[], conflicts=[], gaps=[], suggestions=[])
    cleaned, dropped = repair_synthesis(result, chunks)
    assert dropped == 0
    assert (cleaned.facts[0].recordKey, cleaned.facts[0].quote) == ('b', '제33조 총무는 "회비"를 총괄적으로 관리한다.')
    validate_synthesis(cleaned, chunks)


def test_quote_found_in_several_other_records_is_not_guessed_onto_one_of_them():
    template = '담당자: 미정 | 기한: 미정'
    records = {'cited': records_row('제1조 이 내규는 동아리 운영 기준을 정한다.', 'drive:rules:chunk:0'),
               'meeting-a': records_row('9월 회의 ' + template, 'notion:meeting-a:b1'),
               'meeting-b': records_row('10월 회의 ' + template, 'notion:meeting-b:b1')}
    result = Synthesis(facts=[{'recordKey': 'cited', 'quote': template, 'sectionId': 'the-job'}],
                       tasks=[], conflicts=[], gaps=[], suggestions=[])
    cleaned, dropped = repair_synthesis(result, records)
    assert cleaned.facts == [] and dropped == 1


def records_row(content, source_id):
    return records()[1].model_copy(update={'content': content, 'sourceId': source_id})
