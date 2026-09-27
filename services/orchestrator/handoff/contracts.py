from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Source = Literal['slack', 'notion', 'drive']
Section = Literal['company', 'team-role', 'the-job', 'setup', 'unknowns']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PersonRef(StrictModel):
    id: str | None
    name: str | None


class Evidence(StrictModel):
    schemaVersion: Literal['1.0']
    sourceId: str = Field(min_length=1)
    sourceType: Literal['slack_message', 'slack_thread', 'notion_page', 'notion_block',
                        'drive_file', 'drive_document', 'pdf_chunk', 'unknown']
    title: str | None
    titleOrigin: Literal['source', 'generated', 'unavailable']
    content: str
    contentOrigin: Literal['source_full_text', 'source_excerpt', 'parsed_text']
    url: str
    createdAt: str | None
    updatedAt: str | None
    retrievedAt: str
    author: PersonRef
    owner: PersonRef
    extractionStatus: Literal['complete', 'partial', 'failed']
    accessStatus: Literal['accessible', 'restricted', 'deleted', 'unknown']

    @field_validator('createdAt', 'updatedAt', 'retrievedAt')
    @classmethod
    def timestamp(cls, value):
        if value is not None and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('timezone required')
        return value


class Coverage(StrictModel):
    source: Source
    status: Literal['searched', 'skipped', 'failed']
    recordCount: int = Field(ge=0)


class RetrievalError(StrictModel):
    source: str
    code: str
    message: str


class RetrievalResponse(StrictModel):
    requestId: str = Field(min_length=1)
    status: Literal['ok', 'empty', 'partial', 'failed']
    records: list[Evidence]
    nextCursor: str | None
    coverage: list[Coverage]
    errors: list[RetrievalError]


class Scope(StrictModel):
    userId: str
    teamId: str
    sources: list[Source]


class Decision(StrictModel):
    action: Literal['search', 'next_page', 'read_more', 'finish']
    query: str = Field(max_length=500)
    sources: list[Source]
    reason: str = Field(max_length=240)
    recordKey: str | None = None


class Ref(StrictModel):
    recordKey: str
    quote: str = Field(min_length=1, max_length=3000)


class Fact(Ref):
    sectionId: Section


class GroundedField(StrictModel):
    value: str | None
    evidence: list[Ref]


class Task(StrictModel):
    title: GroundedField
    objective: GroundedField
    ownerName: GroundedField
    dueText: GroundedField
    nextAction: GroundedField
    definitionOfDone: list[GroundedField] = Field(max_length=8)
    steps: list[GroundedField] = Field(max_length=10)
    dependencies: list[GroundedField] = Field(default_factory=list, max_length=8)
    blockers: list[GroundedField] = Field(default_factory=list, max_length=8)


class PersonCandidate(StrictModel):
    name: GroundedField
    role: GroundedField
    relationship: GroundedField
    forRole: GroundedField


class MilestoneCandidate(StrictModel):
    project: GroundedField
    title: GroundedField
    date: GroundedField
    status: GroundedField


class Conflict(StrictModel):
    topic: str = Field(max_length=160)
    alternatives: list[Ref] = Field(min_length=2, max_length=6)
    question: str = Field(max_length=500)


class Gap(StrictModel):
    question: str = Field(max_length=500)
    whyItMatters: str = Field(max_length=500)


class Synthesis(StrictModel):
    facts: list[Fact] = Field(max_length=24)
    tasks: list[Task] = Field(max_length=12)
    conflicts: list[Conflict] = Field(max_length=8)
    gaps: list[Gap] = Field(max_length=12)
    suggestions: list[str] = Field(max_length=6)
    people: list[PersonCandidate] = Field(default_factory=list, max_length=12)
    milestones: list[MilestoneCandidate] = Field(default_factory=list, max_length=16)


class RunRequest(StrictModel):
    mode: Literal['ask', 'generate']
    scope: Scope
    question: str = Field(default='', max_length=4000)
    role: str = Field(default='', max_length=200)
    context: dict = Field(default_factory=dict)


class AgentError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
