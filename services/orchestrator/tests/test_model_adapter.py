import asyncio
import json
import httpx
import pytest
from handoff.models import NemotronModel
from handoff.contracts import Decision, AgentError

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
