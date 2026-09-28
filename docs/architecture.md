# HandoffOS MVP 구조와 에이전트 루프

## 시스템 경계

```mermaid
flowchart LR
  UI["기존 React UI"] --> API["Node API :8787<br/>JWT·job·사용자 상태"]
  API -->|"내부 토큰 / loopback"| Worker["Python worker :8788"]
  Worker --> Runtime{"workflow runtime"}
  Runtime -->|"native 기본"| O["Orchestrator"]
  Runtime -->|"nat 선택 / 실행 검증됨"| NAT["NeMo Agent Toolkit function"]
  NAT --> O
  O --> Provider["읽기 전용 Retrieval adapter<br/>fixture / HTTP / Streamable MCP"]
  Provider --> Fixture["가상 Slack·Notion·Drive"]
  Provider -->|"mcp: search_evidence<br/>필요 시 fetch_context 1회"| Ingestion["지윤: Retrieval MCP / ingestion / ACL"]
  O --> Model["Offline 추출 또는 Nemotron API"]
  O --> Verify["인용·Task 필드·출처 검증"]
  Verify --> Content["GeneratedContent / AssistantAnswer"]
  Content --> API
  API --> ACL["HTTP 캐시 조회 시 전체 출처 ACL 재확인"]
  API --> State["현재 workspace·checklist 저장"]
  State --> PDF["같은 검증 콘텐츠의 PDF / fixture 전용 CLI"]
```

루프는 **Python Orchestrator 내부**에서만 수행합니다. FE polling이나 cron이 에이전트 추론 루프가 아닙니다. Node는 HTTP 계약·인증·job·완료 상태를 담당하며 LLM이 완료율을 결정하지 않습니다. 소스별 독립 추론 에이전트는 만들지 않습니다.

## 요청 중 실행되는 루프

```mermaid
flowchart TD
  A["서버가 검증한 요청·scope"] --> B["상태 구성: 질문/역할/페이지 문맥"]
  B --> C["모델 결정<br/>search / next_page / read_more / finish"]
  C -->|search| D["허용 소스·중복·예산 검사"]
  C -->|next_page| D
  D --> E["RetrievalResponse 검증"]
  E --> F["접근·파싱·URL·명령 주입 검사"]
  F --> G["원문 registry / 내부 recordKey<br/>동일 sourceId의 서로 다른 chunk 보존"]
  G --> H["토큰 예산 내 원문 구간 구성"]
  H --> C
  C -->|read_more| M["이미 받은 원문의 다음 구간<br/>추가 네트워크 접근 없음"]
  M --> H
  C -->|finish 또는 제한| I["사실·업무·충돌·질문 합성"]
  I --> R["인용 복구: 따옴표·공백 차이만 원문 문자열로 되돌림<br/>그래도 불일치하면 해당 항목만 제외·미확정 처리"]
  R --> J{"원문 인용과 필드 검증"}
  J -->|제외 항목이 있으면 한 번 재합성| I
  J -->|정리 결과가 계약 위반| X["명시적 오류 / failed"]
  J -->|성공| K["공개 스키마 투영<br/>제외 건수·근거 부족 안내 포함"]
```

명령 주입 정규식은 탐지 보조 수단이며 완전한 탐지기를 의미하지 않습니다. 핵심 보안 경계는 소스 텍스트가 시스템 지시가 되지 않는 메시지 구조, 서버 scope 고정, 그리고 **외부 쓰기/임의 URL 방문/코드 실행 도구 자체가 없는 것**입니다. 현재 모델 제공자로 보내는 접근 가능한 텍스트는 배포 설정으로 승인된 정보여야 합니다.

## 사용자 제공 시퀀스 준수

```mermaid
sequenceDiagram
  actor U as 신규 구성원
  participant FE as 기존 온보딩 UI
  participant API as Node Onboarding API
  participant O as Python Orchestrator
  participant R as Retrieval provider
  U->>FE: 역할 선택 / 온보딩 시작
  FE->>API: POST /v1/onboarding/generate
  API->>API: job + generating 골격 저장
  API-->>FE: 202 jobId, status, pollUrl
  API->>O: background generate
  loop 제한된 근거 탐색·검토
  O->>R: 검색 또는 다음 페이지
    R->>Ingestion: MCP search_evidence
    opt excerpt 또는 partial evidence 1건
      R->>Ingestion: MCP fetch_context
    end
    Ingestion-->>R: RetrievalResponse
    R-->>O: frozen RetrievalResponse 검증본
    O->>O: 평가 / 필요하면 추가 탐색
  end
  O-->>API: 검증된 GeneratedContent
  API->>API: 현재 checklist 병합 / ready 원자적 저장
  FE->>API: GET /v1/onboarding?userId=...
  API-->>FE: Workspace (Job이 아님)
  FE->>API: GET /v1/onboarding/sections/{sectionId}
  API-->>FE: 블록과 출처
  FE->>API: PATCH /v1/onboarding/checklist/{itemId}
  API-->>FE: 항목 상태와 재계산 진행률
  U->>FE: 현재 페이지에 질문
  FE->>API: POST /v1/onboarding/ask
  API->>O: 질문·문맥 + 검증한 scope
  O->>R: 새 질문 기준 검색
  R-->>O: 현재 조회 결과
  O-->>API: AssistantAnswer
  API-->>FE: 답변·인용·후속 질문
```

generate는 자료 검색을 끝낸 뒤 202를 주는 방식이 아닙니다. 먼저 접수하고 background 실행합니다. ask는 동기 실행입니다.

## 예산과 중단

| 항목 | generate | ask |
|---|---:|---:|
| 검색/다음 페이지 총 횟수 | 최대 8 | 최대 4 |
| 저장 원문 다음 구간 검토 | 최대 2 | 최대 1 |
| 후보 원문 | 최대 64 | 최대 40 |
| 모델에 동시에 넣는 원문 | 최대 24 (검색별로 번갈아 배치) | 최대 24 (검색별로 번갈아 배치) |
| MCP `fetch_context` 확장 | 적중 블록 주변 최대 12개 | 같음 |
| Python 실행 제한 (`HANDOFF_*_TIMEOUT_SECONDS` 기본값) | 360초 | 180초 |
| 새 검색 시작 마감 | 200초 (제한 − 160초, 최소 절반) | 110초 (제한 − 70초, 최소 절반) |
| Node 요청 제한 | Python 제한 + 15초 | Python 제한 + 15초 |
| 브라우저 ask 요청 제한 | — | Node 제한 + 15초 |
| 인용 검증 재합성 | 최대 1 | 최대 1 |

모델은 이전 검색어와 남은 검색 횟수를 보고, 원문이 가리키는 다른 문서·채널·규정 같은 단서가 있으면 새 키워드로 다시 검색합니다(2026-09-28 실제 Nemotron이 검색 1회 후 종료해 늘림). 넓은 첫 검색의 결과가 나중 검색 결과를 모델 입력에서 밀어내지 않도록 검색별로 번갈아 배치합니다. 마감 이후에는 새로운 탐색을 시작하지 않고 합성으로 넘어갑니다. 시간 초과 시 성공으로 위장하지 않습니다. tokenizer는 live 모델에 맞는 실제 파일을 사용하며 전체 프롬프트/출력 여유도 다시 검사합니다.

## 코드 위치

- `services/orchestrator/handoff/engine.py`: 상태·선택·제한·원문 registry.
- `contracts.py`: 확정 입력 및 내부 합성 타입.
- `providers.py`: Fixture/HTTP/Streamable-MCP retrieval 경계. MCP 도구·응답의 불일치가 공개 API로 새지 않도록 contract validation과 scope 검사를 수행한다.
- `models.py`: offline 기준선 / Nemotron JSON 결정·합성.
- `projection.py`, `entities.py`: 인용·Task 관계·인물·일정 검증과 공개 응답 변환.
- `api.py`: 내부 인증·worker 진입점.
- `nat_workflow.py`: 선택적 NAT 등록 및 호출.
- `services/api/server.mjs`: 기존 공개 API 구현.
- `export_pdf.py`: 저장된 콘텐츠의 PDF 변환.

NemoClaw은 이 MVP의 필수 런타임이 아니다. 현재 준비한 OpenShell 배포 경계·실행 계획은 [`openshell-deployment.md`](openshell-deployment.md)에 있으며, OpenShell Gateway/Sandbox 실제 실행은 호스트 요건을 갖춘 뒤에만 주장한다. 기본 로컬 경계가 OpenShell 샌드박스와 동등하다고 주장하지 않습니다.
