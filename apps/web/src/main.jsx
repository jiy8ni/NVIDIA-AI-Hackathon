import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './swagger.css';
import './orchestrator.css';
import { TaskCard, ConflictCard, GapCard, EvidenceParagraph } from './orchestrator-blocks.jsx';

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8787';
const USER_ID = import.meta.env.VITE_USER_ID || 'kim-juhyung';
const TEAM_ID = import.meta.env.VITE_TEAM_ID || 'atlas';
const MODE = import.meta.env.VITE_MODEL_MODE || 'offline';
// A multi-search ask with a hosted model can take minutes; the browser must outlast the API's own limit.
const ASK_TIMEOUT_MS = Number(import.meta.env.VITE_ASK_TIMEOUT_MS) || 180000;

async function api(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    ...options, signal: options.signal || AbortSignal.timeout(60000),
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + (import.meta.env.VITE_AUTH_TOKEN || ''), ...(options.headers || {}) },
  });
  const value = await response.json();
  if (!response.ok) throw Object.assign(new Error(value.message || 'API 요청 실패'), { status: response.status });
  return value;
}
const workspacePath = '/v1/onboarding?userId=' + encodeURIComponent(USER_ID);
const modeLabel = MODE === 'nemotron' ? 'Nemotron 모델 모드' : '오프라인 추출 데모 · LLM 미사용';

function App() {
  const [workspace, setWorkspace] = useState(null);
  const [activeSectionId, setActiveSectionId] = useState('home');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [role, setRole] = useState(import.meta.env.VITE_ROLE || '운영 담당자');
  const [theme, setTheme] = useState(() => localStorage.getItem('onboarding-theme') || 'dark');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('onboarding-sidebar-collapsed') === 'true');
  const [question, setQuestion] = useState('');
  const [selectedText, setSelectedText] = useState(null);
  const [assistant, setAssistant] = useState({ open: false, loading: false, answer: null });
  const [refreshing, setRefreshing] = useState(false);
  const [pollEpoch, setPollEpoch] = useState(0);
  const loadWorkspace = async () => {
    setLoading(true); setError('');
    try { setWorkspace(await api(workspacePath)); setPollEpoch(x => x + 1); }
    catch (e) { if (e.status !== 404) setError(e.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { loadWorkspace(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('onboarding-theme', theme); }, [theme]);
  useEffect(() => { localStorage.setItem('onboarding-sidebar-collapsed', String(sidebarCollapsed)); }, [sidebarCollapsed]);
  useEffect(() => {
    if (workspace?.status !== 'generating') return;
    let cancelled = false, timer;
    const deadline = Date.now() + 210000;
    const poll = async () => {
      try {
        const data = await api(workspacePath);
        if (cancelled) return;
        setWorkspace(data);
        if (data.status === 'failed') { setError('생성에 실패했습니다. 기존 문서는 보존됩니다. 설정을 확인하고 다시 생성하세요.'); return; }
        if (data.status !== 'generating') return;
        if (Date.now() >= deadline) { setError('자동 상태 확인을 중단했습니다. 상태 다시 확인 버튼을 누르세요.'); return; }
        timer = setTimeout(poll, 2000);
      } catch (e) { if (!cancelled) setError(e.message); }
    };
    timer = setTimeout(poll, 100);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [workspace?.status, pollEpoch]);

  const activeSection = useMemo(() => workspace?.sections.find(s => s.id === activeSectionId) || workspace?.sections[0], [workspace, activeSectionId]);
  const busy = refreshing || workspace?.status === 'generating';
  const chooseSection = async id => {
    setActiveSectionId(id); setSelectedText(null);
    try {
      const section = await api('/v1/onboarding/sections/' + id);
      setWorkspace(current => ({ ...current, sections: current.sections.map(s => s.id === id ? section : s) }));
      if (!busy) {
        const progress = await api('/v1/onboarding/progress', { method: 'PATCH', body: JSON.stringify({ currentSectionId: id }) });
        setWorkspace(current => ({ ...current, progress }));
      }
    } catch (e) { setError(e.message); }
  };
  const toggleChecklist = async item => {
    try {
      const result = await api('/v1/onboarding/checklist/' + encodeURIComponent(item.id), { method: 'PATCH', body: JSON.stringify({ completed: !item.completed }) });
      setWorkspace(current => ({ ...current, progress: result.progress }));
    } catch (e) { setError(e.message); }
  };
  const generateWorkspace = async () => {
    setRefreshing(true); setError('');
    try {
      const job = await api('/v1/onboarding/generate', { method: 'POST', body: JSON.stringify({ userId: USER_ID, role, teamId: TEAM_ID, sourceScope: ['slack', 'notion', 'drive'], refresh: true }) });
      if (!job.pollUrl.startsWith('/v1/onboarding?')) throw Error('예상하지 못한 polling 주소입니다.');
      setWorkspace(await api(job.pollUrl)); setPollEpoch(x => x + 1);
    } catch (e) { setError(e.message); }
    finally { setRefreshing(false); }
  };
  const askAssistant = async event => {
    event?.preventDefault();
    if (!question.trim()) { setAssistant({ open: true, loading: false, answer: null }); return; }
    if (busy || assistant.loading) return;
    setAssistant({ open: true, loading: true, answer: null });
    try {
      const answer = await api('/v1/onboarding/ask', { method: 'POST', body: JSON.stringify({ question, context: { onboardingId: workspace.id, currentPath: '/onboarding', sectionId: activeSectionId === 'home' ? null : activeSectionId, selectedText, entityIds: [] } }), signal: AbortSignal.timeout(ASK_TIMEOUT_MS) });
      setAssistant({ open: true, loading: false, answer });
    } catch (e) { setAssistant({ open: true, loading: false, answer: { answer: e.message, citations: [], suggestedQuestions: [] } }); }
  };
  const feedback = async block => {
    try { await api('/v1/onboarding/feedback', { method: 'POST', body: JSON.stringify({ targetType: 'section_block', targetId: block.id, rating: 'needs-review' }) }); setError('검토 요청을 저장했습니다. 원문을 자동 수정하지 않습니다.'); }
    catch (e) { setError(e.message); }
  };
  if (loading) return <LoadingState />;
  if (error && !workspace) return <ErrorState message={error} retry={loadWorkspace} />;
  if (!workspace) return <div className="error-screen"><section className="error-card start-card"><p className="eyebrow">HandoffOS</p><h1>내 역할의 맥락부터</h1><p>{modeLabel} · 가상 조직 자료</p><label htmlFor="role-input">내 역할</label><input id="role-input" value={role} maxLength={200} onChange={e => setRole(e.target.value)} /><button className="primary-button" disabled={refreshing || !role.trim()} onClick={generateWorkspace}>{refreshing ? '접수 중…' : '온보딩 생성 시작'}</button></section></div>;
  const progress = workspace.progress;
  return <div className={'app-shell ' + (sidebarCollapsed ? 'sidebar-collapsed' : '')}>
    <Sidebar sections={workspace.sections} activeSectionId={activeSectionId} progress={progress} onChoose={chooseSection} onHome={() => { setActiveSectionId('home'); setSelectedText(null); }} collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed(x => !x)} />
    <main className="main-column">
      <header className="topbar"><div className="breadcrumbs"><strong>HandoffOS</strong><span> / 온보딩</span></div><div className="topbar-actions"><button className="icon-button" aria-label="테마 전환" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>☼</button><button className="refresh-button" onClick={generateWorkspace} disabled={busy}>{busy ? '근거 검토 중…' : '컨텍스트 새로고침'}</button><button className="refresh-button" onClick={loadWorkspace}>상태 다시 확인</button></div></header>
      <div className="page-content">
        <div className="mode-banner">{modeLabel} · {import.meta.env.VITE_RETRIEVAL_MODE === 'http' ? '실제 검색 어댑터' : '가상 조직 fixture'} · 외부 서비스 읽기 전용</div>
        {error && <div className="inline-error" role="alert">{error}<button onClick={() => setError('')}>닫기</button></div>}
        {workspace.status === 'failed' && <p role="alert">마지막 생성에 실패했습니다. 보이는 내용은 이전 성공 결과일 수 있습니다.</p>}
        {activeSectionId === 'home' ? <><section className="hero-row"><div className="hero-copy"><p className="eyebrow">{USER_ID} · {role}</p><h1>맥락부터 이해하고,<br /><em>자신 있게 시작하세요.</em></h1><p className="hero-description">회사와 역할, 지금 할 일, 확인할 질문을 필요한 만큼 펼쳐보세요.</p><div className="hero-meta"><span className="status-mark" /><span>{busy ? '자료 수집·합성·검증 중 (체크리스트 진행률과 별개)' : workspace.status === 'ready' ? '근거 검증 결과 준비 완료' : '재확인 필요'}</span></div></div><ProgressOrb percent={progress.percent} completedLabel={progress.completed + '/' + progress.total + ' 완료'} /></section><LandingOverview workspace={workspace} onChoose={chooseSection} progress={progress} /></> :
        <section className="workspace-layout"><div className="section-panel"><div className="section-header"><div><p className="section-index">{activeSection.number} / 5</p><h2>{activeSection.title}</h2><p>{activeSection.description}</p></div><span className={'section-status ' + activeSection.status}>{statusLabel(activeSection.status)}</span></div>
        <div className="section-content-layout" onMouseUp={() => { const selection = window.getSelection()?.toString().trim(); if (selection) setSelectedText(selection.slice(0, 320)); }}><SectionOutline section={activeSection} /><div className="block-stack">{activeSection.blocks.map(block => <div className="content-block-anchor" id={block.id} key={block.id}><BlockRenderer block={block} workspace={workspace} onToggleChecklist={toggleChecklist} /><div className="block-evidence">{block.sourceIds?.map(id => { const s = workspace.sources.find(x => x.id === id); return s ? <SourceChip key={id} source={s} /> : null; })}<button disabled={busy} className="review-button" onClick={() => feedback(block)}>검토 요청</button></div></div>)}</div></div></div></section>}
        {selectedText && <div className="selection-context">질문에 포함할 선택 문맥: {selectedText}<button onClick={() => setSelectedText(null)}>해제</button></div>}
        <AssistantCard onSubmit={() => setAssistant(s => ({ ...s, open: true }))} />
      </div>
    </main>
    {assistant.open && <AssistantDialog state={assistant} question={question} setQuestion={setQuestion} onSubmit={askAssistant} onClose={() => setAssistant(s => ({ ...s, open: false }))} />}
  </div>;
}

function Sidebar({ sections, activeSectionId, progress, onChoose, onHome, collapsed, onToggle }) { return <aside className="sidebar"><div className="brand"><div className="brand-mark">n</div><div className="brand-copy"><strong>HandoffOS</strong><span>온보딩 인텔리전스</span></div><button className="sidebar-toggle" onClick={onToggle} aria-label={collapsed ? '사이드바 펼치기' : '사이드바 접기'} aria-expanded={!collapsed}>{collapsed ? '›' : '‹'}</button></div><div className="sidebar-heading">온보딩 여정</div><nav className="section-nav" aria-label="온보딩 섹션"><button className={`nav-item home-nav ${activeSectionId === 'home' ? 'active' : ''}`} onClick={onHome} title="전체 보기"><span className="nav-number">⌂</span><span className="nav-copy"><strong>전체 보기</strong><small>온보딩 홈</small></span><span className="nav-state complete" aria-hidden="true" /></button>{sections.map((section) => <button key={section.id} className={`nav-item ${activeSectionId === section.id ? 'active' : ''}`} onClick={() => onChoose(section.id)} title={section.title}><span className="nav-number">{section.number}</span><span className="nav-copy"><strong>{section.title}</strong><small>{statusLabel(section.status)}</small></span><span className={`nav-state ${section.status}`} aria-hidden="true" /></button>)}</nav><div className="sidebar-bottom"><div className="mini-progress"><div className="mini-progress-top"><span>온보딩 진행률</span><strong>{progress.percent}%</strong></div><div className="progress-track"><span style={{ width: `${progress.percent}%` }} /></div></div><div className="sidebar-user"><div className="profile-avatar small">MC</div><div className="sidebar-user-copy"><strong>{USER_ID}</strong><span>신규 구성원</span></div><span className="chevron">⌄</span></div></div></aside>; }
function LandingOverview({ workspace, onChoose, progress }) { return <section className="landing-overview"><div className="landing-intro"><span className="block-kicker">이번 온보딩의 지도</span><h2>어디서부터 볼까요?</h2><p>왼쪽의 섹션을 열어 회사, 팀, 프로젝트, 설정에 대한 맥락을 차례로 확인하세요.</p><div className="landing-progress-note"><span className="status-mark" /><span>{progress.completed}개 항목 완료</span><span className="meta-divider" /><span>{workspace.sections.flatMap(s => s.blocks).filter(b => b.type === 'unknown-card').length}개 질문 대기 중</span></div></div><div className="landing-section-grid">{workspace.sections.map((section) => <button className="landing-section-card" key={section.id} onClick={() => onChoose(section.id)}><div className="landing-card-top"><span>{section.number}</span><span className={`section-status ${section.status}`}>{statusLabel(section.status)}</span></div><h3>{section.title}</h3><p>{section.description}</p><span className="landing-card-link">열어보기 <span>↗</span></span></button>)}</div></section>; }
function SectionOutline({ section }) { return <aside className="section-outline"><span className="outline-label">이 섹션에서</span><nav aria-label={`${section.title} 목차`}>{section.blocks.map((block, index) => <a key={block.id} href={`#${block.id}`}><span>0{index + 1}</span><strong>{block.title}</strong></a>)}</nav></aside>; }
function ProgressOrb({ percent, completedLabel }) { return <div className="progress-orb-wrap"><div className="progress-orb" style={{ '--progress': `${percent * 3.6}deg` }}><div className="orb-inner"><strong>{percent}%</strong><span>진행 중</span></div></div><div className="orb-caption"><strong>{completedLabel}</strong><span>좋은 흐름을 이어가세요.</span></div></div>; }

function BlockRenderer({ block, workspace, onToggleChecklist }) {
  const payload = block.payload || {};
  if (block.type === 'paragraph') return <EvidenceParagraph block={block} />;
  if (block.type === 'callout') return <article className="content-block callout-block"><div className="callout-mark">↗</div><div><h3>{block.title}</h3><p>{block.body}</p></div></article>;
  if (block.type === 'job') return <TaskCard block={block} />;
  if (block.type === 'person-card') return <article className="content-block"><div className="block-heading"><div><span className="block-kicker">협업 파트너</span><h3>{block.title}</h3></div></div><div className="people-inline">{(payload.personIds || []).map((id) => { const person = workspace.people.find((entry) => entry.id === id); return person ? <PersonRow key={person.id} person={person} compact /> : null; })}</div></article>;
  if (block.type === 'process-flow') return <article className="content-block"><div className="block-heading"><div><span className="block-kicker">일하는 방식</span><h3>{block.title}</h3></div></div><p>{block.body}</p><div className="process-flow">{(payload.steps || []).map((step, index) => <div className="process-step" key={index}><span>{String(index + 1).padStart(2, '0')}</span><strong>{typeof step === 'string' ? step : step.action?.value || '미확정 단계'}</strong>{index < payload.steps.length - 1 && <i>→</i>}</div>)}</div></article>;
  if (block.type === 'timeline') { const timeline = workspace.timelines.find((entry) => entry.projectId === payload.projectId); return <TimelineBlock timeline={timeline} />; }
  if (block.type === 'checklist') return <ChecklistBlock block={block} progress={workspace.progress} onToggle={onToggleChecklist} />;
  if (block.type === 'unknown-card') return <GapCard block={block} />;
  if (block.type === 'comparison') return <ConflictCard block={block} />;
  return <article className="content-block"><h3>{block.title}</h3><p>{block.body}</p></article>;
}

function TimelineBlock({ timeline }) { if (!timeline) return null; return <article className="content-block timeline-block"><div className="block-heading"><div><span className="block-kicker">확인된 일정 범위 · 프로젝트 전체 기간 아님</span><h3>{timeline.title}</h3></div><span className="date-range">{timeline.startDate} - {timeline.endDate}</span></div><div className="timeline-list">{timeline.milestones.map((milestone) => <div className="timeline-item" key={milestone.id}><span className={`timeline-dot ${milestone.status}`} /><div><strong>{milestone.title}</strong><span>{formatDate(milestone.date)}</span></div><small className={milestone.status}>{milestoneStatus(milestone.status)}</small></div>)}</div></article>; }
function ChecklistBlock({ block, progress, onToggle }) { const items = progress.checklist.filter((item) => block.payload.itemIds.includes(item.id)); return <article className="content-block checklist-block"><div className="block-heading"><div><span className="block-kicker">첫 주 체크리스트</span><h3>{block.title}</h3></div><span className="check-count">{items.filter((item) => item.completed).length}/{items.length}</span></div><p>{block.body}</p><div className="checklist">{items.map((item) => <button className={`check-row ${item.completed ? 'done' : ''}`} key={item.id} onClick={() => onToggle(item)}><span className="check-box">{item.completed ? '✓' : ''}</span><span>{item.label}</span><small>{item.completed ? '완료' : '진행 전'}</small></button>)}</div></article>; }
function PersonRow({ person, compact = false }) { const href = person.links?.[0]?.url || '#'; return <a className={`person-row ${compact ? 'compact' : ''}`} href={href} target="_blank" rel="noreferrer" title={`${person.name}의 ${person.links?.[0]?.provider || '프로필'} 열기`}><div className="person-avatar">{initials(person.name)}</div><div className="person-copy"><strong>{person.name}</strong><span>{person.role}</span></div>{!compact && <span className="relationship">{relationshipLabel(person.relationship)}</span>}<span className="person-link-mark" aria-hidden="true">↗</span></a>; }
function UnknownBlock({ unknowns, people }) { return <article className="content-block unknowns-block"><div className="unknown-grid">{unknowns.map((unknown) => <div className="unknown-card" key={unknown.id}><div className="unknown-card-top"><span className="question-mark">?</span><span className="open-label">미해결</span></div><h3>{unknown.question}</h3><p>{unknown.whyItMatters}</p><div className="suggested-owner"><div className="person-avatar mini">{initials(people.find((person) => person.id === unknown.suggestedOwnerId)?.name || '')}</div><span><strong>{people.find((person) => person.id === unknown.suggestedOwnerId)?.name}</strong>에게 질문</span></div></div>)}</div></article>; }
function AssistantCard({ question, setQuestion, onSubmit }) { return <button className="assistant-fab" onClick={onSubmit} aria-label="온보딩 어시스턴트 열기"><span className="assistant-orb">✦</span><span>온보딩 어시스턴트</span></button>; }
function AssistantDialog({ state, question, setQuestion, onSubmit, onClose }) { return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="assistant-dialog" role="dialog" aria-modal="true" aria-labelledby="assistant-title"><div className="dialog-top"><div><span className="block-kicker">컨텍스트 어시스턴트</span><h2 id="assistant-title">워크스페이스에 질문하기</h2></div><button className="close-button" onClick={onClose} aria-label="어시스턴트 닫기">×</button></div>{state.loading ? <div className="assistant-loading"><span className="loading-pulse" /><p>워크스페이스의 맥락을 읽고 있습니다...</p></div> : state.answer && <div className="answer"><p>{state.answer.answer}</p>{state.answer.citations?.length > 0 && <div className="citation-row">{state.answer.citations.map((citation, index) => <a key={citation.id + ':' + index} href={safeHref(citation.url)} target="_blank" rel="noreferrer" title={citation.quotedContext}>[{index + 1}] {providerLabel(citation.provider)}: {citation.title}</a>)}</div>}<div className="suggested-questions">{state.answer.suggestedQuestions?.map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}</button>)}</div></div>}<form className="dialog-form" onSubmit={onSubmit}><input autoFocus value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="추가로 궁금한 내용을 입력하세요" /><button disabled={state.loading || !question.trim()}>질문하기</button></form></section></div>; }
function safeHref(value) { try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? value : undefined; } catch { return undefined; } }
function SourceChip({ source }) { return <a className="source-chip" href={source.access === 'available' ? safeHref(source.url) : undefined} target="_blank" rel="noreferrer"><span className={`source-icon ${source.provider}`}>{source.provider === 'notion' ? 'N' : source.provider === 'slack' ? 'S' : source.provider === 'google_drive' ? 'G' : 'I'}</span><span>{source.title}</span><span className="external">↗</span></a>; }
function LoadingState() { return <div className="loading-screen"><div className="loading-brand"><div className="brand-mark">n</div><strong>컨텍스트를 불러오는 중</strong><span>온보딩 그래프에 연결하고 있습니다...</span></div><div className="loading-lines"><i /><i /><i /></div></div>; }
function ErrorState({ message, retry }) { return <div className="error-screen"><div className="error-card"><div className="error-mark">!</div><h1>워크스페이스를 사용할 수 없습니다</h1><p>{message}</p><button className="primary-button" onClick={retry}>다시 시도</button></div></div>; }
function initials(name) { const parts = name.trim().split(' '); return parts.length > 1 ? parts.map((part) => part[0]).join('').slice(0, 2) : name.slice(0, 2); }
function relationshipLabel(value) { return ({ manager: '매니저', teammate: '팀 동료', collaborator: '협업 파트너', 'subject-matter-expert': '도메인 전문가' })[value] || value; }
function statusLabel(value) { return value === 'in-progress' ? '진행 중' : value === 'complete' ? '완료' : '잠김'; }
function milestoneStatus(value) { return value === 'done' ? '완료' : value === 'next' ? '다음' : value === 'blocked' ? '보류' : '예정'; }
function providerLabel(value) { return value === 'google_drive' ? 'Drive' : value === 'internal' ? '내부 문서' : value[0].toUpperCase() + value.slice(1); }
function formatDate(value) { return new Date(`${value}T12:00:00`).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' }); }

createRoot(document.getElementById('root')).render(<App />);
