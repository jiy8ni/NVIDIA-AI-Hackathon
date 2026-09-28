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
