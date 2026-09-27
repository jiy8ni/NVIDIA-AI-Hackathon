# NeMoTron에서 Retrieval MCP 사용하기

이 서비스는 Slack, Notion, Google Drive를 읽기 전용 MCP 도구로 노출한다.
NeMoTron은 `search_evidence`로 근거를 먼저 찾고, 필요할 때만
`fetch_context`로 원문 맥락을 확인하도록 연결한다.

## 1. Retrieval MCP 실행

```powershell
cd services/retrieval-mcp
Copy-Item .env.example .env
```

`.env`에 NVIDIA API 키와 NeMoTron NIM 모델 ID를 설정한다. 그 밖에 검색할
소스의 자격 증명도 입력한다. 상세 권한과 OAuth 설정은
[`services/retrieval-mcp/README.md`](../services/retrieval-mcp/README.md)를 따른다.

```dotenv
NVIDIA_API_KEY=...
NVIDIA_MODEL=<사용할_NeMoTron_NIM_모델_ID>
SLACK_USER_TOKEN=...
NOTION_TOKEN=...
GOOGLE_ACCESS_TOKEN=...
```

서버를 실행한다.

```powershell
uv sync --extra nat
uv run --env-file .env retrieval-mcp
```

기본 MCP 엔드포인트는 `http://127.0.0.1:8000/mcp`다. NeMoTron 워크플로가 다른
호스트에서 실행되면 `RETRIEVAL_MCP_URL`을 그 호스트에서 접근 가능한 주소로 바꾼다.

## 2. NeMo Agent Toolkit 워크플로 실행

`workflows/retrieval_agent.yaml`은 NVIDIA NIM LLM과 이 MCP 엔드포인트를 이미
연결한다. 별도 터미널에서 실행한다.

```powershell
cd services/retrieval-mcp
uv run --env-file .env nat run --config_file workflows/retrieval_agent.yaml --input "온보딩 절차를 찾아줘"
```

워크플로는 다음 순서로 동작한다.

1. NeMoTron이 `search_evidence`를 호출해 원문 근거와 링크를 검색한다.
2. 확인이 필요한 결과 하나에만 `fetch_context`를 호출한다.
3. 모델은 근거 기반으로 답하고, 검색 실패·권한 부족·근거 부재를 구분해 표시한다.

## 3. 다른 NeMoTron 에이전트에 붙이기

다른 NAT YAML에서도 아래 `function_groups`를 추가하고, 에이전트의 `tool_names`에
`retrieval`을 포함하면 된다. `NVIDIA_MODEL`에는 해당 에이전트가 사용할 NeMoTron
모델 ID를 둔다.

```yaml
function_groups:
  retrieval:
    _type: mcp_client
    server:
      transport: streamable-http
      url: ${RETRIEVAL_MCP_URL:-http://127.0.0.1:8000/mcp}
    include: [search_evidence, fetch_context]
```

운영 환경에서는 `.env`를 커밋하지 말고 비밀 관리 서비스로 주입한다. MCP 서버는
읽기 전용이며 원문을 별도 데이터베이스에 저장하지 않는다.
