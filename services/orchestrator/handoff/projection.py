import hashlib
import re
from .contracts import AgentError
from .providers import provider_of, safe_url
from .entities import entity_refs, labelled, validate_entities, project_entities

SECTIONS = [('company', '회사 맥락'), ('team-role', '팀과 역할'), ('the-job', '지금 할 일'),
            ('setup', '설정과 첫 주'), ('unknowns', '열린 질문')]


def stable(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def link(e, quote=None):
    provider = provider_of(e)
    if not provider or not safe_url(e.url) or e.accessStatus != 'accessible':
        return None
    return {'id': e.sourceId, 'type': {'slack': 'slack_thread', 'notion': 'notion_page', 'drive': 'drive_file'}[provider],
            'provider': 'google_drive' if provider == 'drive' else provider,
            'title': e.title or f'{provider} 자료 ({e.sourceId})', 'url': e.url,
            'access': 'available', 'quotedContext': quote}


def validate_synthesis(result, registry, role=''):
    def ref(r):
        record = registry.get(r.recordKey)
        if not record or r.quote not in record.content or not link(record):
            raise AgentError('MODEL_OUTPUT_INVALID', '검증할 수 없는 인용이 포함되어 있습니다.')
    for f in result.facts:
        ref(f)
    def validate_field(f):
        for r in f.evidence:
            ref(r)
        if f.value is not None and (not f.value.strip() or not f.evidence or not any(f.value in r.quote for r in f.evidence)):
            raise AgentError('MODEL_OUTPUT_INVALID', '원문에 없는 필드가 포함되어 있습니다.')
        if f.value is None and f.evidence:
            raise AgentError('MODEL_OUTPUT_INVALID', '미확정 필드에 확정 근거를 연결할 수 없습니다.')
    validate_entities(result, validate_field, role)
    for task in result.tasks:
        for f in [task.title, task.objective, task.ownerName, task.dueText, task.nextAction, *task.steps, *task.definitionOfDone, *task.dependencies, *task.blockers]:
            validate_field(f)
        for label, fields in [('선행 업무', task.dependencies), ('차단 요소', task.blockers)]:
            for f in fields:
                if not labelled(f, label) or not any(task.title.value and task.title.value in r.quote for r in f.evidence):
                    raise AgentError('MODEL_OUTPUT_INVALID', '업무 관계의 명시적인 연결 근거가 필요합니다.')
        if task.ownerName.value and not any(re.search(r'(?:담당자|담당|assignee)\s*[:：]\s*' + re.escape(task.ownerName.value), r.quote, re.I) for r in task.ownerName.evidence):
            raise AgentError('MODEL_OUTPUT_INVALID', '명시적인 업무 담당자 배정 근거가 필요합니다.')
        if task.dueText.value and not any(re.search(r'(?:기한|마감|deadline|due)\s*[:：]\s*' + re.escape(task.dueText.value), r.quote, re.I) for r in task.dueText.evidence):
            raise AgentError('MODEL_OUTPUT_INVALID', '명시적인 업무 기한 근거가 필요합니다.')
    for c in result.conflicts:
        for r in c.alternatives:
            ref(r)
        if len({(r.recordKey, r.quote) for r in c.alternatives}) < 2:
            raise AgentError('MODEL_OUTPUT_INVALID', '충돌에는 서로 다른 두 근거가 필요합니다.')


def project(result, registry, mode, context, warnings, model_mode, generated_at):
    def ids(refs):
        return list(dict.fromkeys(registry[r.recordKey].sourceId for r in refs))
    def claim(r):
        return {'text': r.quote, 'claimType': 'FACT', 'sourceIds': ids([r])}
    def field(f):
        return {'value': f.value, 'claimType': 'FACT' if f.value is not None else 'UNKNOWN', 'sourceIds': ids(f.evidence)}
    def unknown():
        return {'value': None, 'claimType': 'UNKNOWN', 'sourceIds': []}
    refs = [*result.facts, *(r for c in result.conflicts for r in c.alternatives)]
    refs += [r for t in result.tasks for f in [t.title, t.objective, t.ownerName, t.dueText, t.nextAction, *t.steps, *t.definitionOfDone, *t.dependencies, *t.blockers] for r in f.evidence]
    refs += [r for e in [*result.people, *result.milestones] for r in entity_refs(e)]
    citations = []
    for r in refs:
        item = link(registry[r.recordKey], r.quote)
        if item and not any(c['id'] == item['id'] and c['quotedContext'] == item['quotedContext'] for c in citations):
            citations.append(item)
    def cite(r):
        sid = registry[r.recordKey].sourceId
        return next(i+1 for i, c in enumerate(citations) if c['id'] == sid and c['quotedContext'] == r.quote)
    notices = list(dict.fromkeys(warnings))
    if model_mode == 'offline':
        notices.insert(0, '오프라인 추출 데모: 실제 Nemotron 추론이 아닙니다.')
    sources = list({c['id']: {**c, 'quotedContext': None} for c in citations}.values())
    people, timelines = project_entities(result, registry, sources)
    if mode == 'ask':
        lines = ['확인한 원문'] if result.facts else ['현재 자료에서 답변을 확정할 근거를 찾지 못했습니다.']
        lines += [f'- {r.quote} [{cite(r)}]' for r in result.facts[:8]]
        for c in result.conflicts:
            lines += ['\n서로 다른 근거가 있어 확인이 필요합니다.']
            lines += [f'- {r.quote} [{cite(r)}]' for r in c.alternatives]
            lines.append('확인 질문: ' + c.question)
            candidates = [(registry[r.recordKey].author.name, cite(r)) for r in c.alternatives if registry[r.recordKey].author.name]
            for name, number in list(dict.fromkeys(candidates))[:3]:
                lines.append(f'질문 후보(근거 기반 추론): {name} — 관련 원문 작성자이며 승인권한은 미확정입니다. [{number}]')
        lines += ['\n미확정: ' + g.question for g in result.gaps]
        lines += ['\n에이전트 제안: ' + s for s in result.suggestions]
        lines += ['\n안내: ' + w for w in notices]
        return {'answer': '\n'.join(lines), 'citations': citations, 'relatedPeople': people,
            'suggestedQuestions': [c.question for c in result.conflicts] + [g.question for g in result.gaps],
            'contextUsed': {k: context.get(k) for k in ('currentPath', 'sectionId', 'selectedText')}}
    sections = [{'id': sid, 'number': f'{i+1:02}', 'title': title, 'description': '원문 근거와 미확정 사항을 구분해 확인하세요.',
                 'status': 'in-progress', 'blocks': [], 'sources': [], 'people': [], 'timelines': []} for i, (sid, title) in enumerate(SECTIONS)]
    by_id = {s['id']: s for s in sections}
    def add(sid, kind, title, body, payload, source_ids=None, hint='card', key=None):
        by_id[sid]['blocks'].append({'id': 'block-' + stable(key or sid+kind+title), 'type': kind, 'title': title,
            'body': body, 'payload': payload, 'sourceIds': source_ids or [], 'personIds': [], 'renderHint': hint})
    for f in result.facts:
        add(f.sectionId, 'paragraph', '원문에서 확인', f.quote, {'claims': [claim(f)], 'defaultCollapsed': f.sectionId == 'the-job'}, ids([f]), 'prose', f.recordKey+f.quote)
    task_ids = {id(t): 'task-' + stable(str(ids(t.title.evidence)) + str(t.title.value)) for t in result.tasks}
    for person in people:
        add('team-role', 'person-card', person['name'], person['role'],
            {'claims': [], 'personId': person['id'], 'personIds': [person['id']]}, person['evidence'])
        by_id['team-role']['blocks'][-1]['personIds'] = [person['id']]
    by_id['team-role']['people'] = people
    for timeline in timelines:
        add('the-job', 'timeline', timeline['title'], '표시 범위는 확인된 일정의 최소·최대 날짜이며 실제 프로젝트 시작·종료일은 아닙니다.',
            {'claims': [], 'projectId': timeline['projectId']}, [s['id'] for s in timeline['sources']], 'timeline')
    by_id['the-job']['timelines'] = timelines
    for t in result.tasks:
        task_id = 'task-' + stable(str(ids(t.title.evidence)) + str(t.title.value))
        fields = [t.title, t.objective, t.ownerName, t.dueText, t.nextAction, *t.steps, *t.definitionOfDone, *t.dependencies, *t.blockers]
        task_refs = [r for f in fields for r in f.evidence]
        dependencies, unresolved = [], []
        for dep in t.dependencies:
            candidates = [other for other in result.tasks if other is not t and other.title.value == dep.value]
            if len(candidates) == 1:
                dependencies.append({'taskId': task_ids[id(candidates[0])], 'title': dep.value, 'sourceIds': ids(dep.evidence)})
            else:
                unresolved.append('선행 업무 "' + (dep.value or '미정') + '"의 실제 항목을 확인해 주세요.')
        payload = {'claims': [claim(r) for r in task_refs], 'taskId': task_id, 'projectId': None,
            'objective': field(t.objective), 'ownerId': unknown(), 'ownerName': field(t.ownerName),
            'dueAt': unknown(), 'dueText': t.dueText.value, 'nextAction': field(t.nextAction),
            'definitionOfDone': [field(f) for f in t.definitionOfDone],
            'steps': [{'order': i+1, 'action': field(f), 'input': unknown(), 'output': unknown(), 'successCriteria': unknown(), 'requiresApproval': False} for i, f in enumerate(t.steps)],
            'dependencyTaskIds': [d['taskId'] for d in dependencies], 'dependencies': dependencies,
            'blockers': [{'text': f.value, 'claimType': 'FACT', 'sourceIds': ids(f.evidence)} for f in t.blockers],
            'readiness': 'blocked' if t.blockers else 'needs-clarification', 'lifecycle': 'open',
            'confidence': {'level': 'evidence-checked', 'reason': '인용 문자열은 원문과 일치하며, 배정·기한·승인은 별도 확인이 필요합니다.'},
            'unresolvedQuestions': unresolved + [f'{label}를 확인해 주실 수 있나요?' for label, f in [('담당자', t.ownerName), ('기한', t.dueText), ('다음 행동', t.nextAction)] if f.value is None] + ([] if t.definitionOfDone else ['완료 조건은 무엇인가요?'])}
        add('the-job', 'job', t.title.value or '확인할 업무', '문서 소유자와 업무 담당자는 구분합니다.', payload, ids(task_refs), key=task_id)
    jobs = {b['payload']['taskId']: b['payload'] for b in by_id['the-job']['blocks'] if b['type'] == 'job'}
    for task_id, payload in jobs.items():
        pending, visited = list(payload['dependencyTaskIds']), set()
        while pending:
            other = pending.pop()
            if other == task_id:
                payload['readiness'] = 'blocked'
                payload['unresolvedQuestions'].append('선행 업무 관계가 순환합니다. 실행 순서를 확인해 주세요.')
                break
            if other not in visited:
                visited.add(other)
                pending.extend(jobs[other]['dependencyTaskIds'])
    for c in result.conflicts:
        add('unknowns', 'comparison', c.topic, c.question,
            {'claims': [claim(r) for r in c.alternatives], 'field': c.topic,
             'rows': [{'label': (registry[r.recordKey].title or '원문') + ' · 원본 수정: ' + (registry[r.recordKey].updatedAt or '미상'), 'claim': claim(r)} for r in c.alternatives],
             'resolution': 'unresolved'}, ids(c.alternatives), 'table')
    questions = [(g.question, g.whyItMatters) for g in result.gaps] + [(c.question, '상충하는 근거를 확인해야 합니다.') for c in result.conflicts]
    for q, why in questions:
        # Only conflict-linked participants are candidates, never automatically the task owner.
        matching = [r for c in result.conflicts if c.question == q for r in c.alternatives]
        candidates = []
        for r in matching:
            e = registry[r.recordKey]
            if e.author.name and provider_of(e) == 'slack' and not any(c['name'] == e.author.name for c in candidates):
                candidates.append({'name': e.author.name, 'basis': '관련 원문 작성/논의 참여자. 승인권자는 확인되지 않음', 'sourceId': e.sourceId})
        add('unknowns', 'unknown-card', '확인할 질문', q, {'claims': [], 'gapId': 'gap-'+stable(q), 'unknownId': None,
            'question': q, 'whyItMatters': why, 'suggestedOwnerId': None,
            'recommendationReason': {'text': '확인 담당자의 역할·권한 근거가 부족합니다.', 'claimType': 'UNKNOWN', 'sourceIds': []},
            'draftQuestion': q, 'status': 'open', 'contactCandidates': candidates}, ids(matching), key=q)
    checklist = [{'id': 'read-'+stable(s['id']), 'sectionId': 'setup', 'label': '자료 읽기: '+s['title'], 'completed': False, 'note': None} for s in sources[:8]]
    add('setup', 'checklist', '첫 주 읽을 자료', '에이전트 제안: 원문을 읽은 뒤 미확정 질문을 확인하세요.',
        {'claims': [], 'itemIds': [c['id'] for c in checklist]}, [s['id'] for s in sources[:8]], 'compact-list')
    for w in notices:
        add('company', 'callout', '실행 안내', w, {'claims': [], 'severity': 'info', 'message': w}, hint='callout', key=w)
    add('company', 'callout', '생성 기준', '생성 시각: '+generated_at,
        {'claims': [], 'severity': 'info', 'message': '생성 시각: '+generated_at}, hint='callout')
    retrieved = sorted({e.retrievedAt for e in registry.values()})
    if retrieved:
        message = '자료 조회 시점: ' + retrieved[0] + (' ~ ' + retrieved[-1] if len(retrieved) > 1 else '')
        add('company', 'callout', '조회 기준', message, {'claims': [], 'severity': 'info', 'message': message}, hint='callout')
    for s in sections:
        if s['id'] == 'the-job':
            s['blocks'].sort(key=lambda b: 0 if b['type'] == 'job' else 1)
        used = {sid for b in s['blocks'] for sid in b['sourceIds']}
        s['sources'] = [x for x in sources if x['id'] in used]
        if not s['blocks']:
            add(s['id'], 'callout', '추가 확인 필요', '이 항목을 설명할 근거가 부족합니다.',
                {'claims': [], 'severity': 'info', 'message': '이 항목을 설명할 근거가 부족합니다.'}, hint='callout')
    return {'sections': sections, 'people': people, 'timelines': timelines, 'unknowns': [], 'sources': sources, 'checklist': checklist}
