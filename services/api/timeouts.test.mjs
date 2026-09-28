import test from 'node:test';
import assert from 'node:assert/strict';
import { workerTimeoutMs } from './timeouts.mjs';

test('Node waits longer than the Python worker deadline it relies on', () => {
  for (const [mode, name] of [['ask', 'HANDOFF_ASK_TIMEOUT_SECONDS'], ['generate', 'HANDOFF_GENERATE_TIMEOUT_SECONDS']]) {
    for (const seconds of ['40', '150', '300']) {
      assert.ok(workerTimeoutMs(mode, { [name]: seconds }) > Number(seconds) * 1000, `${mode} ${seconds}s`);
    }
  }
});

test('unset or invalid deadlines fall back to the Python defaults instead of a shorter cut-off', () => {
  assert.ok(workerTimeoutMs('ask', {}) > 180 * 1000);
  assert.ok(workerTimeoutMs('generate', { HANDOFF_GENERATE_TIMEOUT_SECONDS: 'abc' }) > 360 * 1000);
});
