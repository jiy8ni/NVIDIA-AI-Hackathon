# Handoff Retrieval MCP

Read-only MCP tools for searching Slack, Notion, and Google Drive without
making either source a copied system of record.

## Tools

- `search_evidence`: finds source-grounded records across enabled providers.
- `fetch_context`: expands one selected Slack thread, Notion page, or Drive file.

Both tools return the shared `SearchResponse` contract: records include original
text or excerpts, source URL, source timestamps, author, extraction state, and
access state. They never return model-generated summaries.

## Local setup

```powershell
cd services/retrieval-mcp
Copy-Item .env.example .env
uv sync --extra nat
uv run --env-file .env retrieval-mcp
```

The Streamable HTTP MCP endpoint is `http://127.0.0.1:8000/mcp`.

In another terminal, run the NVIDIA Agent Toolkit workflow:

```powershell
uv run --env-file .env nat run --config_file workflows/retrieval_agent.yaml --input "총무의 행사비 정산 절차를 찾아줘"
```

## NeMoTron / NIM integration

See [`docs/neomotron-retrieval-mcp.md`](../../docs/neomotron-retrieval-mcp.md)
for environment setup, running the included NeMo Agent Toolkit workflow, and
adding this MCP server to another NeMoTron agent.

## Credentials

- Slack: create a user OAuth token with `search:read.public` and, when needed,
  `search:read.private`; add `channels:history` and `groups:history` so a
  selected thread can be expanded. The server uses Slack's current
  `assistant.search.context` API, not the legacy `search.messages` endpoint.
- Notion: create an Integration and explicitly share target pages and data
  sources with it. The in-memory index only covers pages visible to that
  Integration (up to `NOTION_INDEX_MAX_PAGES`). It is built in the background
  from server start, `NOTION_INDEX_CONCURRENCY` pages at a time; Notion's rate
  limit (~3 requests/s) still makes a 1,000-page workspace take 10+ minutes.
  Until the first build finishes, a search waits `NOTION_INDEX_WAIT_SECONDS`
  and then answers from the pages indexed so far with an `index_building`
  error, so results are marked partial. After `NOTION_INDEX_TTL_SECONDS` the
  old index keeps answering while a new one is built.
  `NOTION_INDEX_ROOTS` is an allowlist of databases or pages (names or IDs):
  only pages under them are indexed, including sub-pages, pages inside
  toggles or columns, and inline databases. Prefer it over indexing a whole
  workspace; new or unrelated databases then stay out by default.
  `NOTION_INDEX_EXCLUDE` removes databases or pages (and everything under
  them) even inside an allowed root, e.g. a member directory with contact
  details. Names can repeat across a workspace, so prefer IDs; an entry that
  matches several pages or databases is logged as a warning. A page that
  cannot be read is skipped and reported as `index_pages_skipped`; a failed
  rebuild keeps the old index and reports `index_refresh_failed`. All Notion
  requests, including search-time page checks, share
  `NOTION_INDEX_CONCURRENCY`. `search_evidence` with `refreshIndex: true`
  joins a running build or starts one, and waits.
- Google Drive: provide `GOOGLE_ACCESS_TOKEN`, or client ID, client secret, and
  refresh token for a read-only OAuth grant.

Keep every secret in `.env`; it is ignored by Git.

### Get a Slack user token locally

Add the app's Client ID and Client Secret to `.env`, then register this exact
Redirect URL in Slack's **OAuth & Permissions** page:

```text
http://localhost:3333/slack/callback
```

Run the one-time helper below. It prints an approval link, receives the Slack
callback locally, and saves the resulting user token into `.env` without
printing the token.

```powershell
uv run --env-file .env python scripts/slack_user_oauth.py
```

## Search behavior

`source_registry.yaml` expands domain terms and can narrow Slack to channel
names, Notion to indexed page IDs, or Drive to folder IDs. Empty lists search
every source item visible to the authenticated account. Results are
deduplicated, ranked by query relevance and freshness, and diversified across
providers.

`source_registry.yaml` is only a routing map: it stores aliases and source
locations, not source text or inferred organisational facts.

No raw source text is written to a database. The Notion index exists only in
server memory and disappears when the process stops.

## Mock workspace for agent-loop tests

`retrieval-mcp-mock` runs the same MCP server, service, registry and source adapters, but answers the
adapters' Notion/Slack/Drive API calls from `src/retrieval_mcp/mock_data/atlas_workspace.json`, a fictional
startup (Atlas). No credential is read and unknown hosts fail instead of reaching the network.

```powershell
cd services/retrieval-mcp
uv run retrieval-mcp-mock   # same endpoint: http://127.0.0.1:8000/mcp
```

The data is split so that answers need several searches: the old handoff page says "정산 승인 기준은
'결재 규정' 문서를 확인하세요", and the approval rule page is reachable only by searching that name. The old
page (모임통장) conflicts with the 9/22 meeting notes and the #finance thread (법인 계좌 전환 검토, 미확정),
and one page and one message carry a prompt-injection line. Results carry `*.atlas.test` links, so they
cannot be mistaken for real sources.

## Verification

```powershell
uv run python -m unittest discover -s tests -v
uv run nat mcp client tool list --url http://127.0.0.1:8000/mcp
```
