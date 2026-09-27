"""The shared Retrieval-to-Agent handoff contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    PROCEDURE = "procedure"
    DECISION = "decision"
    TIMELINE = "timeline"
    OWNERSHIP = "ownership"
    ARTIFACT = "artifact"


class SourceName(StrEnum):
    SLACK = "slack"
    NOTION = "notion"
    DRIVE = "drive"


class SearchStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"


class TimeRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start: datetime | None = None
    end: datetime | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=500)
    intent: Intent = Intent.PROCEDURE
    role: str | None = Field(default=None, max_length=120)
    project: str | None = Field(default=None, max_length=160)
    entities: list[str] = Field(default_factory=list, max_length=12)
    time_range: TimeRange | None = Field(default=None, alias="timeRange")
    source_scope: list[SourceName] = Field(
        default_factory=lambda: list(SourceName), alias="sourceScope"
    )
    refresh_index: bool = Field(default=False, alias="refreshIndex")
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=4096)


class PersonRef(BaseModel):
    id: str | None = None
    name: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(default="1.0", alias="schemaVersion")
    source_id: str = Field(alias="sourceId")
    source_type: str = Field(alias="sourceType")
    title: str | None = None
    title_origin: str = Field(alias="titleOrigin")
    content: str
    content_origin: str = Field(alias="contentOrigin")
    url: str = ""
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    retrieved_at: datetime = Field(alias="retrievedAt")
    author: PersonRef = Field(default_factory=PersonRef)
    owner: PersonRef = Field(default_factory=PersonRef)
    extraction_status: str = Field(default="complete", alias="extractionStatus")
    access_status: str = Field(default="accessible", alias="accessStatus")


class Coverage(BaseModel):
    source: SourceName
    status: str
    record_count: int = Field(alias="recordCount")


class RetrievalError(BaseModel):
    # Usually slack/notion/drive. "unknown" is reserved for malformed sourceId
    # values passed to fetch_context.
    source: str
    code: str
    message: str


class SearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    status: SearchStatus
    records: list[Evidence]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    coverage: list[Coverage]
    errors: list[RetrievalError]

    def as_tool_result(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class SourceResult(BaseModel):
    """Internal source-adapter result; never returned directly to an Agent."""

    records: list[Evidence] = Field(default_factory=list)
    errors: list[RetrievalError] = Field(default_factory=list)
    next_cursor: str | None = None
