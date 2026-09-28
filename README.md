# HandoffOS — 실행 가능한 온보딩 MVP

기존 React UI와 Node 공개 API를 유지하면서, 모의 generate/ask를 Python Orchestrator에 연결했습니다. Slack·Notion·Drive 원문에서 업무 맥락·Task Contract·상충 가능성·확인 질문을 만들고, 근거를 검증한 후 화면에 제공합니다.

**기본 개발 모드는 offline 모델 + 같은 PC의 Retrieval MCP입니다.** 실제 Nemotron 추론을 실행했다고 표시하지 않습니다. Slack·Notion·Drive credential을 아직 설정하지 않았다면 `.env`에서 `HANDOFF_RETRIEVAL_MODE=fixture`로 바꿔 오프라인 데모를 사용할 수 있습니다. NVIDIA NeMo Agent Toolkit 1.5 워크플로는 Python 3.12에서 실제 실행하고 브라우저 통합까지 확인했습니다. 실제 Nemotron endpoint 호출은 키·tokenizer 설정 후 별도 검증이 필요합니다.

## 빠른 실행 (Windows PowerShell)

Node 22.12+ 또는 24, Python 3.11+가 필요합니다. 먼저 Retrieval MCP와 HandoffOS를 **같은 PC**에서 실행하세요. 이번 검증 환경은 Node 24.11.1 / Python 3.14.3입니다.

터미널 1에서 Retrieval MCP를 켭니다. 이 서비스의 `.env`에는 Slack·Notion·Drive의 읽기 전용 credential만 둡니다. MCP 자체 인증은 로컬 loopback 실행에서는 사용하지 않습니다.

```powershell
cd services/retrieval-mcp
Copy-Item .env.example .env
uv sync
uv run --env-file .env retrieval-mcp
```

터미널 2에서 저장소 루트로 돌아와 HandoffOS를 실행합니다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m pip install -e '.[mcp]'
npm.cmd ci --prefix services/api
npm.cmd ci --prefix apps/web
node scripts/dev.mjs
```

브라우저에서 http://127.0.0.1:5173 접속 → 역할 입력 → **온보딩 생성 시작**. 재실행 시 이전 사용자 문서가 열립니다.

- 공개 API 8787, 내부 worker 8788, UI 5173. 모두 loopback 전용입니다.
- 실행기가 12시간짜리 개발 JWT와 내부 인증 키를 메모리에서 발급합니다. 키를 로그에 출력하지 않습니다.
- `Ctrl+C`로 세 프로세스를 종료합니다.
- `.runtime/state.json`에 문서·진행률·피드백, `.runtime/traces/`에 본문 없는 실행 이벤트가 저장됩니다.
- 실행기는 기본으로 `HANDOFF_RETRIEVAL_MODE=mcp`, `RETRIEVAL_MCP_URL=http://127.0.0.1:8000/mcp`를 사용합니다. `.env`에서는 이를 명시적으로 바꾸거나 `fixture` 오프라인 모드를 선택할 수 있습니다. 실제 키를 VITE_* 변수에 넣거나 Git에 커밋하지 마세요.
- 이 로그인 방식은 **본인 컴퓨터의 가상 데이터 데모용**입니다. 공개 SaaS 인증/SSO가 아닙니다.

## 구현 범위

- generate: 202 접수 → background 실행 → 기존 GET onboarding polling → ready/failed.
- ask: 같은 Orchestrator의 질문 경로 → 필요 시 소스 재검색 → 인용 검증 → 동기 응답.
- 제한된 search/next_page/read_more/finish 루프, 반복 방지, 시간·문맥·후보 수 제한.
- 최종 Evidence/RetrievalResponse 규약 그대로 검증. snapshotId 등 새 필수 필드 없음.
- Task Contract 상세 필드, 없는 담당자·기한·완료 조건은 미확정.
- 명시된 인물 관계·일정 구조화, 선행 업무 연결·차단 요소·순환 의존성 확인 질문.
- 충돌 양쪽 원문 및 수정 시각, 질문 후보와 초안. 전송 기능 없음.
- 기존 UI의 5개 섹션, 체크리스트 보존, 선택 문맥 Q&A, 실제 인용 링크, 검토 피드백.
- 검증된 같은 콘텐츠를 사용하는 한글 PDF 내보내기.
- HTTP/Streamable-MCP Retrieval 어댑터와 Nemotron JSON 추론 어댑터, 선택적 NAT workflow 등록.
- HTTP 자료를 캐시에서 제공하기 전 전체 출처 ACL 재확인. 미설정·실패·권한 철회 시 제공 차단.

## 테스트와 PDF

```powershell
.venv/Scripts/python.exe -m pytest -q
node --test services/api/access.test.mjs
npm.cmd run build --prefix apps/web -- --emptyOutDir=false
$env:PYTHONPATH='services/orchestrator'
.venv/Scripts/python.exe -m handoff.export_pdf --user-id kim-juhyung
```

마지막 명령은 UI와 같은 저장 문서를 `output/pdf/handoffos-onboarding.pdf`로 내보냅니다. `--demo`를 붙이면 별도의 오프라인 fixture 예제를 생성합니다. CLI는 저장 상태에 fixture 출처가 확인된 경우만 내보내며 실제 자료의 PDF는 ACL 연결 전 차단합니다. Linux에서는 `HANDOFF_PDF_FONT`에 한국어를 지원하는 TrueType 폰트 경로를 지정하세요. PDF 다운로드용 공개 endpoint는 추가하지 않았습니다.

이 환경의 Vite 재빌드는 기본 outDir 정리 단계에서 오류 메시지 없이 종료되어 `--emptyOutDir=false`로 검증했습니다. 이 옵션은 오래된 해시 asset을 지우지 않습니다. 배포 시에는 별도의 깨끗한 산출물 디렉터리를 사용하세요.

브라우저 테스트: Playwright 설치 환경에서 `node scripts/ui-smoke.cjs`. `HANDOFF_PLAYWRIGHT_MODULE`로 모듈 경로, `HANDOFF_BROWSER_CHANNEL=msedge`로 설치된 Edge를 사용할 수 있습니다. 개발 서버를 먼저 실행하세요.

## 다음 연결 단계

1. 지윤님: [내부 요청/응답 연결 규약](docs/data-contract.md)의 요청 인자, PDF provider 매핑, 실제 ACL 확인 방식 합의.
2. 민성: NVIDIA API 키·선택 모델 tokenizer를 설정하고 [NVIDIA 실행 안내](docs/nvidia-runtime.md)대로 live 검증.
3. Jay: 상세 job/comparison/unknown-card payload와 기존 UI의 추가 렌더러 확인.
4. 실제 조직 사용 전: SSO·사용자별 소스 권한·권한 철회 시 저장 콘텐츠 재검증·분산 작업 큐·암호화 저장 도입.

**설정·외부 연결이 남은 부분:** 실제 SaaS/MCP 및 ACL 서버 연결, 실제 Nemotron 호출, NeMo Framework/Microservices 참가 전제의 실행 증거. 자동 일정 알림·SSO·외부 쓰기는 이번 MVP 범위 밖입니다. 관계와 날짜를 확인할 수 없는 사람/타임라인은 만들어내지 않고 빈 배열과 확인 질문으로 남깁니다.

- [루프/시퀀스 구조도](docs/architecture.md)
- [공개 API 동작과 FE 규약](docs/api-spec.md)
- [원칙 및 검증 기록](docs/verification.md)
- [데모 순서](docs/demo-script.md)
- [공개 OpenAPI 원본](apps/web/openapi.yaml)
- [Retrieval MCP·OpenShell 배포 계획](docs/openshell-deployment.md)

NAT 실행 방법과 고정 의존성은 [NVIDIA 실행 안내](docs/nvidia-runtime.md), 검증 범위와 제한은 [검증 기록](docs/verification.md)을 확인하세요.
