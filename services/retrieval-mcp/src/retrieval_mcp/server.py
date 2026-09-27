"""Streamable HTTP MCP entrypoint for the handoff retrieval service."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from .models import SearchRequest, SourceName
from .service import RetrievalService

mcp = FastMCP("Handoff Retrieval", json_response=True)
service = RetrievalService()


@mcp.tool()
async def search_evidence(
    query: str,
    intent: Literal["procedure", "decision", "timeline", "ownership", "artifact"] = "procedure",
    role: str | None = None,
    project: str | None = None,
    entities: list[str] | None = None,
    timeRange: dict[str, str | None] | None = None,
    sourceScope: list[Literal["slack", "notion", "drive"]] | None = None,
    refreshIndex: bool = False,
    limit: int = 10,
    cursor: str | None = None,
) -> dict:
    """Search Slack, Notion, and Google Drive for source-grounded handoff evidence.

    Use this first. It returns original text or excerpts, links, timestamps,
    authors, search coverage, and failures; it does not summarize or infer.
    """
    request = SearchRequest(
        query=query,
        intent=intent,
        role=role,
        project=project,
        entities=entities or [],
        timeRange=timeRange,
        sourceScope=sourceScope or list(SourceName),
        refreshIndex=refreshIndex,
        limit=limit,
        cursor=cursor,
    )
    return (await service.search(request)).as_tool_result()


@mcp.tool()
async def fetch_context(sourceId: str) -> dict:
    """Read the full context for a selected evidence source.

    Use only when a search result needs verification: Slack returns ordered
    thread messages, Notion returns page blocks, and Drive returns file chunks.
    """
    return (await service.context(sourceId)).as_tool_result()


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
