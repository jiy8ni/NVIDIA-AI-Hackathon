# HandoffOS OpenShell 실행 절차

이 디렉터리는 **Gateway가 준비된 지원 Linux/WSL host**에서 사용한다. 현재 개발 Windows PC에는 WSL 배포판, Docker, OpenShell CLI가 없으므로 이 문서의 create/attach 명령을 아직 실행하지 않았다.

## 1. Host 준비

Docker 또는 지원 compute driver와 OpenShell Gateway를 먼저 설치한 뒤 아래 명령이 성공해야 한다.

```bash
openshell --version
openshell status
openshell doctor check
```

Windows에서는 WSL2 Linux 배포판과 Docker Desktop의 Linux container backend 또는 별도 Linux host가 필요하다. Gateway가 준비되면 그 gateway를 선택한다.

```bash
openshell gateway add http://127.0.0.1:18080 --local --name local
openshell gateway select local
```

실제 CLI 버전의 `gateway add` 인자는 `openshell gateway add --help`로 다시 확인한다.

## 2. Image build와 policy 검증

repository root에서 image를 build한다. image에는 secret·source·`.env`를 넣지 않는다. Python package는 실행기가 clone한 `services/orchestrator`를 `PYTHONPATH`로 직접 사용하므로 `/opt`의 runtime venv를 sandbox 중에 변경하지 않는다.

```bash
docker build -f deploy/openshell/Dockerfile -t handoffos-runtime:integration .
openshell policy prove --policy deploy/openshell/policy.yaml
```

`policy.yaml`의 `retrieval-mcp.internal`을 실제 **private TLS** MCP DNS로 바꾼 뒤 prove한다. sandbox가 host의 `127.0.0.1:8000`을 쓰게 하면 안 된다.

## 3. Provider로 secret 주입

NVIDIA key를 `.env`, shell history, GitHub, Docker image에 저장하지 않는다. provider creation은 host environment에서 key를 읽고 sandbox에는 endpoint-bound reference만 전달한다.

```bash
# Host process environment에만 NVIDIA_API_KEY가 있는 상태에서 실행
openshell provider create --name handoffos-nvidia --type nvidia --credential NVIDIA_API_KEY
openshell provider list
```

Retrieval MCP가 bearer token/OAuth/mTLS를 요구하게 되면, 그 secret도 **별도 MCP provider profile**로 Retrieval owner가 등록·attach한다. 현재 repository의 Retrieval MCP server에는 token 검증이 없으므로 `RETRIEVAL_MCP_TOKEN`을 설정해도 운영 보안 경계가 되지 않는다.

## 4. 검증된 ref만 clone하여 시작

`HANDOFF_GIT_REF`에는 merge 전 검증 branch 또는 merge 후 SHA를, `HANDOFF_EXPECTED_COMMIT`에는 정확한 commit SHA를 둔다. bootstrap은 clone 후 SHA가 다르면 중단한다. 기본은 offline+fixture로 sandbox 자체를 먼저 검증하는 것이다.

```bash
openshell sandbox create \
  --name handoffos-mvp \
  --from handoffos-runtime:integration \
  --provider handoffos-nvidia \
  --policy deploy/openshell/policy.yaml \
  --env HANDOFF_GIT_REF=feat/agent-retrieval-integration \
  --env HANDOFF_EXPECTED_COMMIT=<verified-commit-sha> \
  --env HANDOFF_MODEL_MODE=offline \
  --env HANDOFF_RETRIEVAL_MODE=fixture \
  --forward 5173 \
  --detach
```

MCP/Nemotron live validation으로 바꿀 때는 image에 model과 **동일한 tokenizer.json**을 read-only로 포함하고, 다음 non-secret configuration만 바꾼다.

```bash
openshell sandbox exec handoffos-mvp -- \
  env HANDOFF_MODEL_MODE=nemotron \
      HANDOFF_RETRIEVAL_MODE=mcp \
      RETRIEVAL_MCP_URL=https://retrieval-mcp.internal/mcp \
      NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b \
      NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1 \
      NVIDIA_TOKENIZER_PATH=/opt/handoff-model/tokenizer.json \
  /usr/local/bin/bootstrap-and-run-handoffos
```

Provider attach/update 뒤에는 새 process를 시작해야 injected configuration을 받는다. live MCP mode는 Retrieval MCP 인증/ACL이 준비된 뒤에만 허용한다.

## 5. 실행 확인과 종료

```bash
openshell policy get handoffos-mvp --full
openshell logs handoffos-mvp --tail
openshell sandbox exec handoffos-mvp -- git -C /sandbox/work/HandoffOS rev-parse HEAD
openshell sandbox delete handoffos-mvp
```

log에는 policy deny event와 credential value 유출이 없는지 확인한다. policy update와 provider attach는 해당 CLI 버전에서 `--wait` 지원 여부를 help로 확인해 적용 완료 후 다음 단계로 진행한다.
