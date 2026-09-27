import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import quote
import httpx
import pytest
import yaml
import jsonschema
from handoff.providers import ROOT


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def jwt(secret, uid='kim', exp=None):
    b64 = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
    head = b64({'alg': 'HS256', 'typ': 'JWT'})
    payload = b64({'sub': uid, 'teamId': 'atlas', 'iss': 'handoffos-local', 'aud': 'handoffos-api', 'exp': exp or int(time.time()) + 600})
    unsigned = head + '.' + payload
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    return unsigned + '.' + signature


def nullable(value):
    if isinstance(value, list): return [nullable(x) for x in value]
    if not isinstance(value, dict): return value
    result = {k: nullable(v) for k, v in value.items() if k != 'nullable'}
    return {'anyOf': [result, {'type': 'null'}]} if value.get('nullable') else result


SPEC = nullable(yaml.safe_load((ROOT / 'apps/web/openapi.yaml').read_text(encoding='utf-8')))
def check(name, data):
    schema = {**SPEC['components']['schemas'][name], 'components': SPEC['components']}
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(data)


@pytest.fixture(scope='module')
def services(tmp_path_factory):
    if not shutil.which('node'): pytest.skip('Node runtime required')
    folder = tmp_path_factory.mktemp('http-integration')
    api_port, worker_port = free_port(), free_port()
    secret = 'test-secret-' + 'x' * 48
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'services/orchestrator'), 'PYTHONUTF8': '1', 'HANDOFF_JWT_SECRET': secret, 'HANDOFF_INTERNAL_TOKEN': 'test-internal-token', 'HANDOFF_MODEL_MODE': 'offline', 'HANDOFF_RETRIEVAL_MODE': 'fixture', 'HANDOFF_TEAM_ID': 'atlas', 'HANDOFF_STATE_DIR': str(folder), 'HANDOFF_WORKER_URL': f'http://127.0.0.1:{worker_port}', 'PORT': str(api_port)}
    processes = []
    with (folder / 'services.log').open('w', encoding='utf-8') as log:
        try:
            processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'handoff.api:app', '--host', '127.0.0.1', '--port', str(worker_port), '--no-access-log'], cwd=ROOT, env=env, stdout=log, stderr=log))
            processes.append(subprocess.Popen(['node', 'services/api/server.mjs'], cwd=ROOT, env=env, stdout=log, stderr=log))
            client = httpx.Client(base_url=f'http://127.0.0.1:{api_port}', headers={'Authorization': 'Bearer ' + jwt(secret)}, timeout=60)
            for _ in range(100):
                try:
                    healthy = httpx.get(f'http://127.0.0.1:{worker_port}/internal/health', headers={'Authorization': 'Bearer test-internal-token'}).status_code == 200
                    if healthy and client.get('/v1/onboarding?userId=kim').status_code == 404: break
                except httpx.HTTPError: pass
                time.sleep(.1)
            else: raise AssertionError('Services failed to start: ' + (folder / 'services.log').read_text(encoding='utf-8'))
            yield client, secret, folder, processes
            client.close()
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)


def poll(client):
    for _ in range(100):
        response = client.get('/v1/onboarding?userId=kim')
        assert response.status_code == 200, response.text
        ws = response.json(); check('OnboardingWorkspace', ws)
        if ws['status'] != 'generating': return ws
        time.sleep(.1)
    raise AssertionError('generation never finished')


def test_public_end_to_end(services):
    client, secret, folder, processes = services
    assert client.get('/v1/onboarding?userId=kim', headers={'Authorization': ''}).status_code == 401
    assert client.get('/v1/onboarding?userId=kim', headers={'Authorization': 'Bearer ' + jwt(secret, exp=1)}).status_code == 401
    assert client.get('/v1/onboarding?userId=kim').status_code == 404
    payload = {'userId': 'kim', 'role': '운영 담당자', 'teamId': 'atlas', 'sourceScope': ['slack', 'notion', 'drive']}
    assert client.post('/v1/onboarding/generate', json={**payload, 'userId': 'someone'}).status_code == 403
    response = client.post('/v1/onboarding/generate', json=payload)
    assert response.status_code == 202, response.text
    check('GenerationJob', response.json())
    assert response.json()['pollUrl'] == '/v1/onboarding?userId=kim'
    ws = poll(client)
    assert ws['status'] == 'ready', (folder / 'services.log').read_text(encoding='utf-8')
    assert client.get('/v1/onboarding?userId=other').status_code == 403
    assert client.get('/v1/onboarding?userId=kim&include=bogus').status_code == 400
    other = client.get('/v1/onboarding?userId=other', headers={'Authorization': 'Bearer ' + jwt(secret, 'other')})
    assert other.status_code == 404
    for section in ws['sections']:
        response = client.get('/v1/onboarding/sections/' + section['id'])
        assert response.status_code == 200; check('OnboardingSection', response.json())
    first = ws['progress']['checklist'][0]
    response = client.patch('/v1/onboarding/checklist/' + first['id'], json={'completed': True, 'note': 'read'})
    assert response.status_code == 200; check('ChecklistMutation', response.json())
    assert response.json()['progress']['completed'] == 1
    assert response.json()['progress']['percent'] == 100 // len(ws['progress']['checklist'])
    assert client.patch('/v1/onboarding/progress', json={}).status_code == 400
    response = client.patch('/v1/onboarding/progress', json={'currentSectionId': 'setup', 'lastSeenItemId': first['id']})
    check('OnboardingProgress', response.json())
    assert client.get('/v1/onboarding/progress?userId=kim').json()['currentSectionId'] == 'setup'
    context = {'onboardingId': ws['id'], 'currentPath': '/onboarding', 'sectionId': 'unknowns', 'selectedText': '모임통장', 'entityIds': []}
    response = client.post('/v1/onboarding/ask', json={'question': '법인인데 왜 모임통장?', 'context': context})
    assert response.status_code == 200, response.text; check('AssistantAnswer', response.json())
    assert len(response.json()['citations']) >= 2
    assert response.json()['contextUsed']['selectedText'] == '모임통장'
    assert client.post('/v1/onboarding/ask', json={'question': '?', 'context': {**context, 'onboardingId': 'other'}}).status_code == 403
    assert client.post('/v1/onboarding/ask', json={'question': '?', 'context': {**context, 'selectedText': 'x' * 321}}).status_code == 400
    people = client.get('/v1/people?onboardingId=' + ws['id']).json()
    assert people['items'] and people['nextCursor'] is None
    check('Person', people['items'][0])
    timeline = client.get('/v1/projects/' + ws['timelines'][0]['projectId'] + '/timeline')
    assert timeline.status_code == 200
    check('ProjectTimeline', timeline.json())
    assert client.get('/v1/projects/unknown/timeline').status_code == 404
    response = client.get('/v1/sources/' + quote(ws['sources'][0]['id'], safe=''))
    assert response.status_code == 200; check('SourceLink', response.json())
    assert client.get('/v1/onboarding/unknowns').json() == {'items': []}
    target = ws['sections'][0]['blocks'][0]['id']
    response = client.post('/v1/onboarding/feedback', json={'targetType': 'section_block', 'targetId': target, 'rating': 'needs-review'})
    assert response.status_code == 201; check('Feedback', response.json())
    response = client.post('/v1/onboarding/generate', json={**payload, 'refresh': True})
    assert response.status_code == 202
    refreshed = poll(client)
    assert refreshed['status'] == 'ready' and refreshed['id'] == ws['id']
    assert next(x for x in refreshed['progress']['checklist'] if x['id'] == first['id'])['completed']
    # Real worker outage: old validated content is preserved and status becomes failed.
    processes[0].terminate(); processes[0].wait(timeout=10)
    client.post('/v1/onboarding/generate', json={**payload, 'refresh': True})
    failed = poll(client)
    assert failed['status'] == 'failed' and failed['sources'] == refreshed['sources']
    assert failed['progress']['checklist'] == refreshed['progress']['checklist']
