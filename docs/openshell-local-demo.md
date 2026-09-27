# OpenShell 로컬 데모: 무엇을 했고, 지금 무엇이 되는가

## 한 줄 요약

HandoffOS의 **offline + fixture 데모**를 Windows의 WSL2/Docker Desktop 위에 설치한 OpenShell 샌드박스에서 실행했다.

지금 이 PC에서는 브라우저로 `http://localhost:5173`에 접속해 데모를 볼 수 있다. 이 문서는 클라우드 배포 안내가 아니라, 한 대의 개발 PC에서 OpenShell로 격리 실행한 현황과 재현 방법이다.

## 먼저 알아둘 용어

| 이름 | 여기서 하는 일 |
| --- | --- |
| HandoffOS | 온보딩 정보를 만들고 보여 주는 우리 프로젝트 |
| Docker Desktop | Windows에서 컨테이너를 실행하는 엔진 |
| WSL2 Ubuntu | Docker와 OpenShell 명령을 실행하는 Linux 환경 |
| OpenShell Gateway | 샌드박스를 만들고 안전한 통신을 관리하는 로컬 관리자 |
| OpenShell Sandbox | HandoffOS를 실제로 실행하는 격리된 컨테이너 |

흐름은 아래와 같다.

```text
Windows 브라우저
  └─ http://localhost:5173
       └─ OpenShell local port forward
            └─ handoffos-demo sandbox
                 ├─ Vite UI          : 5173
                 ├─ Node public API  : 8787
                 └─ Python worker    : 8788
```

## 현재 검증된 것

- OpenShell Gateway가 Docker Desktop과 연결되고, mTLS 인증 상태로 동작한다.
- `handoffos-demo`라는 OpenShell 샌드박스가 `Ready` 상태로 실행된다.
- Windows의 `http://localhost:5173`에서 UI가 HTTP 200으로 응답한다.
- UI가 쓰는 API 포트 `8787`도 로컬 포워딩된다.
- 실제 `POST /v1/onboarding/generate` 요청 후 workspace가 `ready` 상태가 되는 것을 확인했다.
- 이 검증은 `HANDOFF_MODEL_MODE=offline`, `HANDOFF_RETRIEVAL_MODE=fixture`로 실행했다. 즉, 외부 모델이나 회사 데이터를 사용하지 않는 데모다.

## 아직 검증되지 않은 것

아래 항목은 이번 OpenShell 작업과 별개로, retrieval 담당자가 검증해야 한다.

1. retrieval MCP가 실제 Slack, Notion, Google Drive를 읽는지
2. HandoffOS 에이전트가 retrieval MCP를 실제로 호출하는지
3. NVIDIA Nemotron 실추론이 `NVIDIA_API_KEY`, 모델명, tokenizer 설정과 함께 동작하는지
4. 여러 사람이 접속하는 클라우드/공유 환경 배포

특히 현재 HandoffOS의 HTTP retrieval adapter는 `/search` 형식을 기대하지만 retrieval MCP는 `/mcp` endpoint를 제공한다. 따라서 둘 사이를 연결하는 코드 또는 adapter 검증이 끝나기 전에는 `HANDOFF_RETRIEVAL_MODE=http`로 바꾸면 안 된다.

## 이 브랜치에 추가된 파일

| 파일 | 역할 |
| --- | --- |
| `deploy/openshell/Dockerfile` | Node 22, Python 3.11, Python virtualenv, 프로젝트의 고정 의존성을 포함한 실행 이미지를 만든다. |
| `.dockerignore` | `.env`, 로컬 가상환경, `node_modules`, 출력물 등을 이미지에 넣지 않는다. |
| `docs/openshell-local-demo.md` | 이 문서 |

이미지에는 API 키나 `.env` 파일을 넣지 않는다. 현재 데모는 실행 때도 외부 API 키가 필요 없다.

## 현재 PC에서 데모 열기

1. Docker Desktop을 실행한다.
2. Windows 브라우저에서 `http://localhost:5173`을 연다.
3. 역할을 입력하고 **온보딩 생성 시작**을 누른다.

UI는 `5173`, API는 `8787`에 각각 로컬 바인딩되어 있다. 둘 다 `127.0.0.1`만 사용하므로 다른 기기에서 접근할 수 없다.

## OpenShell 안에서 실행되는 명령

이미지의 시작 명령은 아래와 같다.

```text
node /app/scripts/dev.mjs
```

이 스크립트는 UI, Node API, Python worker를 한 번에 시작한다. 기본값이 offline + fixture라서 NVIDIA/Slack/Notion/Google 키 없이도 데모 화면과 생성 흐름을 재현할 수 있다.

## 코드가 바뀐 뒤 다시 배포하는 방법

아래 명령은 **Ubuntu WSL 터미널**에서 실행한다. 먼저 이 저장소의 최신 코드를 받은 뒤 진행한다.

```bash
cd /mnt/c/Users/user/dev/NVIDIA-AIHackathon
docker build --tag handoffos-app:offline --file deploy/openshell/Dockerfile .

openshell sandbox delete handoffos-demo
openshell sandbox create \
  --name handoffos-demo \
  --from handoffos-app:offline \
  --cpu 2 \
  --memory 4Gi \
  --expose 5173 \
  --detach \
  --no-tty \
  -- node /app/scripts/dev.mjs
```

그 뒤 포트 포워드를 다시 시작한다. 터미널을 계속 켜 둘 수 있다면 아래 두 명령을 각각 실행한다.

```bash
openshell forward start 127.0.0.1:5173 handoffos-demo
openshell forward start 127.0.0.1:8787 handoffos-demo
```

현재 작업 PC에서는 이 두 포워드를 `systemd --user` 서비스로 등록했고 `loginctl enable-linger user`도 적용했다. 따라서 WSL 사용자 서비스가 살아 있는 동안 자동으로 유지된다.

## 멈추거나 상태를 볼 때

```bash
# 샌드박스 상태와 로그
openshell sandbox list
openshell logs handoffos-demo

# 현재 PC에서 등록한 로컬 포워드 서비스 중지
systemctl --user stop handoffos-ui-forward.service
systemctl --user stop handoffos-api-forward.service

# 샌드박스 중지 또는 삭제
openshell sandbox stop handoffos-demo
openshell sandbox delete handoffos-demo
```

`delete`는 샌드박스 안의 작업 공간까지 지우므로, 로그나 결과가 필요하면 먼저 확인한다.

## live 모드로 바꾸기 전에 필요한 값

현재는 필요 없다. 아래 값은 retrieval MCP 연결까지 검증된 뒤에만 `.env` 또는 OpenShell provider로 주입한다. 값을 Git에 커밋하거나 Dockerfile에 적으면 안 된다.

| 목적 | 필요한 값 |
| --- | --- |
| NVIDIA 추론 | `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `NVIDIA_TOKENIZER_PATH` |
| HandoffOS HTTP retrieval | `HANDOFF_RETRIEVAL_URL`, `HANDOFF_RETRIEVAL_TOKEN`, `HANDOFF_ACCESS_URL` |
| retrieval MCP | `RETRIEVAL_MCP_URL`, Slack/Notion/Google OAuth 또는 token 값 |

## 보안 메모

- 브라우저 UI와 API 포워드는 `127.0.0.1`에만 바인딩했다.
- Docker 샌드박스가 Gateway로 되돌아갈 수 있도록 Gateway는 WSL 내부 네트워크에서 수신하도록 설정했다. Gateway 통신은 mTLS를 유지한다.
- 이 구성은 개인 개발 PC용이다. 팀 공유·클라우드 배포에는 OIDC 또는 접근 프록시, 별도 HTTPS 도메인, 명시적인 정책 검토가 필요하다.

## 관련 문서

- [OpenShell sandbox service forwarding](https://docs.nvidia.com/openshell/dev/manage/sandboxes/overview)
- [OpenShell Gateway configuration](https://docs.nvidia.com/openshell/dev/manage/gateways/configuration)
- [retrieval MCP와 NeMoTron 안내](neomotron-retrieval-mcp.md)
- [NVIDIA runtime 설정](nvidia-runtime.md)
