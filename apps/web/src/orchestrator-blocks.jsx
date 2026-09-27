const names = { FACT: '원문에 명시', INFERENCE: '근거 기반 추론', SUGGESTION: '에이전트 제안', UNKNOWN: '미확정' };
const Badge = ({ type = 'UNKNOWN' }) => <span className={'claim-badge claim-' + type}>{names[type] || type}</span>;
const Field = ({ label, field }) => <div className="task-field"><dt>{label}</dt><dd><Badge type={field?.claimType} /> {field?.value || '확인 필요'}</dd></div>;
export function EvidenceParagraph({ block }) {
  const contents = block.payload.claims?.length ? block.payload.claims.map((c, i) => <p key={i}><Badge type={c.claimType} /> {c.text}</p>) : <p>{block.body}</p>;
  return <article className="content-block prose-block">{block.payload.defaultCollapsed ? <details><summary>원문 근거 펼치기</summary>{contents}</details> : <><h3>{block.title}</h3>{contents}</>}</article>;
}
export function TaskCard({ block }) {
  const p = block.payload;
  return <article className="content-block job-block"><span className="block-kicker">Task Contract · {p.readiness}</span><h3>{block.title}</h3><p>{block.body}</p><dl>
    <Field label="목적" field={p.objective} /><Field label="담당자" field={p.ownerName || p.ownerId} />
    <Field label="기한" field={p.dueAt?.value ? p.dueAt : { value: p.dueText, claimType: p.dueText ? 'FACT' : 'UNKNOWN' }} />
    <Field label="다음 행동" field={p.nextAction} />
  </dl><h4>완료 조건</h4>{p.definitionOfDone?.length ? <ul>{p.definitionOfDone.map((f, i) => <li key={i}><Badge type={f.claimType} /> {f.value}</li>)}</ul> : <p><Badge /> 완료 조건 확인 필요</p>}
  <details><summary>절차 · 의존성 · 차단 요소</summary><ol>{p.steps?.map((s, i) => <li key={i}>{s.action?.value || '확인 필요'}</li>)}</ol>{!p.steps?.length && <p>확인된 절차 없음. 다음 행동과 확인 질문부터 시작하세요.</p>}<p>의존성: {p.dependencies?.map(d => d.title).join(', ') || p.dependencyTaskIds?.join(', ') || '명시된 정보 없음 (없다고 확정하지 않음)'}</p><p>차단 요소: {p.blockers?.map(c => c.text).join(', ') || '명시된 정보 없음'}</p><p>신뢰도: 원문 문자열·근거 일치 검증. 업무 승인이나 담당자 권한을 보증하지 않습니다.</p><h4>미확정 질문</h4><ul>{p.unresolvedQuestions?.map((q, i) => <li key={i}>{q}</li>)}</ul></details></article>;
}
export function ConflictCard({ block }) { const p = block.payload; return <article className="content-block conflict-block"><Badge type="INFERENCE" /><h3>{block.title}</h3><p>상충 가능성 · 최신 내용을 자동 채택하지 않았습니다.</p>{p.rows?.map((r, i) => <blockquote key={i}><strong>{r.label}</strong><p><Badge type={r.claim.claimType} /> {r.claim.text}</p></blockquote>)}<p>{block.body}</p></article>; }
export function GapCard({ block }) { const p = block.payload; return <article className="content-block unknowns-block"><Badge /><h3>{p.question || block.body}</h3><p>{p.whyItMatters}</p><p>확인 대상: {p.suggestedOwnerId || '미확정 — 팀 내 승인·담당 체계를 먼저 확인하세요.'}</p><p>{p.recommendationReason?.text}</p>{p.contactCandidates?.map((c, i) => <p key={i}><Badge type="INFERENCE" /> 질문 후보: {c.name} · {c.basis}</p>)}<details><summary>질문 초안 보기 (전송하지 않음)</summary><blockquote>{p.draftQuestion}</blockquote></details></article>; }
