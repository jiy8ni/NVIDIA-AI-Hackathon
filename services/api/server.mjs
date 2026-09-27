import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';
import YAML from 'yaml';
import { recheckAccess } from './access.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const port = Number(process.env.PORT || 8787);
const secret = process.env.HANDOFF_JWT_SECRET;
const internalToken = process.env.HANDOFF_INTERNAL_TOKEN;
if (!secret || secret.length < 32 || !internalToken) throw new Error('Configure JWT secret (32+ chars) and internal token, or use node scripts/dev.mjs');
const teamId = process.env.HANDOFF_TEAM_ID || 'atlas';
const worker = process.env.HANDOFF_WORKER_URL || 'http://127.0.0.1:8788';
const dataDir = process.env.HANDOFF_STATE_DIR || path.join(root, '.runtime');
fs.mkdirSync(dataDir, { recursive: true });
const statePath = path.join(dataDir, 'state.json');
const state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : { users: {}, feedback: [] };
const active = new Map();
const sectionIds = ['company', 'team-role', 'the-job', 'setup', 'unknowns'];
const spec = YAML.parse(fs.readFileSync(path.join(root, 'apps/web/openapi.yaml'), 'utf8'));
const ajv = new Ajv({ strict: false, allErrors: false });
addFormats(ajv);
const validators = Object.fromEntries(Object.entries(spec.components.schemas).map(([name, schema]) => [name, ajv.compile({ ...schema, components: spec.components })]));
const hash = value => crypto.createHash('sha256').update(value).digest('hex').slice(0, 20);
const fail = (status, code, message) => { throw Object.assign(new Error(message), { status, code }); };
function validate(name, value, output = false) {
  if (!validators[name](value)) fail(output ? 502 : 400, output ? 'MODEL_OUTPUT_INVALID' : 'INVALID_REQUEST', output ? '생성 결과가 API 계약을 만족하지 않습니다.' : '요청이 API 계약을 만족하지 않습니다.');
  return value;
}
function save() {
  const temp = statePath + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(state, null, 2), { mode: 0o600 });
  fs.renameSync(temp, statePath);
}
for (const user of Object.values(state.users)) {
  if (user.workspace.status === 'generating') { user.workspace.status = 'failed'; user.job.status = 'failed'; }
}
save();
function identity(req) {
  try {
    if (!/^Bearer [^ ]+$/.test(req.headers.authorization || '')) throw Error();
    const parts = (req.headers.authorization || '').replace(/^Bearer /, '').split('.');
    if (parts.length !== 3) throw Error();
    const header = JSON.parse(Buffer.from(parts[0], 'base64url'));
    if (header.alg !== 'HS256') throw Error();
    const expected = crypto.createHmac('sha256', secret).update(parts.slice(0, 2).join('.')).digest();
    const received = Buffer.from(parts[2], 'base64url');
    if (received.length !== expected.length || !crypto.timingSafeEqual(received, expected)) throw Error();
    const claims = JSON.parse(Buffer.from(parts[1], 'base64url'));
    if (typeof claims.sub !== 'string' || !claims.sub || claims.iss !== 'handoffos-local' || claims.aud !== 'handoffos-api' || !Number.isFinite(claims.exp) || claims.exp <= Date.now() / 1000 || (claims.nbf && claims.nbf > Date.now() / 1000) || claims.teamId !== teamId) throw Error();
    return claims.sub;
  } catch { fail(401, 'UNAUTHORIZED', '유효한 Bearer JWT가 필요합니다.'); }
}
async function body(req) {
  let value = '';
  for await (const chunk of req) { value += chunk; if (Buffer.byteLength(value) > 64000) fail(413, 'INVALID_REQUEST', '요청이 너무 큽니다.'); }
  try { return JSON.parse(value); } catch { fail(400, 'INVALID_REQUEST', 'JSON 요청이 필요합니다.'); }
}
function progress(ws) {
  const p = ws.progress;
  p.total = p.checklist.length; p.completed = p.checklist.filter(x => x.completed).length;
  p.percent = p.total ? Math.floor(100 * p.completed / p.total) : 0;
  for (const section of ws.sections) {
    const items = p.checklist.filter(x => x.sectionId === section.id);
    section.status = ws.status === 'generating' ? 'locked' : items.length && items.every(x => x.completed) ? 'complete' : 'in-progress';
  }
  return p;
}
function getUser(uid) {
  const user = state.users[hash(uid)];
  if (!user || user.userId !== uid) fail(404, 'WORKSPACE_NOT_FOUND', '먼저 온보딩을 생성해 주세요.');
  progress(user.workspace);
  return user;
}
function editable(user) { if (user.workspace.status === 'generating') fail(409, 'GENERATION_IN_PROGRESS', '생성 완료 후 다시 시도하세요.'); }
function ownQuery(url, uid) {
  if (!url.searchParams.get('userId')) fail(400, 'INVALID_REQUEST', 'userId가 필요합니다.');
  if (url.searchParams.get('userId') !== uid) fail(403, 'FORBIDDEN', '다른 사용자의 자료를 조회할 수 없습니다.');
}
function workspaceIds(ws) { return new Set([ws.id, ...ws.sections.flatMap(s => [s.id, ...s.blocks.map(b => b.id)]), ...ws.sources.map(s => s.id), ...ws.people.map(p => p.id), ...ws.timelines.map(t => t.projectId)]); }
async function runAgent(input) {
  let response;
  try {
    response = await fetch(worker + '/internal/run', { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + internalToken }, body: JSON.stringify(input), signal: AbortSignal.timeout(input.mode === 'ask' ? 45000 : 180000) });
  } catch (error) { fail(error.name === 'TimeoutError' ? 504 : 502, error.name === 'TimeoutError' ? 'ASK_TIMEOUT' : 'UPSTREAM_ERROR', 'Orchestrator 응답을 받지 못했습니다.'); }
  const data = await response.json();
  if (!response.ok) fail(response.status, data.code || 'UPSTREAM_ERROR', data.message || 'Orchestrator 처리에 실패했습니다.');
  return data;
}
function skeleton(uid) {
  return { id: 'onboarding-' + hash(uid), status: 'generating', progress: { completed: 0, total: 0, percent: 0, currentSectionId: 'company', lastSeenItemId: null, checklist: [] },
    sections: sectionIds.map((id, i) => ({ id, number: '0' + (i + 1), title: ['회사 맥락', '팀과 역할', '지금 할 일', '설정과 첫 주', '열린 질문'][i], description: '자료를 검토하고 있습니다.', status: 'locked', blocks: [], sources: [] })), people: [], timelines: [], unknowns: [], sources: [] };
}
async function generate(user, input) {
  try {
    const output = await runAgent({ mode: 'generate', scope: { userId: user.userId, teamId, sources: input.sourceScope }, role: input.role, question: '', context: {} });
    const content = output.result;
    const prefix = hash(teamId + input.role);
    const existing = new Map(user.workspace.progress.checklist.map(x => [x.id, x]));
    const itemMap = new Map(content.checklist.map(x => [x.id, prefix + '-' + x.id]));
    const checklist = content.checklist.map(x => ({ ...x, id: itemMap.get(x.id), completed: existing.get(itemMap.get(x.id))?.completed || false, note: existing.get(itemMap.get(x.id))?.note ?? null }));
    for (const s of content.sections) for (const b of s.blocks) if (b.type === 'checklist') b.payload.itemIds = b.payload.itemIds.map(id => itemMap.get(id));
    const next = { id: user.workspace.id, status: 'ready', progress: { ...user.workspace.progress, checklist }, sections: content.sections, people: content.people, timelines: content.timelines, unknowns: content.unknowns, sources: content.sources };
    if (!checklist.some(x => x.id === next.progress.lastSeenItemId)) next.progress.lastSeenItemId = null;
    progress(next); validate('OnboardingWorkspace', next, true);
    user.workspace = next; user.role = input.role; user.sources = input.sourceScope;
    user.retrievalMode = process.env.HANDOFF_RETRIEVAL_MODE || 'fixture';
    user.job.status = 'complete'; user.lastRunId = output.runId;
  } catch (error) {
    user.workspace.status = 'failed'; user.job.status = 'failed'; progress(user.workspace);
    console.error(JSON.stringify({ event: 'generation_failed', jobId: user.job.jobId, code: error.code || 'INTERNAL_ERROR' }));
  } finally { active.delete(user.userId); save(); }
}

const server = http.createServer(async (req, res) => {
  const send = (status, value) => { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }); res.end(JSON.stringify(value)); };
  try {
    const origin = req.headers.origin;
    const allowed = (process.env.HANDOFF_WEB_ORIGINS || 'http://localhost:5173,http://127.0.0.1:5173').split(',');
    if (origin && !allowed.includes(origin)) fail(403, 'FORBIDDEN', '허용되지 않은 브라우저 origin입니다.');
    if (origin) { res.setHeader('access-control-allow-origin', origin); res.setHeader('vary', 'origin'); }
    res.setHeader('access-control-allow-headers', 'authorization,content-type');
    res.setHeader('access-control-allow-methods', 'GET,POST,PATCH,OPTIONS');
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
    const uid = identity(req);
    const url = new URL(req.url, 'http://127.0.0.1');
    const pathname = url.pathname;
    if (url.searchParams.has('include') && url.searchParams.get('include').split(',').some(v => !spec.components.schemas.IncludeValue.enum.includes(v))) fail(400, 'INVALID_REQUEST', 'include 값이 올바르지 않습니다.');
    if (req.method === 'POST' && pathname === '/v1/onboarding/generate') {
      const input = validate('GenerateOnboardingRequest', await body(req));
      if (input.userId !== uid || input.teamId !== teamId) fail(403, 'FORBIDDEN', '사용자 또는 팀 권한이 없습니다.');
      if (!input.role.trim() || input.role.length > 200 || !input.sourceScope.length || input.sourceScope.includes('internal')) fail(400, 'INVALID_REQUEST', '역할과 slack/notion/drive 범위를 선택하세요. internal 소스는 MVP에서 지원하지 않습니다.');
      input.sourceScope = [...new Set(input.sourceScope)];
      let user = state.users[hash(uid)];
      if (active.has(uid)) { send(202, user.job); return; }
      if (user?.workspace.status === 'ready' && !input.refresh) { send(202, user.job); return; }
      if (!user) { user = { userId: uid, workspace: skeleton(uid), role: input.role, sources: input.sourceScope }; state.users[hash(uid)] = user; }
      user.workspace.status = 'generating'; progress(user.workspace);
      user.job = { jobId: crypto.randomUUID(), status: 'queued', pollUrl: '/v1/onboarding?userId=' + encodeURIComponent(uid) };
      active.set(uid, true); save();
      send(202, validate('GenerationJob', user.job, true));
      setImmediate(() => { user.job.status = 'running'; save(); generate(user, input); });
      return;
    }
    const user = getUser(uid), ws = user.workspace;
    // Guard every cached-content route, including workspace/sections/people and ask context.
    const cachedMode = user.retrievalMode === 'fixture' && (process.env.HANDOFF_RETRIEVAL_MODE || 'fixture') === 'fixture' ? 'fixture' : 'http';
    await recheckAccess({ scope: { userId: uid, teamId, sources: user.sources }, sourceIds: ws.sources.map(s => s.id) }, { ...process.env, HANDOFF_RETRIEVAL_MODE: cachedMode });
    if (req.method === 'GET' && pathname === '/v1/onboarding') { ownQuery(url, uid); send(200, validate('OnboardingWorkspace', ws, true)); return; }
    if (pathname === '/v1/onboarding/progress') {
      if (req.method === 'GET') { ownQuery(url, uid); send(200, ws.progress); return; }
      if (req.method === 'PATCH') {
        editable(user); const input = validate('UpdateProgressRequest', await body(req));
        if (!Object.keys(input).some(k => ['currentSectionId', 'lastSeenItemId'].includes(k))) fail(400, 'INVALID_REQUEST', '변경할 위치가 필요합니다.');
        if (input.lastSeenItemId !== undefined && !ws.progress.checklist.some(x => x.id === input.lastSeenItemId)) fail(404, 'NOT_FOUND', '해당 체크리스트가 없습니다.');
        if (input.currentSectionId !== undefined) ws.progress.currentSectionId = input.currentSectionId;
        if (input.lastSeenItemId !== undefined) ws.progress.lastSeenItemId = input.lastSeenItemId;
        save(); send(200, ws.progress); return;
      }
    }
    if (req.method === 'PATCH' && pathname.startsWith('/v1/onboarding/checklist/')) {
      editable(user); const input = validate('ChecklistMutationRequest', await body(req));
      const item = ws.progress.checklist.find(x => x.id === decodeURIComponent(pathname.split('/').pop()));
      if (!item) fail(404, 'NOT_FOUND', '해당 체크리스트가 없습니다.');
      item.completed = input.completed; if (input.note !== undefined) item.note = input.note;
      progress(ws); save(); send(200, { itemId: item.id, completed: item.completed, progress: ws.progress }); return;
    }
    if (req.method === 'GET' && pathname.startsWith('/v1/onboarding/sections/')) {
      const section = ws.sections.find(s => s.id === pathname.split('/').pop());
      if (!section) fail(404, 'NOT_FOUND', '섹션이 없습니다.'); send(200, section); return;
    }
    if (req.method === 'POST' && pathname === '/v1/onboarding/ask') {
      editable(user); const input = validate('AssistantQuestion', await body(req)); const ctx = input.context;
      if (!input.question.trim() || input.question.length > 4000 || ctx.currentPath.length > 500 || (ctx.entityIds?.length || 0) > 50) fail(400, 'INVALID_REQUEST', '질문 또는 문맥이 너무 길거나 비어 있습니다.');
      if (ctx.onboardingId !== ws.id || (ctx.sectionId && !sectionIds.includes(ctx.sectionId)) || (ctx.entityIds || []).some(id => !workspaceIds(ws).has(id))) fail(403, 'FORBIDDEN', '워크스페이스 범위 밖의 문맥입니다.');
      const output = await runAgent({ mode: 'ask', scope: { userId: uid, teamId, sources: user.sources }, role: user.role, question: input.question, context: ctx });
      send(200, validate('AssistantAnswer', output.result, true)); return;
    }
    if (req.method === 'GET' && pathname === '/v1/people') {
      if (url.searchParams.get('onboardingId') !== ws.id) fail(403, 'FORBIDDEN', '워크스페이스가 일치하지 않습니다.');
      const relationship = url.searchParams.get('relationship') || 'all', limit = Number(url.searchParams.get('limit') || 20);
      if (!['all', 'manager', 'teammate', 'collaborator', 'subject-matter-expert'].includes(relationship) || !Number.isInteger(limit) || limit < 1 || limit > 100) fail(400, 'INVALID_REQUEST', '조회 조건이 올바르지 않습니다.');
      send(200, { items: ws.people.filter(p => relationship === 'all' || p.relationship === relationship).slice(0, limit), nextCursor: null }); return;
    }
    if (req.method === 'GET' && /^\/v1\/projects\/[^/]+\/timeline$/.test(pathname)) {
      const timeline = ws.timelines.find(t => t.projectId === decodeURIComponent(pathname.split('/')[3]));
      if (!timeline) fail(404, 'NOT_FOUND', '확인된 날짜를 가진 타임라인이 없습니다.'); send(200, timeline); return;
    }
    if (req.method === 'GET' && pathname.startsWith('/v1/sources/')) {
      const source = ws.sources.find(s => s.id === decodeURIComponent(pathname.slice('/v1/sources/'.length)));
      if (!source) fail(404, 'NOT_FOUND', '접근 가능한 출처가 없습니다.');
      send(200, source); return;
    }
    if (req.method === 'GET' && pathname === '/v1/onboarding/unknowns') {
      const status = url.searchParams.get('status') || 'open', sectionId = url.searchParams.get('sectionId');
      if (!['open', 'resolved', 'dismissed'].includes(status) || (sectionId && !sectionIds.includes(sectionId))) fail(400, 'INVALID_REQUEST', '조회 조건이 올바르지 않습니다.');
      const ids = sectionId ? new Set(ws.sections.find(s => s.id === sectionId).blocks.map(b => b.payload.unknownId).filter(Boolean)) : null;
      send(200, { items: ws.unknowns.filter(x => x.status === status && (!ids || ids.has(x.id))) }); return;
    }
    if (req.method === 'POST' && pathname === '/v1/onboarding/feedback') {
      editable(user); const input = validate('FeedbackRequest', await body(req));
      const targets = { section_block: ws.sections.flatMap(s => s.blocks.map(b => b.id)), source: ws.sources.map(s => s.id), person: ws.people.map(p => p.id), unknown: ws.unknowns.map(u => u.id), timeline: ws.timelines.map(t => t.projectId) };
      if (!targets[input.targetType].includes(input.targetId)) fail(404, 'NOT_FOUND', '피드백 대상이 없습니다.');
      const feedback = { ...input, id: crypto.randomUUID(), status: 'received', createdAt: new Date().toISOString() };
      state.feedback.push({ userId: uid, feedback }); save(); send(201, feedback); return;
    }
    fail(404, 'NOT_FOUND', '지원하지 않는 API입니다.');
  } catch (error) { send(error.status || 500, { code: error.code || 'INTERNAL_ERROR', message: error.status ? error.message : '서버 처리에 실패했습니다.', requestId: crypto.randomUUID() }); }
});
server.requestTimeout = 60000;
server.listen(port, '127.0.0.1', () => console.log('HandoffOS API: http://127.0.0.1:' + port));
