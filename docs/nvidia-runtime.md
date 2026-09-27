# NVIDIA 실행 연결과 미검증 항목

## Nemotron

`.env.example`를 참고해 로컬 `.env` 또는 서버 환경 변수를 설정합니다.

```dotenv
HANDOFF_MODEL_MODE=nemotron
NVIDIA_API_KEY=실제 키 (커밋 금지)
NVIDIA_MODEL=선택한_endpoint에서_허용된_모델_ID
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_TOKENIZER_PATH=C:/absolute/path/to/tokenizer.json
NVIDIA_CONTEXT_TOKENS=32768
```

모델과 동일한 tokenizer.json을 제공하세요. 코드가 임의 모델 tokenizer를 내려받거나 문자 수를 live token 수로 가장하지 않습니다. 컨텍스트 길이는 endpoint의 허용 범위 이내로 설정합니다. 키/모델/tokenizer가 없거나 모델 응답이 실패하면 오류를 반환하며 offline 답으로 몰래 바꾸지 않습니다.

공식 [NVIDIA Chat Completions API](https://docs.api.nvidia.com/nim/reference/llm-apis)를 따르는 HTTP 어댑터입니다. NVIDIA 호스팅 API 호출 코드가 있다는 사실과 NIM/NeMo Microservices를 직접 배포했다는 사실은 다릅니다.

2026-09-28 live 확인에서 제공받은 키로 `GET /v1/models`는 성공했지만, HandoffOS의 실제 `generate` 요청은 Chat Completions endpoint에서 HTTP 410을 받아 `MODEL_UNAVAILABLE`로 종료했습니다. trace에는 `modelMode=nemotron`과 오류 코드만 남았고 키·질문·원문은 기록하지 않았습니다. 즉 **HTTP 경로·키 주입·실패 처리까지는 실제로 확인했지만, 모델 합성 결과는 아직 검증되지 않았습니다.**

NVIDIA Build 조직에서 Public API Endpoints 사용 권한을 확인한 뒤, 해당 endpoint가 반환하는 모델 ID와 일치하는 `tokenizer.json`으로 다시 `generate`와 `ask`를 실행하세요. hosted endpoint를 쓸 수 없다면, [NVIDIA NIM self-hosted 배포 옵션](https://build.nvidia.com/nvidia/nemotron-3-nano-30b-a3b?nim=self-hosted)의 OpenAI-compatible base URL과 그 서버가 노출한 모델 ID를 `NVIDIA_BASE_URL`·`NVIDIA_MODEL`에 설정합니다. 어떤 경우에도 실패 시 offline 답으로 몰래 바꾸지 않습니다.

## 선택적 NeMo Agent Toolkit

`nat_workflow.py`는 같은 Orchestrator를 NAT function으로 등록하고, `HANDOFF_WORKFLOW_RUNTIME=nat`이면 내부 worker가 WorkflowBuilder를 통해 실행하도록 연결했습니다. native와 다른 합성 로직을 유지하지 않습니다.

NAT 1.5 기준 별도 Python 3.11~3.13 환경에서:

```powershell
python -m pip install -e '.[nat]'
$env:HANDOFF_WORKFLOW_RUNTIME='nat'
$env:HANDOFF_PYTHON='C:/absolute/path/to/nat-env/Scripts/python.exe'
node scripts/dev.mjs
```

그 Python의 uvicorn으로 내부 worker를 실행하세요. scripts/dev.mjs는 HANDOFF_PYTHON을 우선 사용하고, 미설정 시 repo/.venv를 사용합니다. 재현용 고정 의존성은 `requirements-nat-lock.txt`입니다. Python 3.12 venv에서 이 파일을 설치한 뒤 `pip install -e '.[nat]'`를 실행하세요. 예제 CLI workflow는 `configs/nat-workflow.yml`. workflow config의 user/team/sources는 신뢰하는 운영자가 설정합니다. NAT serve를 인증 없이 공개하지 마세요.

이 코드는 [NAT custom function 등록](https://docs.nvidia.com/nemo/agent-toolkit/1.5/extend/custom-components/custom-functions/functions.html), [plugin entry point](https://docs.nvidia.com/nemo/agent-toolkit/1.5/extend/plugins.html)를 기준으로 작성했습니다. 2026-09-27에 Python 3.12.14 + NAT 1.5.0을 별도로 설치해 WorkflowBuilder 실제 호출, native와의 계약·업무 결과 동등성, NAT 경유 UI generate/ask까지 검증했습니다. 모델은 offline, 자료는 fixture이므로 live NVIDIA 추론이나 NeMo Retriever 실행 증거는 아닙니다. 지원 환경은 [NVIDIA 설치 문서](https://docs.nvidia.com/nemo/agent-toolkit/1.5/get-started/installation.html)를 확인하세요.

기본 실행 trace는 자체 JSON 이벤트입니다. NAT profiler/OTel 연동까지 검증한 것은 아닙니다.

## Retrieval MCP 연결

`HANDOFF_RETRIEVAL_MODE=mcp`는 Orchestrator가 Streamable HTTP MCP에 직접 연결하는 읽기 전용 Provider입니다. MCP는 `search_evidence`를 호출하고, 결과가 excerpt/partial일 때만 `fetch_context`를 요청당 한 번 호출합니다. 모델의 네 가지 루프 행동(`search`, `next_page`, `read_more`, `finish`)을 늘리지 않습니다. `read_more`는 이미 받은 원문을 로컬에서 더 보는 행동입니다.

```powershell
# Orchestrator 환경 (native 실행에도 필요)
python -m pip install -e '.[mcp]'

# Retrieval MCP 환경에서 별도 실행
cd services/retrieval-mcp
uv sync --extra nat
uv run --env-file .env retrieval-mcp

# HandoffOS root .env 또는 OpenShell의 비밀/환경 주입 설정
HANDOFF_RETRIEVAL_MODE=mcp
RETRIEVAL_MCP_URL=http://127.0.0.1:8000/mcp
```

`http://`는 loopback 개발에서만 허용합니다. 다른 호스트에서는 TLS URL을 사용하며, Orchestrator는 coverage·source 범위·Evidence enum을 검증해 계약 밖 응답을 차단합니다. Retrieval MCP의 현재 구현은 source OAuth credential 미설정 시 `missing_credentials`를 반환합니다. 이는 MCP 연결 실패가 아니라 source 접근 실패입니다.

2026-09-28 검증: MCP Python client 1.30.0으로 local `http://127.0.0.1:8000/mcp`에 연결하고 `search_evidence`, `fetch_context`를 실제 호출했다. Slack credential을 일부러 주입하지 않은 상태라 두 도구가 각각 `missing_credentials`를 반환하는 것을 확인했다. excerpt의 full-context 보강, scope 초과 차단, coverage enum 정규화는 자동 테스트로 검증했다. 실제 조직 OAuth/ACL과 live NVIDIA API 호출은 이 검증에 포함되지 않는다.

## OpenShell 배포 준비

OpenShell 준비 파일과 실제 gateway 전제 조건은 [`openshell-deployment.md`](openshell-deployment.md), [`../deploy/openshell/README.md`](../deploy/openshell/README.md)를 따른다. NVIDIA API 키는 `.env`, image, GitHub에 넣지 않고 OpenShell의 `nvidia` Provider로만 넣는다. NemoClaw은 OpenClaw/Hermes를 OpenShell에서 실행하는 별도 blueprint이므로, HandoffOS의 custom Python Orchestrator를 NemoClaw라고 부르거나 설치 완료로 표기하지 않는다.

## Creative Use-case 제출 판단

| 채점 축 | 이번 구현의 증거 | 제출 전 보완 |
|---|---|---|
| NVIDIA 기술 깊이 | Nemotron 결정/합성 어댑터, 실제 NAT workflow 실행, live HTTP failure-path 확인 | API entitlement 또는 self-hosted NIM으로 실제 모델·NAT profiler trace와 비용/지연 측정 |
| 실용성·산업가치 | 업무 정리, 근거, 미확정 질문, 권한 경계 | 실제 신규 구성원 테스트·업무 이해도/소요시간 측정 |
| 완성도 | 동작 UI/API, 계약 테스트, PDF, 재현 명령 | live provider 연결·장애/성능 검증 |
| 독창성·커스터마이징 | Task Contract, 상충 근거 보존, '누구에게 확인할까' 초안 | 실제 조직의 의사결정·책임 데이터로 품질 평가 |

사진의 **NeMo Framework 또는 NeMo Microservices 활용** 조건은 단순 Nemotron API/NAT 이름만으로 충족했다고 판정하지 않습니다. 지윤님의 실제 NeMo Retriever 구성 등 실행 증거를 연결하고 주최 측 인정 범위를 확인해야 합니다. NemoClaw/OpenShell은 미구현이며 구현 완료 목록에 포함하지 않습니다.
