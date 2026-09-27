import asyncio
import pytest
from handoff.contracts import AgentError, Synthesis
from handoff.models import OfflineModel
from handoff.projection import validate_synthesis, project
from test_engine import records


TEXT = '''인물: 대협 | 역할: 운영팀장 | 관계: 협업자 | 대상 역할: 운영 담당자
프로젝트: 가을 행사 | 일정: 정산 검토 | 날짜: 2026-10-02 | 상태: 예정
프로젝트: 가을 행사 | 일정: 준비 회의 | 날짜: 2026-09-28 | 상태: 완료
업무: 증빙 수집 | 목적: 지출 확인 | 담당자: 미정 | 기한: 미정 | 다음 행동: 영수증 확인
업무: 정산 검토 | 목적: 누락 확인 | 담당자: 미정 | 기한: 미정 | 다음 행동: 자료 확인 | 선행 업무: 증빙 수집 | 차단 요소: 승인자 미확정
'''


def synthesis(text=TEXT, role='운영 담당자'):
    registry = {'r': records()[0].model_copy(update={'content': text})}
    result = asyncio.run(OfflineModel().synthesize({'records': [{'recordKey': 'r', 'content': text}], 'role': role}))
    validate_synthesis(result, registry, role)
    return result, registry


def test_grounded_people_timeline_and_dependency_projection():
    result, registry = synthesis()
    out = project(result, registry, 'generate', {}, [], 'offline', '2026-09-27T00:00:00Z')
    assert out['people'][0]['relationship'] == 'collaborator'
    timeline = out['timelines'][0]
    assert timeline['startDate'] == '2026-09-28' and timeline['endDate'] == '2026-10-02'
    assert [m['status'] for m in timeline['milestones']] == ['done', 'future']
    jobs = [b['payload'] for s in out['sections'] for b in s['blocks'] if b['type'] == 'job']
    assert jobs[1]['dependencyTaskIds'] == [jobs[0]['taskId']]
    assert jobs[1]['blockers'][0]['text'] == '승인자 미확정'
    assert jobs[1]['readiness'] == 'blocked'


def test_unknown_dates_and_unrelated_role_do_not_create_entities():
    result, _ = synthesis(TEXT.replace('2026-10-02', '미정').replace('2026-09-28', '다음 주'), '디자이너')
    assert result.people == [] and result.milestones == []


@pytest.mark.parametrize('value', ['2026-02-30', '2026-13-01'])
def test_invalid_calendar_dates_rejected(value):
    with pytest.raises(AgentError):
        synthesis(TEXT.replace('2026-10-02', value))


def test_author_does_not_establish_relationship():
    result, registry = synthesis()
    result.people[0].relationship.value = '직속 관리자'
    with pytest.raises(AgentError):
        validate_synthesis(result, registry, '운영 담당자')


def test_unresolved_dependency_does_not_create_fake_task_id():
    result, registry = synthesis(TEXT.replace('선행 업무: 증빙 수집', '선행 업무: 없는 업무'))
    out = project(result, registry, 'generate', {}, [], 'offline', '2026-09-27T00:00:00Z')
    task = next(b['payload'] for s in out['sections'] for b in s['blocks'] if b['title'] == '정산 검토')
    assert not task['dependencyTaskIds']
    assert any('없는 업무' in q for q in task['unresolvedQuestions'])


def test_cyclic_dependencies_are_flagged_not_silently_scheduled():
    result, registry = synthesis(TEXT.replace('다음 행동: 영수증 확인', '다음 행동: 영수증 확인 | 선행 업무: 정산 검토'))
    out = project(result, registry, 'generate', {}, [], 'offline', '2026-09-27T00:00:00Z')
    jobs = [b['payload'] for s in out['sections'] for b in s['blocks'] if b['type'] == 'job']
    assert all(j['readiness'] == 'blocked' for j in jobs)
    assert all(any('순환' in q for q in j['unresolvedQuestions']) for j in jobs)
