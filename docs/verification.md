# MVP 구현 및 원칙 재검증

검증일: 2026-09-28. 기준 main: 5164c37.
작업 브랜치: feat/agent-retrieval-integration. 실제 NVIDIA hosted 연동과 모의 Retrieval 자료 검증을 아래처럼 구분합니다.

## 실행 결과

- Python 3.12.14 + NAT 1.5.0 환경: **47 tests passed**. 입력 계약, guided JSON 추론 어댑터, 엔진, 근거/인물/일정/업무 관계, 실제 Node↔Python 프로세스, 실제 로컬 HTTP Retrieval, native/NAT 동등성 포함.
- Node ACL bridge: **9 tests passed**. 정상·미설정·권한 철회 3종·누락/중복/잘못된 requestId·네트워크 실패 시 차단.
- NAT SDK를 설치해 WorkflowBuilder와 등록된 handoffos function을 실제 실행. 모델은 offline, 자료는 fixture.
- NVIDIA Build Personal API 권한으로 `nvidia/nemotron-3.5-lightning-30b-a3b` hosted endpoint를 실제 호출. `ask`는 evidence 2건, `generate`는 evidence 7건을 조회하고 `validated` trace까지 확인.
- NAT 경유 Edge 브라우저: generate→poll→ready, 사람·일정·선행 업무, 충돌·질문·인용 링크 검증. page error 0. 데스크톱/모바일 화면 시각 검토.
- 공개 API: 12 operations 유지. 원본 OpenAPI Git diff 없음. 요청/Workspace/Section/Person/Timeline/Answer/Mutation/Feedback JSON Schema 검사.
- worker 종료 장애 테스트: failed 표시, 이전 성공 콘텐츠와 체크리스트 보존.
- 웹 production build: 깨끗한 dist-pr-verify로 **minify 포함 성공**. 기본 dist 재빌드는 이 Windows 환경에서 산출물 정리 시 오류 메시지 없이 exit 1; --emptyOutDir=false도 성공. 일반 기본 재빌드 문제가 해결됐다고 주장하지 않음. CI에서는 새 checkout으로 기본 build 실행.
- PDF: 저장된 fixture workspace를 9페이지 한글 PDF로 내보냄. 사람·일정·의존성·차단 요소·미확정·출처의 텍스트 검사와 9페이지 PNG 시각 검토.
- Python/Node 소스 검사 및 의존성 일관성 확인. CI 구성 포함; GitHub에서 실제 실행한 결과는 PR checks가 기준.

재현 명령:

```powershell
.venv-nat/Scripts/python.exe -m pytest -q
node --test services/api/access.test.mjs
.venv-nat/Scripts/python.exe -m pip check
npm.cmd run build --prefix apps/web -- --outDir dist-pr-verify
# 이미 존재하는 outDir 대신 새 경로를 쓰거나 --emptyOutDir=false 사용
```

NAT 환경 구성은 nvidia-runtime.md와 requirements-nat-lock.txt 참고. NAT 없는 기본 환경에서는 NAT 테스트 1개가 skip되는 것이 정상입니다. UI smoke는 개발 서버를 먼저 실행한 뒤 scripts/ui-smoke.cjs로 수행합니다. PDF·스크린샷·가상환경·.env·.runtime은 Git에서 제외합니다.

## 원칙별 확인

| 원칙 | 구현 및 검증 | 한계 |
|---|---|---|
| 최종 입력 규약 유지 | strict Pydantic + JSON Schema drift 검사 | 실제 팀원 응답 추가 확인 필요 |
| 같은 sourceId의 PDF chunks 보존 | content별 실행 내부 recordKey | 페이지 번호/locator 생성 안 함 |
| 담당자·기한 추측 금지 | null/UNKNOWN + 명시적 배정/기한 근거 | 라벨 없는 자연어는 보수적 미확정 |
| Task Contract | 목적·담당자·기한·행동·절차·완료 조건·의존성·차단·근거·질문 | 전체 업무 실행/자동 배정 기능 아님 |
| 업무 관계 | 명시된 제목으로 유일한 Task 연결, 누락/동명 질문, 순환 blocked | 빈 목록은 '없음 확정'이 아님 |
| 인물·일정 | 동일 인물/일정의 연결 근거, 역할 관계·ISO 날짜·상태 검증 | 명시 라벨 문법 중심; 자연어 관계 graph 전체 추출 아님 |
| 사실/추론/제안/미확정 | UI/PDF 라벨 | exact quote가 최신 승인까지 보증하지 않음 |
| 최신 내용 자동 채택 금지 | 양쪽 원문·시각·확인 질문 | offline 충돌 판별은 계좌 예제 중심 |
| 질문 대상 추천 | 관련 원문 작성/논의 참여자 후보 | 승인권자로 확정하지 않음 |
| 점진적 노출 | 홈 목차, Task 우선, 원문·절차·초안 접기 | 사용자 사용성 평가는 별도 |
| 실패와 부재 구분 | empty 근거 부족 vs failed 오류 | 실제 provider 오류 코드 튜닝 필요 |
| 제한적 agent loop | search/next_page/read_more/finish, 반복·시간·문맥 제한 | 무제한 자율 실행 아님 |
| 외부 읽기 전용 | 외부 쓰기/임의 URL 방문/코드 실행 도구 없음 | 정규식 탐지 완전성·OpenShell 격리 보장 아님 |
| 저장 자료 ACL | HTTP 자료는 매 조회 sourceIds 전체 재검증, 실패 시 차단 | 실제 ACL 서비스 계약 합의·연결 필요 |
| FE/API 호환 | 원본 12개 API와 기존 polling, 로컬 renderer 통합 | Jay upstream 합의는 PR 리뷰 필요 |
| 같은 콘텐츠 PDF | 검증 workspace만 변환, fixture 전용 CLI | 공개 PDF endpoint 추가 안 함 |

## 완료로 주장하지 않는 항목

1. **실제 Nemotron endpoint 호출:** hosted API `ask`/`generate`와 matching tokenizer까지 검증 완료. 실제 조직 데이터가 아닌 fixture evidence를 사용했으며, 비용·지연·한국어 품질 측정은 남음.
2. **실제 Retrieval/SaaS/MCP/ACL 서버:** feat/retrieval 브랜치는 확인 시 main과 같은 기준 커밋. HTTP 요청 및 ACL bridge는 합의 전 제안 규약. 로컬 HTTP 프로토콜 테스트이지 Slack/Notion/Drive 실제 접속이 아님.
3. **NeMo Framework/Microservices 전제 충족:** 실제 NeMo Retriever·NemoClaw/OpenShell 실행 없음. NAT 실행만으로 참가 전제를 충족했다고 단정하지 않음.
4. **NAT profiler/OTel:** SDK workflow는 검증했지만 trace는 자체 JSON 이벤트이며 profiler 연동은 별도.
5. SSO, 외부 write-back, cron 알림, 다중 replica 큐/DB, 암호화 저장·삭제/보존 정책, 성능·사용성/업무 이해도 개선 실험.

## 팀원이 이어서 할 최소 작업

- 지윤: 실제 Retrieval 응답 4종, 요청/커서·PDF provider·ACL 재확인 함수 합의.
- 민성: hosted Nemotron generate/ask·인용·충돌 품질 검증 완료. 실제 Retrieval MCP OAuth/ACL과 결합한 end-to-end 검증이 남음.
- Jay: 상세 Task/인물/일정 payload와 화면 연결 PR 리뷰.
- 제출: 실제 NVIDIA 실행 로그와 평가 지표를 추가하고 NeMo Framework/Microservices 인정 범위를 주최 측에 확인.

## 2026-09-28 다단계 루프 검증 (실제 Nemotron + 모의 Retrieval MCP)

모델은 실제 hosted `nvidia/nemotron-3.5-lightning-30b-a3b`, 자료는 `retrieval-mcp-mock`의 **가상** Atlas 업무 공간입니다. MCP 서버·어댑터·Orchestrator 코드는 실제 경로 그대로이고, Slack/Notion/Drive API 응답만 모의입니다. Node/UI 경로는 이번 검증에 포함하지 않았습니다.

| 실행 | 검색 순서 (모델이 고른 검색어) | 결과 |
|---|---|---|
| ask "법인인데 왜 인수인계 문서에는 모임통장을 판다고 되어 있지?" | 인수인계 모임통장 → 결재 규정 (단서) → 모임통장 → 행사비 정산 인수인계 (단서) | 80초. 모임통장(2026-08 인수인계) 대 법인 계좌 전환 검토(9/22 회의록) 충돌과 확인 질문 |
| ask "행사비 정산은 어떤 계좌로 하고, 승인은 누구에게 받아야 해?" | 행사비 정산 계좌 승인 → 모임통장 (단서) → 결재 규정 (단서) | 170초. 결재 기준(100만 원 미만 운영팀장·이상 대표) 인용, 계좌는 미확정 |
| generate (운영 담당자) | 운영 담당자 → 운영팀 역할과 책임 (단서) → 행사비 정산 인수인계 (단서) → 행사비 정산 | 156초. fact 15, 인물 2(라벨 근거), 업무 1(담당자 미정 → 미확정 질문) |

이번 변경: 모델에 이전 검색어·새 자료 수·남은 검색 횟수·원문에 인용된 미검색 문서명(`unsearchedLeads`) 제공, 검색 예산 ask 4 / generate 8, 검색별 교차 배치, `fetch_context` 12블록 창, 일시적 모델 오류 1회 재시도, 합성 전용 제한 시간(150초), Node·브라우저 제한 시간을 Python 제한보다 길게 조정.

남은 한계:
- hosted 호출 지연 편차가 큽니다(같은 판단 호출이 2초~60초 이상). 60초 초과 후 재시도가 세 번 중 두 번 발생했습니다.
- generate는 회의록의 두 번째 업무(선행 업무·차단 요소)와 일정(프로젝트 라벨)을 아직 추출하지 못했고, 충돌은 ask에서만 표시됐습니다.
- ask 하나에서 보완 관계인 두 결재 기준을 "충돌"로 분류했습니다. 충돌 판정 품질은 평가셋으로 측정해야 합니다.
- 모의 Slack 검색은 조사 제거 후 키워드 일치로 근사한 것입니다. 실제 Slack 검색 동작과 다를 수 있습니다.

## 2026-09-28 실제 Notion·Google Drive + Nemotron 속도 개선

실제 자료: Notion(새 Integration 토큰)과 Google Drive(`drive.readonly` refresh token). 자격 증명은 Git 제외 파일 `services/retrieval-mcp/.env`에만 있습니다. Slack은 미설정입니다. 실제 Notion에는 동아리 운영 자료가 거의 없고, Drive의 정관·내규·의사록이 주 근거입니다.

| 변경 | 이전 | 이후 |
|---|---|---|
| 모델 `nvidia/nemotron-3-super-120b-a12b` (tokenizer `nemotron-3`, API 토큰 수와 템플릿 16토큰 차이로 일치 확인) | lightning 판단 호출 29~107초 | 1~3초 |
| 판단 단계에는 원문 앞 400자 요약만, 합성에는 전체 원문 | 판단 입력 약 2만 토큰 | 약 1만 토큰 |
| 합성 원문 예산을 시스템 지시문·스키마·출력 몫을 뺀 값으로 계산 | `CONTEXT_LIMIT` 실패 | 입력 약 22.6k 토큰으로 통과 |
| Drive 파일 동시 다운로드 | 검색 1회 약 25초 | 약 3초 |
| 503/429는 1.5초·4초 대기 후 재시도, 계속 혼잡하면 `NVIDIA_FALLBACK_MODEL`로 그 호출만 대체(trace `model_fallback`) | 즉시 재시도 후 실패 | 재시도 후 통과 |
| 같은 문서의 다른 조각 번호로 인용한 문장은 실제로 들어 있는 조각으로 출처 정정 | 제33조 인용 4건 제외 | 인용 유지 |

결과: ask "총무는 어떤 일을 맡고, 회비나 예산은 어떤 규정으로 관리해?" 34초, 내규의 총무 책임·예산 인계·예산 공지 절차 인용. generate(역할 총무) 34.5초, 정관·내규·의사록 fact 8개와 확인 질문 1개.

남은 한계: 같은 문서의 PDF본과 Docs본이 모두 걸려 같은 문장이 중복됩니다. generate가 ask에서 찾은 제33조를 놓쳤습니다. Drive 검색이 `.ipynb` 과제 파일 조각으로 채워지는 경우가 있습니다. Drive access token은 서버 시작 후 한 번만 갱신되므로 1시간 넘게 켜 두면 Drive 호출이 실패할 수 있습니다.
