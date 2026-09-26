import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './swagger.css';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8787';

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'content-type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.status === 204 ? null : response.json();
}

function App() {
  const [workspace, setWorkspace] = useState(null);
  const [activeSectionId, setActiveSectionId] = useState('home');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [theme, setTheme] = useState(() => localStorage.getItem('onboarding-theme') || 'dark');
  const [question, setQuestion] = useState('');
  const [assistant, setAssistant] = useState({ open: false, loading: false, answer: null });
  const [refreshing, setRefreshing] = useState(false);

  const loadWorkspace = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api('/v1/onboarding?userId=maya-chen&include=sections,people,timelines,unknowns,sources,checklist');
      setWorkspace(data);
      setActiveSectionId('home');
    } catch (requestError) {
      setError('워크스페이스에 연결할 수 없습니다. 8787 포트에서 API를 실행한 뒤 다시 시도해 주세요.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadWorkspace(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('onboarding-theme', theme); }, [theme]);

  const activeSection = useMemo(() => workspace?.sections.find((section) => section.id === activeSectionId) || workspace?.sections[0], [workspace, activeSectionId]);
  const isLanding = activeSectionId === 'home';

  const chooseSection = async (sectionId) => {
    setActiveSectionId(sectionId);
    try {
      const progress = await api('/v1/onboarding/progress', { method: 'PATCH', body: JSON.stringify({ currentSectionId: sectionId }) });
      setWorkspace((current) => ({ ...current, progress }));
    } catch { /* Navigation remains useful if a progress write is unavailable. */ }
  };

  const goHome = () => setActiveSectionId('home');

  const toggleChecklist = async (item) => {
    try {
      const result = await api(`/v1/onboarding/checklist/${item.id}`, { method: 'PATCH', body: JSON.stringify({ completed: !item.completed }) });
      setWorkspace((current) => ({ ...current, progress: result.progress }));
    } catch { setError('체크리스트를 저장하지 못했습니다. 다시 시도해 주세요.'); }
  };

  const generateWorkspace = async () => {
    setRefreshing(true);
    try {
      await api('/v1/onboarding/generate', { method: 'POST', body: JSON.stringify({ userId: 'maya-chen', role: 'Product engineer', teamId: 'atlas', sourceScope: ['slack', 'notion', 'drive', 'internal'], refresh: true }) });
      window.setTimeout(() => setRefreshing(false), 850);
    } catch { setRefreshing(false); setError('새로고침을 등록하지 못했습니다.'); }
  };

  const askAssistant = async (event) => {
    event?.preventDefault();
    if (!question.trim()) {
      setAssistant({ open: true, loading: false, answer: null });
      return;
    }
    setAssistant({ open: true, loading: true, answer: null });
    try {
      const answer = await api('/v1/onboarding/ask', { method: 'POST', body: JSON.stringify({ question, context: { onboardingId: workspace.id, currentPath: '/onboarding', sectionId: activeSectionId, selectedText: null, entityIds: [] } }) });
      setAssistant({ open: true, loading: false, answer });
    } catch { setAssistant({ open: true, loading: false, answer: { answer: '지금은 어시스턴트를 사용할 수 없습니다. Mock API가 실행 중인지 확인해 주세요.', citations: [], suggestedQuestions: [] } }); }
  };

  if (loading) return <LoadingState />;
  if (error && !workspace) return <ErrorState message={error} retry={loadWorkspace} />;
  const progress = workspace.progress;

  return <div className="app-shell">
    <Sidebar sections={workspace.sections} activeSectionId={activeSectionId} progress={progress} onChoose={chooseSection} onHome={goHome} />
    <main className="main-column">
      <header className="topbar"><div className="breadcrumbs"><span>워크스페이스</span><span className="crumb-slash">/</span><strong>온보딩</strong></div><div className="topbar-actions"><button className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="테마 전환"><span aria-hidden="true">{theme === 'dark' ? '☼' : '◐'}</span></button><button className="refresh-button" onClick={generateWorkspace} disabled={refreshing}><span className={refreshing ? 'spin' : ''} aria-hidden="true">↻</span>{refreshing ? '새로고침 중' : '컨텍스트 새로고침'}</button><div className="profile-avatar" aria-label="마야 첸">MC</div></div></header>
      <div className="page-content">
        {error && <div className="inline-error" role="alert">{error}<button onClick={() => setError('')}>닫기</button></div>}
        {isLanding ? <><section className="hero-row"><div className="hero-copy"><p className="eyebrow">나의 온보딩 워크스페이스</p><h1>맥락부터 이해하고,<br /><em>자신 있게 시작하세요.</em></h1><p className="hero-description">첫 기여를 더 유용하게 만드는 사람, 프로젝트, 의사결정을 한곳에서 확인하세요.</p><div className="hero-meta"><span className="status-mark" /><span>컨텍스트 준비 완료</span><span className="meta-divider" /><span>방금 업데이트됨</span></div></div><ProgressOrb percent={progress.percent} completedLabel={`${progress.completed}/${progress.total} 완료`} /></section><LandingOverview workspace={workspace} onChoose={chooseSection} progress={progress} /></> : <section className="workspace-layout"><div className="section-panel"><div className="section-header"><div><p className="section-index">{activeSection.number} / {workspace.sections.length}</p><h2>{activeSection.title}</h2><p>{activeSection.description}</p></div><span className={`section-status ${activeSection.status}`}>{statusLabel(activeSection.status)}</span></div><div className="section-content-layout"><SectionOutline section={activeSection} /><div className="block-stack">{activeSection.blocks.map((block) => <div className="content-block-anchor" id={block.id} key={block.id}><BlockRenderer block={block} workspace={workspace} onToggleChecklist={toggleChecklist} /></div>)}</div></div><div className="source-footer"><span className="source-label">연결된 컨텍스트</span><div className="source-list">{activeSection.sources.map((source) => <SourceChip key={source.id} source={source} />)}</div></div></div></section>}
        <AssistantCard onSubmit={askAssistant} />
      </div>
    </main>
    {assistant.open && <AssistantDialog state={assistant} question={question} setQuestion={setQuestion} onSubmit={askAssistant} onClose={() => setAssistant({ open: false, loading: false, answer: null })} />}
  </div>;
}

function Sidebar({ sections, activeSectionId, progress, onChoose, onHome }) { return <aside className="sidebar"><div className="brand"><div className="brand-mark">n</div><div><strong>northstar</strong><span>온보딩 인텔리전스</span></div></div><div className="sidebar-heading">온보딩 여정</div><nav className="section-nav" aria-label="온보딩 섹션"><button className={`nav-item home-nav ${activeSectionId === 'home' ? 'active' : ''}`} onClick={onHome}><span className="nav-number">⌂</span><span className="nav-copy"><strong>전체 보기</strong><small>온보딩 홈</small></span><span className="nav-state complete" aria-hidden="true" /></button>{sections.map((section) => <button key={section.id} className={`nav-item ${activeSectionId === section.id ? 'active' : ''}`} onClick={() => onChoose(section.id)}><span className="nav-number">{section.number}</span><span className="nav-copy"><strong>{section.title}</strong><small>{statusLabel(section.status)}</small></span><span className={`nav-state ${section.status}`} aria-hidden="true" /></button>)}</nav><div className="sidebar-bottom"><div className="mini-progress"><div className="mini-progress-top"><span>온보딩 진행률</span><strong>{progress.percent}%</strong></div><div className="progress-track"><span style={{ width: `${progress.percent}%` }} /></div></div><div className="sidebar-user"><div className="profile-avatar small">MC</div><div><strong>마야 첸</strong><span>프로덕트 엔지니어</span></div><span className="chevron">⌄</span></div></div></aside>; }
function LandingOverview({ workspace, onChoose, progress }) { return <section className="landing-overview"><div className="landing-intro"><span className="block-kicker">이번 온보딩의 지도</span><h2>어디서부터 볼까요?</h2><p>왼쪽의 섹션을 열어 회사, 팀, 프로젝트, 설정에 대한 맥락을 차례로 확인하세요.</p><div className="landing-progress-note"><span className="status-mark" /><span>{progress.completed}개 항목 완료</span><span className="meta-divider" /><span>{workspace.unknowns.length}개 질문 대기 중</span></div></div><div className="landing-section-grid">{workspace.sections.map((section) => <button className="landing-section-card" key={section.id} onClick={() => onChoose(section.id)}><div className="landing-card-top"><span>{section.number}</span><span className={`section-status ${section.status}`}>{statusLabel(section.status)}</span></div><h3>{section.title}</h3><p>{section.description}</p><span className="landing-card-link">열어보기 <span>↗</span></span></button>)}</div></section>; }
function SectionOutline({ section }) { return <aside className="section-outline"><span className="outline-label">이 섹션에서</span><nav aria-label={`${section.title} 목차`}>{section.blocks.map((block, index) => <a key={block.id} href={`#${block.id}`}><span>0{index + 1}</span><strong>{block.title}</strong></a>)}</nav></aside>; }
function ProgressOrb({ percent, completedLabel }) { return <div className="progress-orb-wrap"><div className="progress-orb" style={{ '--progress': `${percent * 3.6}deg` }}><div className="orb-inner"><strong>{percent}%</strong><span>진행 중</span></div></div><div className="orb-caption"><strong>{completedLabel}</strong><span>좋은 흐름을 이어가세요.</span></div></div>; }

function BlockRenderer({ block, workspace, onToggleChecklist }) {
  const payload = block.payload || {};
  if (block.type === 'paragraph') return <article className="content-block prose-block"><h3>{block.title}</h3><p>{block.body}</p><ul>{(payload.bullets || []).map((bullet) => <li key={bullet}>{bullet}</li>)}</ul></article>;
  if (block.type === 'callout') return <article className="content-block callout-block"><div className="callout-mark">↗</div><div><h3>{block.title}</h3><p>{block.body}</p></div></article>;
  if (block.type === 'job') return <article className="content-block job-block"><div className="block-heading"><div><span className="block-kicker">역할 요약</span><h3>{block.title}</h3></div><span className="job-badge">Atlas</span></div><p>{block.body}</p><div className="outcome-grid">{(payload.outcomes || []).map((outcome, index) => <div className="outcome" key={outcome}><span>0{index + 1}</span><strong>{outcome}</strong></div>)}</div></article>;
  if (block.type === 'person-card') return <article className="content-block"><div className="block-heading"><div><span className="block-kicker">협업 파트너</span><h3>{block.title}</h3></div></div><div className="people-inline">{(payload.personIds || []).map((id) => { const person = workspace.people.find((entry) => entry.id === id); return person ? <PersonRow key={person.id} person={person} compact /> : null; })}</div></article>;
  if (block.type === 'process-flow') return <article className="content-block"><div className="block-heading"><div><span className="block-kicker">일하는 방식</span><h3>{block.title}</h3></div></div><p>{block.body}</p><div className="process-flow">{(payload.steps || []).map((step, index) => <div className="process-step" key={step}><span>{String(index + 1).padStart(2, '0')}</span><strong>{step}</strong>{index < payload.steps.length - 1 && <i>→</i>}</div>)}</div></article>;
  if (block.type === 'timeline') { const timeline = workspace.timelines.find((entry) => entry.projectId === payload.projectId); return <TimelineBlock timeline={timeline} />; }
  if (block.type === 'checklist') return <ChecklistBlock block={block} progress={workspace.progress} onToggle={onToggleChecklist} />;
  if (block.type === 'unknown-card') return <UnknownBlock unknowns={workspace.unknowns} people={workspace.people} />;
  return <article className="content-block"><h3>{block.title}</h3><p>{block.body}</p></article>;
}

function TimelineBlock({ timeline }) { if (!timeline) return null; return <article className="content-block timeline-block"><div className="block-heading"><div><span className="block-kicker">프로젝트 타임라인</span><h3>{timeline.title}</h3></div><span className="date-range">3월 30일 - 4월 24일</span></div><div className="timeline-list">{timeline.milestones.map((milestone) => <div className="timeline-item" key={milestone.id}><span className={`timeline-dot ${milestone.status}`} /><div><strong>{milestone.title}</strong><span>{formatDate(milestone.date)}</span></div><small className={milestone.status}>{milestoneStatus(milestone.status)}</small></div>)}</div></article>; }
function ChecklistBlock({ block, progress, onToggle }) { const items = progress.checklist.filter((item) => block.payload.itemIds.includes(item.id)); return <article className="content-block checklist-block"><div className="block-heading"><div><span className="block-kicker">첫 주 체크리스트</span><h3>{block.title}</h3></div><span className="check-count">{items.filter((item) => item.completed).length}/{items.length}</span></div><p>{block.body}</p><div className="checklist">{items.map((item) => <button className={`check-row ${item.completed ? 'done' : ''}`} key={item.id} onClick={() => onToggle(item)}><span className="check-box">{item.completed ? '✓' : ''}</span><span>{item.label}</span><small>{item.completed ? '완료' : '진행 전'}</small></button>)}</div></article>; }
function PersonRow({ person, compact = false }) { const href = person.links?.[0]?.url || '#'; return <a className={`person-row ${compact ? 'compact' : ''}`} href={href} target="_blank" rel="noreferrer" title={`${person.name}의 ${person.links?.[0]?.provider || '프로필'} 열기`}><div className="person-avatar">{initials(person.name)}</div><div className="person-copy"><strong>{person.name}</strong><span>{person.role}</span></div>{!compact && <span className="relationship">{relationshipLabel(person.relationship)}</span>}<span className="person-link-mark" aria-hidden="true">↗</span></a>; }
function UnknownBlock({ unknowns, people }) { return <article className="content-block unknowns-block"><div className="unknown-grid">{unknowns.map((unknown) => <div className="unknown-card" key={unknown.id}><div className="unknown-card-top"><span className="question-mark">?</span><span className="open-label">미해결</span></div><h3>{unknown.question}</h3><p>{unknown.whyItMatters}</p><div className="suggested-owner"><div className="person-avatar mini">{initials(people.find((person) => person.id === unknown.suggestedOwnerId)?.name || '')}</div><span><strong>{people.find((person) => person.id === unknown.suggestedOwnerId)?.name}</strong>에게 질문</span></div></div>)}</div></article>; }
function AssistantCard({ question, setQuestion, onSubmit }) { return <button className="assistant-fab" onClick={onSubmit} aria-label="온보딩 어시스턴트 열기"><span className="assistant-orb">✦</span><span>온보딩 어시스턴트</span></button>; }
function AssistantDialog({ state, question, setQuestion, onSubmit, onClose }) { return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="assistant-dialog" role="dialog" aria-modal="true" aria-labelledby="assistant-title"><div className="dialog-top"><div><span className="block-kicker">컨텍스트 어시스턴트</span><h2 id="assistant-title">워크스페이스에 질문하기</h2></div><button className="close-button" onClick={onClose} aria-label="어시스턴트 닫기">×</button></div>{state.loading ? <div className="assistant-loading"><span className="loading-pulse" /><p>워크스페이스의 맥락을 읽고 있습니다...</p></div> : state.answer && <div className="answer"><p>{state.answer.answer}</p>{state.answer.citations?.length > 0 && <div className="citation-row">{state.answer.citations.map((citation) => <span key={citation.id}>{providerLabel(citation.provider)}: {citation.title}</span>)}</div>}<div className="suggested-questions">{state.answer.suggestedQuestions?.map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}</button>)}</div></div>}<form className="dialog-form" onSubmit={onSubmit}><input autoFocus value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="추가로 궁금한 내용을 입력하세요" /><button>질문하기</button></form></section></div>; }
function SourceChip({ source }) { return <a className="source-chip" href={source.url} target="_blank" rel="noreferrer"><span className={`source-icon ${source.provider}`}>{source.provider === 'notion' ? 'N' : source.provider === 'slack' ? 'S' : source.provider === 'google_drive' ? 'G' : 'I'}</span><span>{source.title}</span><span className="external">↗</span></a>; }
function LoadingState() { return <div className="loading-screen"><div className="loading-brand"><div className="brand-mark">n</div><strong>컨텍스트를 불러오는 중</strong><span>온보딩 그래프에 연결하고 있습니다...</span></div><div className="loading-lines"><i /><i /><i /></div></div>; }
function ErrorState({ message, retry }) { return <div className="error-screen"><div className="error-card"><div className="error-mark">!</div><h1>워크스페이스를 사용할 수 없습니다</h1><p>{message}</p><button className="primary-button" onClick={retry}>다시 시도</button></div></div>; }
function initials(name) { const parts = name.trim().split(' '); return parts.length > 1 ? parts.map((part) => part[0]).join('').slice(0, 2) : name.slice(0, 2); }
function relationshipLabel(value) { return ({ manager: '매니저', teammate: '팀 동료', collaborator: '협업 파트너', 'subject-matter-expert': '도메인 전문가' })[value] || value; }
function statusLabel(value) { return value === 'in-progress' ? '진행 중' : value === 'complete' ? '완료' : '잠김'; }
function milestoneStatus(value) { return value === 'done' ? '완료' : value === 'next' ? '다음' : value === 'blocked' ? '보류' : '예정'; }
function providerLabel(value) { return value === 'google_drive' ? 'Drive' : value === 'internal' ? '내부 문서' : value[0].toUpperCase() + value.slice(1); }
function formatDate(value) { return new Date(`${value}T12:00:00`).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' }); }

createRoot(document.getElementById('root')).render(<App />);
