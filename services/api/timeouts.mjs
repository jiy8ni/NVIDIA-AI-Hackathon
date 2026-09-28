// The Python worker enforces its own deadline (HANDOFF_*_TIMEOUT_SECONDS) and reports AGENT_TIMEOUT with a
// trace. Node must wait a little longer, or it cuts the request off first and the user sees a generic 504.
// Defaults mirror BUDGETS in services/orchestrator/handoff/engine.py.
const PYTHON_DEFAULT_SECONDS = { ask: 180, generate: 360 };
const MARGIN_SECONDS = 15;

export function workerTimeoutMs(mode, env = process.env) {
  const key = mode === 'ask' ? 'ask' : 'generate';
  const value = Number(env[key === 'ask' ? 'HANDOFF_ASK_TIMEOUT_SECONDS' : 'HANDOFF_GENERATE_TIMEOUT_SECONDS']);
  const seconds = Number.isFinite(value) && value > 0 ? value : PYTHON_DEFAULT_SECONDS[key];
  return (seconds + MARGIN_SECONDS) * 1000;
}
