import crypto from 'node:crypto';

const fail = (status, code, message) => { throw Object.assign(new Error(message), { status, code }); };

// Separate proposed integration contract. Never infer current ACL from cached accessStatus.
export async function recheckAccess({ scope, sourceIds }, config = process.env, request = fetch) {
  if ((config.HANDOFF_RETRIEVAL_MODE || 'fixture') === 'fixture' || sourceIds.length === 0) return;
  if (!config.HANDOFF_ACCESS_URL) fail(503, 'ACCESS_RECHECK_REQUIRED', '실제 자료의 권한 재검증 연결이 필요합니다.');
  let url;
  try { url = new URL(config.HANDOFF_ACCESS_URL); } catch { fail(503, 'ACCESS_RECHECK_REQUIRED', '권한 재검증 주소를 확인하세요.'); }
  if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)))) fail(503, 'ACCESS_RECHECK_REQUIRED', '권한 조회는 HTTPS 또는 loopback 주소만 허용합니다.');
  const requestId = crypto.randomUUID(), unique = [...new Set(sourceIds)];
  let data;
  try {
    const response = await request(url, { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + (config.HANDOFF_RETRIEVAL_TOKEN || '') }, body: JSON.stringify({ requestId, scope, sourceIds: unique }), signal: AbortSignal.timeout(5000), redirect: 'error' });
    if (!response.ok) throw Error();
    data = await response.json();
  } catch { fail(503, 'ACCESS_RECHECK_UNAVAILABLE', '현재 자료 접근 권한을 확인할 수 없어 콘텐츠 제공을 중단했습니다.'); }
  if (data.requestId !== requestId || !Array.isArray(data.records) || data.records.length !== unique.length || new Set(data.records.map(x => x?.sourceId)).size !== unique.length || data.records.some(x => !x || !unique.includes(x.sourceId) || !['accessible', 'restricted', 'deleted', 'unknown'].includes(x.accessStatus))) fail(503, 'ACCESS_RECHECK_INVALID', '권한 조회 응답이 계약과 일치하지 않습니다.');
  if (data.records.some(x => x.accessStatus !== 'accessible')) fail(403, 'SOURCE_ACCESS_REVOKED', '일부 원문의 권한이 변경되었습니다. 접근 가능한 범위로 온보딩을 새로 생성해 주세요.');
}
