# 공통 계약

- 공개 FE API 원본: ../../apps/web/openapi.yaml. 사본을 추가해 서로 다른 원본을 만들지 않습니다.
- evidence.schema.json / retrieval-response.schema.json: 사용자 확정 handoff의 JSON Schema.
- 실행 모델: ../../services/orchestrator/handoff/contracts.py.
- 테스트: test_contracts.py가 생성본과 실행 모델의 동일성을 검사합니다.
- 상세 연결 책임: ../../docs/data-contract.md.

내부 모델의 recordKey·합성 타입은 사용자 전달 스키마와 별개입니다. 새로운 Evidence 필수 필드를 요구하지 않습니다.
