"""Conservative, citation-checked entity projection. No identity/authority inference."""
import hashlib
import re
from datetime import date
from .contracts import AgentError

RELATIONSHIPS = {'직속 관리자': 'manager', '팀원': 'teammate', '협업자': 'collaborator',
                 '분야 전문가': 'subject-matter-expert'}
STATUSES = {'완료': 'done', '다음': 'next', '예정': 'future', '차단': 'blocked'}


def labelled(field, label):
    return field.value is not None and any(
        re.search(r'(?:^|\|)\s*' + re.escape(label) + r'\s*[:：]\s*' + re.escape(field.value) + r'\s*(?:\||$)', r.quote)
        for r in field.evidence)


def entity_refs(entity):
    return [r for f in entity.__dict__.values() for r in f.evidence]


def validate_entities(result, validate_field, role):
    for person in result.people:
        for key, label in [('name', '인물'), ('role', '역할'), ('relationship', '관계'), ('forRole', '대상 역할')]:
            f = getattr(person, key)
            validate_field(f)
            if not labelled(f, label):
                raise AgentError('MODEL_OUTPUT_INVALID', '인물의 이름·역할·관계에는 명시적 근거가 필요합니다.')
        if person.relationship.value not in RELATIONSHIPS or person.forRole.value != role:
            raise AgentError('MODEL_OUTPUT_INVALID', '현재 역할에 대한 인물 관계를 확인할 수 없습니다.')
        # Co-occurrence prevents splicing a name from one person and a role from another.
        if not set.intersection(*[{(r.recordKey, r.quote) for r in f.evidence} for f in person.__dict__.values()]):
            raise AgentError('MODEL_OUTPUT_INVALID', '동일 인물에 대한 연결 근거가 필요합니다.')
    for milestone in result.milestones:
        for key, label in [('project', '프로젝트'), ('title', '일정'), ('date', '날짜'), ('status', '상태')]:
            f = getattr(milestone, key)
            validate_field(f)
            if not labelled(f, label):
                raise AgentError('MODEL_OUTPUT_INVALID', '일정에는 프로젝트·날짜·상태의 명시적 근거가 필요합니다.')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', milestone.date.value):
            raise AgentError('MODEL_OUTPUT_INVALID', '일정 날짜는 원문에 명시된 ISO 날짜만 허용합니다.')
        try:
            date.fromisoformat(milestone.date.value)
        except ValueError as exc:
            raise AgentError('MODEL_OUTPUT_INVALID', '유효하지 않은 일정 날짜입니다.') from exc
        if milestone.status.value not in STATUSES:
            raise AgentError('MODEL_OUTPUT_INVALID', '일정 상태를 임의 추정할 수 없습니다.')
        if not set.intersection(*[{(r.recordKey, r.quote) for r in f.evidence} for f in milestone.__dict__.values()]):
            raise AgentError('MODEL_OUTPUT_INVALID', '동일 일정의 연결 근거가 필요합니다.')


def project_entities(result, registry, sources):
    def stable(value):
        return hashlib.sha256(value.encode()).hexdigest()[:16]
    def ids(entity):
        return list(dict.fromkeys(registry[r.recordKey].sourceId for r in entity_refs(entity)))
    people = []
    for p in result.people:
        evidence = ids(p)
        value = {'id': 'person-' + stable(str(evidence) + p.name.value + p.role.value),
                 'name': p.name.value, 'role': p.role.value, 'relationship': RELATIONSHIPS[p.relationship.value],
                 'links': [s for s in sources if s['id'] in evidence], 'evidence': evidence}
        if not any(x['id'] == value['id'] for x in people):
            people.append(value)
    timelines = {}
    for m in result.milestones:
        pid = 'project-' + stable(m.project.value)
        timeline = timelines.setdefault(pid, {'projectId': pid, 'title': m.project.value,
            'startDate': m.date.value, 'endDate': m.date.value, 'milestones': [], 'owners': [], 'sources': []})
        item = {'id': 'milestone-' + stable(pid + m.title.value + m.date.value), 'title': m.title.value,
                'date': m.date.value, 'status': STATUSES[m.status.value], 'ownerIds': [], 'sourceIds': ids(m)}
        if not any(x['id'] == item['id'] for x in timeline['milestones']):
            timeline['milestones'].append(item)
        timeline['startDate'] = min(timeline['startDate'], m.date.value)
        timeline['endDate'] = max(timeline['endDate'], m.date.value)
        used = {sid for row in timeline['milestones'] for sid in row['sourceIds']}
        timeline['sources'] = [s for s in sources if s['id'] in used]
    for timeline in timelines.values():
        timeline['milestones'].sort(key=lambda x: x['date'])
    return people, list(timelines.values())
