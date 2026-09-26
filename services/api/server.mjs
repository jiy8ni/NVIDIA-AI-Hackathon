import { createServer } from 'node:http';

const port = Number(process.env.PORT || 8787);

const sourceLinks = [
  {
    id: 'src-notion-product',
    type: 'notion_page',
    title: '제품 원칙과 로드맵',
    url: 'https://notion.so/northstar/product-principles',
    provider: 'notion',
    access: 'available',
    quotedContext: 'Northstar는 빠른 피드백 루프와 명확한 오너십을 중요하게 생각합니다.'
  },
  {
    id: 'src-slack-launch',
    type: 'slack_thread',
    title: '#launchpad: Atlas 출시 노트',
    url: 'https://slack.com/archives/launchpad/p1710000000000000',
    provider: 'slack',
    access: 'available',
    quotedContext: '첫 번째 고객 파일럿은 4월 둘째 주에 진행할 예정입니다.'
  },
  {
    id: 'src-drive-architecture',
    type: 'drive_file',
    title: 'Atlas 아키텍처 의사결정 기록',
    url: 'https://drive.google.com/file/d/atlas-architecture',
    provider: 'google_drive',
    access: 'available',
    quotedContext: '이벤트 파이프라인이 활성화 지표의 기준 데이터입니다.'
  },
  {
    id: 'src-internal-handbook',
    type: 'internal_doc',
    title: '엔지니어링 온보딩 핸드북',
    url: 'https://internal.northstar.dev/handbook/engineering',
    provider: 'internal',
    access: 'available',
    quotedContext: '모든 엔지니어는 첫 2주 안에 작은 프로덕션 변경을 배포합니다.'
  }
];

const people = [
  {
    id: 'person-jiwoo',
    name: '박지우',
    role: '엔지니어링 매니저',
    relationship: 'manager',
    avatarUrl: null,
    links: [sourceLinks[0]],
    evidence: ['Atlas 팀의 방향을 책임집니다', '주간 제품 싱크를 진행합니다']
  },
  {
    id: 'person-leon',
    name: '레온 마틴스',
    role: '스태프 프로덕트 엔지니어',
    relationship: 'subject-matter-expert',
    avatarUrl: null,
    links: [sourceLinks[1]],
    evidence: ['이벤트 파이프라인을 관리합니다', '데이터 계약의 주요 리뷰어입니다']
  },
  {
    id: 'person-sana',
    name: '사나 오카포',
    role: '프로덕트 디자이너',
    relationship: 'collaborator',
    avatarUrl: null,
    links: [sourceLinks[1]],
    evidence: ['활성화 플로우의 협업 파트너입니다', '고객 리서치를 진행합니다']
  },
  {
    id: 'person-matt',
    name: '마티아스 홀름',
    role: '플랫폼 엔지니어',
    relationship: 'teammate',
    avatarUrl: null,
    links: [sourceLinks[0]],
    evidence: ['로컬 환경 설정의 페어링 파트너입니다', '배포 템플릿을 관리합니다']
  }
];

const timelines = [
  {
    projectId: 'atlas',
    title: 'Atlas 활성화 루프',
    startDate: '2026-03-30',
    endDate: '2026-04-24',
    milestones: [
      { id: 'milestone-contract', title: '이벤트 계약 확정', date: '2026-04-03', status: 'done', ownerIds: ['person-leon'], sourceIds: ['src-drive-architecture'] },
      { id: 'milestone-pilot', title: '파일럿 워크스페이스 준비', date: '2026-04-10', status: 'next', ownerIds: ['person-jiwoo', 'person-sana'], sourceIds: ['src-slack-launch'] },
      { id: 'milestone-readout', title: '파일럿 결과 공유', date: '2026-04-24', status: 'future', ownerIds: ['person-sana'], sourceIds: ['src-slack-launch'] }
    ],
    owners: [people[0], people[1], people[2]],
    sources: [sourceLinks[1], sourceLinks[2]]
  }
];

const unknowns = [
  {
    id: 'unknown-release-owner',
    question: '파일럿 출시가 늦어질 때 최종 결정은 누가 내리나요?',
    whyItMatters: '구현 담당자는 명확하지만, 에스컬레이션 경로가 문서화되어 있지 않습니다.',
    suggestedOwnerId: 'person-jiwoo',
    evidence: ['출시 스레드에서 언급됨', '릴리스 체크리스트에 담당자 없음'],
    status: 'open'
  },
  {
    id: 'unknown-metric-definition',
    question: '주간 제품 리뷰에서 사용하는 활성화 이벤트는 무엇인가요?',
    whyItMatters: '이벤트 파이프라인에 후보 신호가 두 개 있고, 현재 대시보드가 둘을 섞어 사용합니다.',
    suggestedOwnerId: 'person-leon',
    evidence: ['아키텍처 ADR에 두 이벤트가 모두 기록됨', '제품 리뷰 노트에서 활성화 정의가 없음'],
    status: 'open'
  },
  {
    id: 'unknown-research-cadence',
    question: '팀은 파일럿 고객과 얼마나 자주 이야기하나요?',
    whyItMatters: '정기적인 고객 접점이 필요하지만, 노트에 주기만 암시되어 있습니다.',
    suggestedOwnerId: 'person-sana',
    evidence: ['파일럿 계획에 고객 세션이 언급됨', '반복 일정 링크를 찾지 못함'],
    status: 'open'
  }
];

const sections = [
  {
    id: 'company',
    number: '01',
    title: '회사 맥락',
    description: '우리가 일하는 방식과 제품, 그리고 현재 프로젝트의 배경을 확인합니다.',
    status: 'complete',
    blocks: [
      {
        id: 'block-company-overview',
        type: 'paragraph',
        title: 'Northstar는 함께 배우는 팀을 위한 도구를 만듭니다.',
        body: '회사는 짧은 피드백 루프를 중심으로 움직입니다. 제품, 디자인, 엔지니어링이 고객 결과에 대한 오너십을 함께 가집니다.',
        payload: { bullets: ['완벽한 계획보다 빠른 피드백', '모든 팀은 측정 가능한 고객 신호를 책임짐', '기록된 맥락도 제품의 일부'] },
        sourceIds: ['src-notion-product'],
        renderHint: 'prose'
      },
      {
        id: 'block-company-callout',
        type: 'callout',
        title: '첫 달의 방향은 명확합니다.',
        body: '활성화 루프를 이해하고, 작은 프로덕션 변경을 배포하고, 고객 대화에 한 번 참여하세요.',
        payload: { tone: 'accent' },
        sourceIds: ['src-internal-handbook'],
        renderHint: 'callout'
      }
    ],
    people: [people[0]],
    sources: [sourceLinks[0], sourceLinks[3]],
    timelines: []
  },
  {
    id: 'team-role',
    number: '02',
    title: '팀과 역할',
    description: '함께 일할 사람, 협업 방식, 각자가 책임지는 의사결정을 확인합니다.',
    status: 'complete',
    blocks: [
      {
        id: 'block-role-summary',
        type: 'job',
        title: '프로덕트 엔지니어, Atlas',
        body: '활성화 경험과 이를 뒷받침하는 이벤트 파이프라인을 함께 다루게 됩니다.',
        payload: { outcomes: ['활성화 경로를 더 쉽게 이해하도록 만들기', '이벤트 계약을 안정적으로 유지하기', '고객 근거를 주간 의사결정에 반영하기'] },
        sourceIds: ['src-notion-product', 'src-drive-architecture'],
        personIds: ['person-jiwoo', 'person-leon'],
        renderHint: 'card'
      },
      {
        id: 'block-team-people',
        type: 'person-card',
        title: '가장 가까운 협업 파트너',
        body: null,
        payload: { personIds: ['person-jiwoo', 'person-leon', 'person-sana'] },
        sourceIds: [],
        personIds: ['person-jiwoo', 'person-leon', 'person-sana'],
        renderHint: 'compact-list'
      }
    ],
    people: people.slice(0, 3),
    sources: [sourceLinks[0], sourceLinks[2]],
    timelines: []
  },
  {
    id: 'the-job',
    number: '03',
    title: '지금 할 일',
    description: '프로젝트 맥락, 가까운 마일스톤, 그리고 첫 번째 유용한 기여를 확인합니다.',
    status: 'in-progress',
    blocks: [
      {
        id: 'block-job-process',
        type: 'process-flow',
        title: '팀이 움직이는 방식',
        body: '가벼운 루프를 통해 고객 근거가 구현과 가까이 있도록 합니다.',
        payload: { steps: ['불편함 관찰하기', '가장 작은 실험 정의하기', '배포하고 측정하기', '신호 리뷰하기'] },
        sourceIds: ['src-notion-product'],
        renderHint: 'graph'
      },
      {
        id: 'block-job-timeline',
        type: 'timeline',
        title: 'Atlas 활성화 루프',
        body: '현재 프로젝트 일정과 다음 의사결정 지점입니다.',
        payload: { projectId: 'atlas' },
        sourceIds: ['src-slack-launch', 'src-drive-architecture'],
        renderHint: 'timeline'
      }
    ],
    people: people.slice(0, 3),
    sources: [sourceLinks[1], sourceLinks[2]],
    timelines
  },
  {
    id: 'setup',
    number: '04',
    title: '설정과 첫 주',
    description: '접근 권한부터 첫 배포까지, 바로 시작하는 데 필요한 체크리스트입니다.',
    status: 'in-progress',
    blocks: [
      {
        id: 'block-setup-checklist',
        type: 'checklist',
        title: '첫 변경까지 가기',
        body: '코드베이스를 탐색하기 전에 완료하면 좋은 핵심 설정 단계입니다.',
        payload: { itemIds: ['item-access', 'item-local', 'item-pair', 'item-ship'] },
        sourceIds: ['src-internal-handbook'],
        renderHint: 'compact-list'
      }
    ],
    people: [people[3], people[1]],
    sources: [sourceLinks[3]],
    timelines: []
  },
  {
    id: 'unknowns',
    number: '05',
    title: '열린 질문',
    description: '일찍 확인할수록 좋은 문서화 공백과 질문할 사람을 정리했습니다.',
    status: 'in-progress',
    blocks: [
      {
        id: 'block-unknowns',
        type: 'unknown-card',
        title: '확인할 질문',
        body: '좋은 온보딩 워크스페이스는 이미 아는 것과 대화가 필요한 것을 구분해 보여줍니다.',
        payload: { unknownIds: unknowns.map((item) => item.id) },
        sourceIds: ['src-slack-launch', 'src-drive-architecture'],
        renderHint: 'card'
      }
    ],
    people: people.slice(0, 3),
    sources: [sourceLinks[1], sourceLinks[2]],
    timelines: []
  }
];

const checklist = [
  { id: 'item-access', sectionId: 'setup', label: 'Slack, Notion, Drive 접근 권한 확인', completed: true, note: '계정에서 권한을 동기화했습니다' },
  { id: 'item-local', sectionId: 'setup', label: 'Atlas 워크스페이스 로컬에서 실행', completed: true, note: '로컬 환경이 준비되었습니다' },
  { id: 'item-pair', sectionId: 'setup', label: 'Leon과 이벤트 파이프라인 페어링', completed: false, note: null },
  { id: 'item-ship', sectionId: 'setup', label: '작은 프로덕션 변경 하나 배포', completed: false, note: null },
  { id: 'item-customer', sectionId: 'the-job', label: '파일럿 고객 세션 참여', completed: false, note: null },
  { id: 'item-review', sectionId: 'the-job', label: '첫 주간 리드아웃 공유', completed: false, note: null },
  { id: 'item-question', sectionId: 'unknowns', label: '팀과 열린 질문 하나 해결', completed: false, note: null }
];

const state = {
  checklist,
  currentSectionId: 'the-job',
  lastSeenItemId: 'item-local'
};

function getProgress() {
  const completed = state.checklist.filter((item) => item.completed).length;
  return {
    completed,
    total: state.checklist.length,
    percent: Math.round((completed / state.checklist.length) * 100),
    currentSectionId: state.currentSectionId,
    lastSeenItemId: state.lastSeenItemId,
    checklist: state.checklist
  };
}

function getWorkspace() {
  return {
    id: 'onboarding-maya-chen',
    status: 'ready',
    progress: getProgress(),
    sections,
    people,
    timelines,
    unknowns,
    sources: sourceLinks
  };
}

function sendJson(response, status, payload) {
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'access-control-allow-origin': '*',
    'access-control-allow-headers': 'content-type, authorization',
    'access-control-allow-methods': 'GET, POST, PATCH, OPTIONS'
  });
  response.end(JSON.stringify(payload));
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let value = '';
    request.on('data', (chunk) => { value += chunk; });
    request.on('end', () => {
      if (!value) return resolve({});
      try { resolve(JSON.parse(value)); } catch (error) { reject(error); }
    });
    request.on('error', reject);
  });
}

function findPerson(id) {
  return people.find((person) => person.id === id) || null;
}

async function handle(request, response) {
  if (request.method === 'OPTIONS') return sendJson(response, 204, {});

  const url = new URL(request.url, `http://${request.headers.host || 'localhost'}`);
  const path = url.pathname;

  if (path === '/health') return sendJson(response, 200, { status: 'ok', service: 'onboarding-mock-api' });
  if (path === '/v1/onboarding' && request.method === 'GET') return sendJson(response, 200, getWorkspace());

  if (path === '/v1/onboarding/progress' && request.method === 'GET') return sendJson(response, 200, getProgress());
  if (path === '/v1/onboarding/progress' && request.method === 'PATCH') {
    const body = await readBody(request);
    if (body.currentSectionId) state.currentSectionId = body.currentSectionId;
    if (body.lastSeenItemId) state.lastSeenItemId = body.lastSeenItemId;
    return sendJson(response, 200, getProgress());
  }

  if (path === '/v1/onboarding/generate' && request.method === 'POST') {
    return sendJson(response, 202, { jobId: 'job-refresh-001', status: 'queued', pollUrl: '/v1/onboarding/jobs/job-refresh-001' });
  }

  if (path === '/v1/onboarding/ask' && request.method === 'POST') {
    const body = await readBody(request);
    const question = String(body.question || '').toLowerCase();
    const answer = question.includes('owner') || question.includes('오너') || question.includes('담당')
      ? '오너십과 에스컬레이션은 지우에게 먼저 물어보세요. 이벤트 단위의 구현 세부사항은 레온이 가장 잘 설명할 수 있습니다.'
      : question.includes('setup') || question.includes('설정')
        ? '설정 체크리스트부터 시작한 뒤, 배포 템플릿은 마티아스와, 이벤트 파이프라인은 레온과 페어링하세요.'
        : '가장 좋은 다음 단계는 Atlas 아키텍처 의사결정 기록을 읽고, 지우에게 첫 기여와 연결되는 맥락을 물어보는 것입니다.';
    return sendJson(response, 200, {
      answer,
      citations: [sourceLinks[0], sourceLinks[2]],
      relatedPeople: [people[0], people[1]],
      suggestedQuestions: ['무엇을 먼저 배포하면 좋을까요?', '활성화 지표의 오너는 누구인가요?', '파일럿에서 아직 모르는 것은 무엇인가요?'],
      contextUsed: body.context || { currentPath: '/onboarding', sectionId: state.currentSectionId, selectedText: null }
    });
  }

  const checklistMatch = path.match(/^\/v1\/onboarding\/checklist\/([^/]+)$/);
  if (checklistMatch && request.method === 'PATCH') {
    const itemId = checklistMatch[1];
    const item = state.checklist.find((entry) => entry.id === itemId);
    if (!item) return sendJson(response, 404, { code: 'NOT_FOUND', message: 'Checklist item not found', requestId: 'mock-request-404' });
    const body = await readBody(request);
    item.completed = Boolean(body.completed);
    item.note = body.note || item.note;
    return sendJson(response, 200, { itemId, completed: item.completed, progress: getProgress() });
  }

  const sectionMatch = path.match(/^\/v1\/onboarding\/sections\/([^/]+)$/);
  if (sectionMatch && request.method === 'GET') {
    const section = sections.find((entry) => entry.id === sectionMatch[1]);
    if (!section) return sendJson(response, 404, { code: 'NOT_FOUND', message: 'Section not found', requestId: 'mock-request-404' });
    return sendJson(response, 200, section);
  }

  if (path === '/v1/people' && request.method === 'GET') {
    const relationship = url.searchParams.get('relationship');
    const items = relationship && relationship !== 'all' ? people.filter((person) => person.relationship === relationship) : people;
    return sendJson(response, 200, { items, nextCursor: null });
  }

  const timelineMatch = path.match(/^\/v1\/projects\/([^/]+)\/timeline$/);
  if (timelineMatch && request.method === 'GET') {
    const timeline = timelines.find((entry) => entry.projectId === timelineMatch[1]);
    if (!timeline) return sendJson(response, 404, { code: 'NOT_FOUND', message: 'Project timeline not found', requestId: 'mock-request-404' });
    return sendJson(response, 200, timeline);
  }

  const sourceMatch = path.match(/^\/v1\/sources\/([^/]+)$/);
  if (sourceMatch && request.method === 'GET') {
    const source = sourceLinks.find((entry) => entry.id === sourceMatch[1]);
    if (!source) return sendJson(response, 404, { code: 'NOT_FOUND', message: 'Source not found', requestId: 'mock-request-404' });
    return sendJson(response, 200, source);
  }

  if (path === '/v1/onboarding/unknowns' && request.method === 'GET') {
    const status = url.searchParams.get('status') || 'open';
    return sendJson(response, 200, { items: unknowns.filter((item) => item.status === status) });
  }

  if (path === '/v1/onboarding/feedback' && request.method === 'POST') {
    const body = await readBody(request);
    return sendJson(response, 201, { ...body, id: 'feedback-mock-001', status: 'received', createdAt: new Date().toISOString() });
  }

  return sendJson(response, 404, { code: 'NOT_FOUND', message: `No mock route for ${request.method} ${path}`, requestId: 'mock-request-404' });
}

createServer((request, response) => {
  handle(request, response).catch((error) => {
    console.error(error);
    sendJson(response, 500, { code: 'MOCK_SERVER_ERROR', message: 'The mock server could not process the request', requestId: 'mock-request-500' });
  });
}).listen(port, () => {
  console.log(`Onboarding mock API listening at http://localhost:${port}`);
});
