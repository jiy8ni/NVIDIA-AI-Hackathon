import asyncio
import json
import httpx
import pytest
from handoff.models import NemotronModel
from handoff.contracts import Decision, AgentError

@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr('handoff.models.RETRY_BACKOFF_SECONDS', (0, 0))


def model():
    result = object.__new__(NemotronModel)
    result.key, result.model, result.base = 'test-key', 'test-nemotron', 'https://model.example.test/v1'
    result.context_limit = 32768
    result.count_tokens = lambda text: len(text.encode())
    return result

def test_nemotron_json_contract_and_schema_repair(monkeypatch):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        data = 'not json' if len(seen) == 1 else json.dumps({'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': data}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(model().complete('instruction', {'content': 'untrusted'}, Decision, 800))
    assert result.action == 'finish' and len(seen) == 2
    assert seen[0]['messages'][0]['role'] == 'system'
    assert 'untrusted' in seen[0]['messages'][1]['content']
    assert seen[0]['response_format'] == {'type': 'json_object'}
    assert seen[0]['chat_template_kwargs'] == {'enable_thinking': False}
    assert seen[0]['guided_json']['title'] == 'Decision'

def test_nemotron_http_failure_is_not_offline_answer(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(lambda req: httpx.Response(503)), **kwargs))
    with pytest.raises(AgentError) as error:
        asyncio.run(model().complete('instruction', {}, Decision, 800))
    assert error.value.code == 'MODEL_UNAVAILABLE'

@pytest.mark.parametrize('first_failure', ['timeout', 503, 429])
def test_one_transient_model_failure_is_retried(monkeypatch, first_failure):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            if first_failure == 'timeout':
                raise httpx.ReadTimeout('hosted model queue', request=request)
            return httpx.Response(first_failure)
        data = json.dumps({'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': data}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(model().complete('instruction', {}, Decision, 800))
    assert result.action == 'finish' and len(calls) == 2
    assert len(calls[1]['messages']) == 2  # a transport retry is not a schema-repair turn

def test_model_failure_after_the_retry_is_reported(monkeypatch):
    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout('hosted model queue', request=request)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AgentError) as error:
        asyncio.run(model().complete('instruction', {}, Decision, 800))
    assert error.value.code == 'MODEL_UNAVAILABLE' and len(calls) == 2

def test_synthesis_waits_longer_than_a_routing_decision(monkeypatch):
    timeouts = {}
    def handler(request):
        body = json.loads(request.content)
        if body['guided_json']['title'] == 'Decision':
            data = {'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'}
        else:
            data = {'facts': [], 'tasks': [], 'conflicts': [], 'gaps': [], 'suggestions': []}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]})
    real = httpx.AsyncClient
    def client(**kwargs):
        timeouts.setdefault('calls', []).append(kwargs['timeout'])
        return real(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    m = model()
    m.timeout, m.synthesis_timeout = 60, 150
    asyncio.run(m.decide({}))
    asyncio.run(m.synthesize({}))
    decide_timeout, synthesis_timeout = timeouts['calls']
    assert synthesis_timeout > decide_timeout


def test_busy_model_is_retried_with_backoff_until_it_answers(monkeypatch):
    calls = []
    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        data = json.dumps({'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': data}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(model().complete('instruction', {}, Decision, 800)).action == 'finish'
    assert len(calls) == 3

def test_busy_primary_model_falls_back_to_the_configured_nemotron_model(monkeypatch):
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body['model'])
        if body['model'] == 'primary-nemotron':
            return httpx.Response(503)
        data = json.dumps({'action': 'finish', 'query': '', 'sources': [], 'reason': 'enough'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': data}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    m = model()
    m.model, m.fallback_model = 'primary-nemotron', 'fallback-nemotron'
    assert asyncio.run(m.complete('instruction', {}, Decision, 800)).action == 'finish'
    assert seen == ['primary-nemotron'] * 3 + ['fallback-nemotron']
    assert m.drain_events() == [{'event': 'model_fallback', 'reason': 'busy'}]
