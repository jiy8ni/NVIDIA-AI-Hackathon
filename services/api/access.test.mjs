import test from 'node:test';
import assert from 'node:assert/strict';
import { recheckAccess } from './access.mjs';

const input = { scope: { userId: 'kim', teamId: 'atlas', sources: ['notion'] }, sourceIds: ['notion:a', 'notion:b'] };
const config = { HANDOFF_RETRIEVAL_MODE: 'http', HANDOFF_ACCESS_URL: 'https://retrieval.example.com/access' };
const transport = edit => async (url, options) => {
  const body = JSON.parse(options.body);
  assert.deepEqual(body.scope, input.scope);
  assert.equal(options.redirect, 'error');
  const data = { requestId: body.requestId, records: body.sourceIds.map(sourceId => ({ sourceId, accessStatus: 'accessible' })) };
  edit?.(data);
  return { ok: true, json: async () => data };
};
test('all source permissions rechecked with server scope', async () => {
  await recheckAccess(input, config, transport());
});
test('missing ACL bridge fails closed', async () => {
  await assert.rejects(recheckAccess(input, { HANDOFF_RETRIEVAL_MODE: 'http' }), { code: 'ACCESS_RECHECK_REQUIRED' });
});
for (const accessStatus of ['restricted', 'deleted', 'unknown']) test(`revoked ${accessStatus} blocks entire cached workspace`, async () => {
  await assert.rejects(recheckAccess(input, config, transport(d => { d.records[0].accessStatus = accessStatus; })), { code: 'SOURCE_ACCESS_REVOKED' });
});
for (const edit of [d => { d.records.pop(); }, d => { d.requestId = 'wrong'; }, d => { d.records[1].sourceId = d.records[0].sourceId; }]) test('incomplete or mismatched ACL response is rejected', async () => {
  await assert.rejects(recheckAccess(input, config, transport(edit)), { code: 'ACCESS_RECHECK_INVALID' });
});
test('outage does not serve stale content', async () => {
  await assert.rejects(recheckAccess(input, config, async () => { throw Error('network'); }), { code: 'ACCESS_RECHECK_UNAVAILABLE' });
});
