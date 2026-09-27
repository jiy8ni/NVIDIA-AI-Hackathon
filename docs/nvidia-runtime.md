# NVIDIA 실행 연결과 미검증 항목

## Nemotron

`.env.example`를 참고해 로컬 `.env` 또는 서버 환경 변수를 설정합니다.

```dotenv
HANDOFF_MODEL_MODE=nemotron
NVIDIA_API_KEY=실제 키 (커밋 금지)
NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_TOKENIZER_PATH=C:/absolute/path/to/tokenizer.json
NVIDIA_CONTEXT_TOKENS=32768
```

모델과 동일한 tokenizer.json을 제공하세요. 코드가 임의 모델 tokenizer를 내려받거나 문자 수를 live token 수로 가장하지 않습니다. 컨텍스트 길이는 endpoint의 허용 범위 이내로 설정합니다. 키/모델/tokenizer가 없거나 모델 응답이 실패하면 오류를 반환하며 offline 답으로 몰래 바꾸지 않습니다.

공식 [NVIDIA Chat Completions API](https://docs.api.nvidia.com/nim/reference/llm-apis)를 따르는 HTTP 어댑터입니다. NVIDIA 호스팅 API 호출 코드가 있다는 사실과 NIM/NeMo Microservices를 직접 배포했다는 사실은 다릅니다.

live 확인: 앱 재시작 → generate → ask → `.runtime/traces`의 modelMode=nemotron 확인 + 실제 응답과 인용 검토. 모델 응답 자체와 비용·지연·한국어 품질은 이번 환경에서 검증하지 않았습니다. 승인된 가상 자료부터 테스트하세요.

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

## Creative Use-case 제출 판단

| 채점 축 | 이번 구현의 증거 | 제출 전 보완 |
|---|---|---|
| NVIDIA 기술 깊이 | Nemotron 결정/합성 어댑터, 실제 NAT workflow 실행 | 실제 모델·NAT profiler trace와 비용/지연 측정 |
| 실용성·산업가치 | 업무 정리, 근거, 미확정 질문, 권한 경계 | 실제 신규 구성원 테스트·업무 이해도/소요시간 측정 |
| 완성도 | 동작 UI/API, 계약 테스트, PDF, 재현 명령 | live provider 연결·장애/성능 검증 |
| 독창성·커스터마이징 | Task Contract, 상충 근거 보존, '누구에게 확인할까' 초안 | 실제 조직의 의사결정·책임 데이터로 품질 평가 |

사진의 **NeMo Framework 또는 NeMo Microservices 활용** 조건은 단순 Nemotron API/NAT 이름만으로 충족했다고 판정하지 않습니다. 지윤님의 실제 NeMo Retriever 구성 등 실행 증거를 연결하고 주최 측 인정 범위를 확인해야 합니다. NemoClaw/OpenShell은 미구현이며 구현 완료 목록에 포함하지 않습니다.
