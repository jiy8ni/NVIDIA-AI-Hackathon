# Retrieval → Orchestrator 팀 인수인계 규약

## 확정 입력: 수정하지 않음

- [Evidence JSON Schema](../packages/contracts/evidence.schema.json)
- [RetrievalResponse JSON Schema](../packages/contracts/retrieval-response.schema.json)
- 실행 검증 모델: `services/orchestrator/handoff/contracts.py`.
- 예제: `fixtures/evidence.json`.

사용자 최종 필드와 enum을 그대로 사용합니다. snapshotId/evidenceId/locator/parentSourceId는 추가하지 않았습니다. JSON Schema는 Pydantic 모델의 생성본이며 drift 테스트로 비교합니다.

Evidence의 nullable 필드는 필드 자체를 생략하지 않고 null을 전달합니다. 작성자/소유자는 각각 {id:null,name:null}이 가능합니다. sourceId는 원본 식별자, 실행 내부 recordKey는 content가 다른 chunk를 보존하기 위한 임시 키입니다. 후자는 팀원이 전달하지 않습니다.

제목이 generated이면 검색·판단의 사실 근거로 사용하지 않습니다. author/owner를 업무 담당자나 승인권자로 승격하지 않습니다. createdAt/updatedAt/retrievedAt은 일정이나 정책 시행일이 아닙니다.

## 요청 형식: 응답 계약과 달리 팀 합의가 필요한 adapter 제안

HTTP provider는 설정된 `HANDOFF_RETRIEVAL_URL`에 다음 JSON을 POST합니다. 이 endpoint는 공개 FE API가 아닙니다.

```json
{
  "requestId": "한 검색 호출의 UUID",
  "query": "현재 승인된 계좌 운영 기준",
  "sources": ["notion", "slack"],
  "cursor": null,
  "scope": {
    "userId": "kim-juhyung",
    "teamId": "atlas",
    "sources": ["slack", "notion", "drive"]
  }
}
```

- 응답 requestId는 요청 값 그대로 돌려줍니다.
- sources는 실제 이번 검색 대상, scope.sources는 서버가 허용한 최대 범위입니다.
- 다음 페이지는 같은 query/sources/scope와 받은 cursor를 전달합니다. cursor는 불투명 값으로 취급합니다.
- fixture의 query `*`는 가상 조직 전체 조회입니다. 실제 provider에서 지원할지 합의하거나 provider 내부에서 치환해야 합니다.
- 인증은 HANDOFF_RETRIEVAL_TOKEN Bearer. 실제 provider는 호출자와 원본 ACL을 독립 검증해야 합니다.
- 요청 필드나 프로토콜이 다르면 HttpProvider만 조정하고 공개 API·합성 코드는 유지합니다.
- 기존 프로세스 내부 Python 함수나 MCP로 제공할 경우 `search(query,sources,cursor,scope,request_id)` 인터페이스를 구현하면 됩니다. **현재 구현은 실제 MCP 서버가 아닙니다.**

## 상태 의미 및 보완 처리

| 값 | 처리 |
|---|---|
| ok | 확보한 records 사용. 전체 사실을 모두 읽었다는 뜻은 아님 |
| empty | 검색 성공·자료 없음. 근거 부족 답변, 업무가 없다고 단정하지 않음 |
| partial | 사용 가능한 자료만 사용 + 검색 공백 안내 |
| failed | 명시적 오류. ready/정상 답변으로 위장하지 않음 |
| nextCursor | 같은 검색의 다음 페이지. 한도 내에서만 조회 |
| coverage | 요청한 소스마다 한 항목. records만으로 0건/실패를 구별할 수 없음 |
| errors | 실패 안내에 활용. upstream 상세 문자열/토큰은 공개 응답에 복사하지 않음 |

coverage.recordCount는 **현재 응답에서 반환한 해당 provider 자료 수**입니다. accessible로 선별한 이후 수나 총 검색 hit 수가 아닙니다. coverage의 소스 집합이 요청과 다르거나 수가 맞지 않으면 계약 오류입니다.

## 자료 단위와 PDF

- Slack message/thread, Notion page/block은 원문을 유지합니다.
- Notion DB의 스키마·행을 현재 계약으로 전달할 때는 필요한 속성을 content 안에 명시적으로 직렬화합니다. AI 요약을 넣지 않습니다.
- PDF는 parsed_text + pdf_chunk. 같은 sourceId에 서로 다른 content를 보내도 둘 다 보존합니다.
- MVP에서 pdf_chunk의 provider 매핑은 명시적 `drive:` sourceId 접두사만 인정합니다. unknown provider를 coverage로 추정하지 않습니다.
- Drive 이외 PDF나 다른 ID 규칙은 **지윤님과 sourceId→provider 매핑 함수를 먼저 합의**해야 합니다.
- PDF 페이지 번호/locator가 없으면 페이지 번호를 만들어 인용하지 않습니다.
- 공개 가능한 http(s) 원본 URL이 없으면 현재 SourceLink 필수 필드를 채울 수 없으므로 사실 근거 투영에서 제외합니다. 내부 source-view 계약은 후속 논의입니다.

## 연결 전 같이 확인할 것

1. 실제 정상·empty·partial·failed 응답을 각 1개씩 공유.
2. requestId echo, coverage.recordCount, cursor 수명과 검색 조건 고정.
3. 사용자/팀별 접근 범위를 누가 검사하는지, 삭제·권한 철회가 언제 반영되는지.
4. author/owner의 의미: 원문 작성/문서 소유인지, 명시적 업무 배정인지 구분.
5. Notion DB 직렬화 예제, PDF provider 접두사와 chunk 경계.
6. 문서 길이·반환 자료 수·응답 시간. 긴 문서는 부분 검토 표시 및 추가 질의가 필요.
7. 저장된 workspace 및 인용 링크의 **현재 권한 재확인 함수**. 아래 ACL bridge 요청/응답은 별도 합의 대상입니다.

현재 응답의 accessStatus만으로 과거에 저장한 문서의 이후 권한 철회를 검출할 수 없습니다. HTTP 모드에서는 workspace/section/people/source/ask 등 저장 콘텐츠 경로 전체를 아래 bridge로 보호합니다. 누락된 설정 또는 실패는 503, 권한 철회는 403으로 전체 응답을 차단합니다. 아직 실제 조직의 ACL 서버를 연결한 것은 아닙니다.

## ACL bridge 제안 — Evidence 필드 변경 없음

운영자가 설정한 HANDOFF_ACCESS_URL에 HTTPS POST (localhost 개발만 HTTP 허용). 검색과 동일한 서비스 Bearer 인증을 사용하며 provider는 scope를 신뢰하기 전에 서비스 인증과 실제 사용자 ACL을 확인해야 합니다.

```json
{
  "requestId": "호출 UUID",
  "scope": {"userId": "kim-juhyung", "teamId": "atlas", "sources": ["slack", "notion", "drive"]},
  "sourceIds": ["notion:account", "slack:C123:1710000000.000"]
}
```

```json
{
  "requestId": "요청의 UUID 그대로",
  "records": [
    {"sourceId": "notion:account", "accessStatus": "accessible"},
    {"sourceId": "slack:C123:1710000000.000", "accessStatus": "restricted"}
  ]
}
```

모든 요청 ID에 정확히 하나의 응답이 필요합니다. accessible/restricted/deleted/unknown만 허용합니다. 하나라도 accessible이 아니면 과거 요약·제목·인용도 반환하지 않습니다. HTTP에서 fixture로 설정을 바꾸더라도 저장된 retrievalMode를 검사하므로 과거 실제 자료의 ACL 검사를 우회하지 않습니다. 원문 본문은 ACL 요청에 전송하지 않습니다.

이 방식은 **fail-closed MVP adapter 제안**이며 최종 Evidence/RetrievalResponse 계약에 새 필수 필드를 넣는 것이 아닙니다. 별도 ACL API 대신 기존 provider의 권한 함수로 연결할 수 있습니다. 삭제·암호화·보존 기간 및 이미 다운로드된 PDF의 회수는 후속 보안 정책입니다. CLI PDF는 실제 자료의 ACL 연결 전 fixture만 허용합니다.

## Synthesis 내부 추출 제한 (수집 스키마 변경 아님)

인물 관계는 같은 원문 구절의 인물/역할/관계/대상 역할 라벨이 있고 대상 역할이 요청 role과 일치할 때만 Person으로 생성합니다. 날짜·상태가 명시된 프로젝트/일정/날짜/상태만 Timeline에 넣습니다. 라벨 없는 자연어는 원문과 질문으로 남기는 보수적 MVP입니다. 원문에 없는 라벨이나 정보를 ingestion에서 AI로 만들어 넣지 마세요.

업무 선행 관계는 원문에 명시된 선행 업무 제목이 현재 Task 목록의 유일한 항목과 일치할 때만 ID를 연결합니다. 동명·누락 대상은 미확정 질문, 순환은 blocked로 표시합니다. 상세 예제와 반례는 test_entities.py를 확인하세요.
