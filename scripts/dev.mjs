import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (fs.existsSync(path.join(root, '.env'))) process.loadEnvFile(path.join(root, '.env'));
const env = { ...process.env, HANDOFF_INTERNAL_TOKEN: process.env.HANDOFF_INTERNAL_TOKEN || crypto.randomBytes(32).toString('hex'), HANDOFF_JWT_SECRET: process.env.HANDOFF_JWT_SECRET || crypto.randomBytes(32).toString('hex'), HANDOFF_TEAM_ID: process.env.HANDOFF_TEAM_ID || 'atlas', HANDOFF_MODEL_MODE: process.env.HANDOFF_MODEL_MODE || 'offline', HANDOFF_RETRIEVAL_MODE: process.env.HANDOFF_RETRIEVAL_MODE || 'mcp', PYTHONPATH: path.join(root, 'services/orchestrator'), PYTHONUTF8: '1' };
const uid = process.env.HANDOFF_USER_ID || 'kim-juhyung';
const b64 = obj => Buffer.from(JSON.stringify(obj)).toString('base64url');
const unsigned = b64({ alg: 'HS256', typ: 'JWT' }) + '.' + b64({ sub: uid, teamId: env.HANDOFF_TEAM_ID, iss: 'handoffos-local', aud: 'handoffos-api', exp: Math.floor(Date.now() / 1000) + 43200 });
const token = unsigned + '.' + crypto.createHmac('sha256', env.HANDOFF_JWT_SECRET).update(unsigned).digest('base64url');
const python = process.env.HANDOFF_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const children = [];
let stopping = false;
function stop(code = 0) { if (stopping) return; stopping = true; for (const child of children) child.kill(); process.exitCode = code; }
function start(command, args, cwd, extra = {}) { const child = spawn(command, args, { cwd, env: { ...env, ...extra }, stdio: 'inherit', windowsHide: true }); children.push(child); child.on('error', error => { console.error(error.message); stop(1); }); child.on('exit', code => { if (code && !stopping) stop(code); }); }
process.on('SIGINT', () => stop()); process.on('SIGTERM', () => stop());
start(python, ['-m', 'uvicorn', 'handoff.api:app', '--host', '127.0.0.1', '--port', '8788', '--no-access-log'], root);
start(process.execPath, ['services/api/server.mjs'], root);
start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], path.join(root, 'apps/web'), { VITE_API_URL: 'http://127.0.0.1:8787', VITE_AUTH_TOKEN: token, VITE_USER_ID: uid, VITE_TEAM_ID: env.HANDOFF_TEAM_ID, VITE_ROLE: process.env.HANDOFF_ROLE || '운영 담당자', VITE_MODEL_MODE: env.HANDOFF_MODEL_MODE, VITE_RETRIEVAL_MODE: env.HANDOFF_RETRIEVAL_MODE });
console.log('HandoffOS local: http://127.0.0.1:5173 — demo JWT expires in 12h. Ctrl+C stops all services.');
