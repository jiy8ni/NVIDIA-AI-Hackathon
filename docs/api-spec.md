# FE ↔ Backend 구현 프로파일

원본은 [apps/web/openapi.yaml](../apps/web/openapi.yaml)이며 내용과 12개 operation을 변경하지 않았습니다. /v1, camelCase, 응답 envelope 없음, 5개 section을 유지합니다. Node가 원본 YAML로 Ajv 요청 검증과 주요 생성 응답 검증을 수행합니다.

| UI 동작 | 공개 API | 응답/주의 |
|---|---|---|
| 초기 조회·poll | GET /v1/onboarding?userId= | Workspace. 생성 전 404 |
| 생성·갱신 | POST /v1/onboarding/generate | 202 GenerationJob |
| 섹션 열기 | GET /v1/onboarding/sections/{sectionId} | Section |
| 질문 | POST /v1/onboarding/ask | AssistantAnswer |
| 진행률 | GET /v1/onboarding/progress?userId= | 서버 계산 진행률 |
| 위치 저장 | PATCH /v1/onboarding/progress | 빈 PATCH 거부 |
| 완료/재개 | PATCH /v1/onboarding/checklist/{itemId} | 체크리스트와 진행률 |
| 사람 | GET /v1/people?onboardingId= | items, nextCursor=null |
| 일정 | GET /v1/projects/{projectId}/timeline | 확인된 데이터 없으면 404 |
| 출처 | GET /v1/sources/{sourceId} | SourceLink |
| 미확정 항목 | GET /v1/onboarding/unknowns | items |
| 피드백 | POST /v1/onboarding/feedback | 201 Feedback |

## 생성 및 상태

- generate의 pollUrl은 기존 GET onboarding입니다. 새로운 /jobs API를 만들지 않았습니다.
- 최초 골격 generating → worker 결과 검증 → ready. 실패는 failed이며 기존 성공 콘텐츠 보존.
- 중복 실행 중 요청은 같은 job, refresh=false의 ready 요청은 기존 complete job.
- FE는 Workspace.status를 2초마다 조회하며 210초 후 수동 재조회로 전환합니다.
- 체크리스트 percent는 floor(completed/total*100), 0개면 0.
- 생성 중 PATCH/ask는 409. 동일 역할 재생성에서 안정적인 항목 ID와 완료/note 보존.
- SourceLink.id를 경로에 넣을 때 encodeURIComponent를 사용합니다.
- 원본의 불완전한 오류 목록은 구현 프로파일에서 ApiError 형태로 보완했습니다.

## UI payload 연결

| type | 구현 |
|---|---|
| paragraph | 원문 인용 + FACT 표기. the-job 원문은 기본 접힘 |
| job | 목적·담당자·기한·다음 행동·완료 조건·절차·의존성·차단 요소·미확정 질문 |
| comparison | 양쪽 근거·원본 수정 시각·미해결 표기 |
| unknown-card | 담당자 미상도 표시. 질문 후보는 승인권자와 구별 |
| checklist | 서버 progress 항목을 참조. LLM이 completed를 지정하지 않음 |
| callout | 조회/생성 시점, offline·부분 조회 등 안내 |
| person-card | 명시된 역할·관계의 Person.id 연결; personId와 기존 personIds 모두 제공 |
| timeline | 명시된 날짜·상태만 표시; 범위는 프로젝트 전체 기간이 아님 |
| 기타 | 기존 renderer와 body fallback 유지 |

자유 객체 payload의 로컬 프로파일 확장: job.ownerName, confidence, unresolvedQuestions, dependencies; unknown-card.contactCandidates; paragraph.defaultCollapsed. 공개 root schema를 바꾸지 않았지만 **Jay의 upstream 코드에 자동 합의된 것으로 간주하지 않습니다.** 이번 로컬 UI에는 직접 연결하고 브라우저 검증했습니다.

FACT는 '원문에 명시'이지 최신 정책으로 승인됐다는 뜻이 아닙니다. 모델의 충돌 판단과 질문 후보는 INFERENCE, 첫 주 읽기 계획은 SUGGESTION, 누락 값은 UNKNOWN입니다. 수치형 신뢰도를 근거 없이 만들지 않습니다.

Unknown.suggestedOwnerId가 필수이므로 담당자가 미상인 항목은 root unknowns에 허위 ID를 넣지 않고 unknown-card에 보존합니다. 홈 질문 수는 해당 블록 수를 사용합니다. Person.relationship과 ProjectTimeline 날짜도 임의 생성하지 않습니다.

## 인증·배포 제한

- 모든 공개 operation에 JWT 검증. userId/sub 일치, teamId 서버 바인딩.
- 내부 worker 별도 Bearer token, loopback.
- 데모 JWT는 local runner가 브라우저에 주입하므로 실제 서비스 배포용이 아닙니다.
- 저장은 단일 Node 프로세스의 파일 기반 원자적 교체. 다중 replica/멀티테넌트 SaaS 저장소가 아닙니다.
- 문서/질문 텍스트는 React text로 렌더링하고 임의 HTML을 실행하지 않습니다.
- 내부 체크리스트/피드백 저장만 허용. Slack/Notion/Drive write-back 없음.
- HTTP 자료는 workspace/sections/people/ask 등 모든 캐시 조회 전 ACL bridge로 sourceIds 전체를 재확인합니다. 미설정·실패는 503, 권한 철회는 403으로 전체 콘텐츠를 차단합니다. 실제 ACL 서버와 SSO는 후속 통합 조건입니다.
- dependencies는 {taskId,title,sourceIds} 목록입니다. 명시적 선행 업무가 유일한 현재 Task와 일치할 때만 연결하며, 누락·동명이면 확인 질문으로 남깁니다. 순환은 blocked와 확인 질문으로 처리합니다.
- 인물/일정 자동 구조화는 보수적인 라벨 문법을 사용합니다. 원문 author/owner를 관계로 전환하거나 updatedAt을 일정으로 사용하지 않습니다.
