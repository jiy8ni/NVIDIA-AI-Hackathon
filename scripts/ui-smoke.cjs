const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HANDOFF_PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.HANDOFF_BROWSER_CHANNEL ? { channel: process.env.HANDOFF_BROWSER_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:5173');
    await page.locator('.start-card, .app-shell, .error-card').first().waitFor();
    if (await page.getByRole('button', { name: '온보딩 생성 시작', exact: true }).isVisible()) {
      await page.getByRole('button', { name: '온보딩 생성 시작', exact: true }).click();
    } else {
      await page.getByRole('button', { name: '컨텍스트 새로고침', exact: true }).click();
    }
    await page.getByText('근거 검증 결과 준비 완료', { exact: true }).waitFor({ timeout: 30000 });
    fs.mkdirSync(path.join('output', 'qa'), { recursive: true });
    await page.screenshot({ path: 'output/qa/home.png', fullPage: true });
    await page.locator('.section-nav button[title="팀과 역할"]').click();
    await page.locator('.people-inline strong').getByText('대협', { exact: true }).waitFor();
    await page.screenshot({ path: 'output/qa/people.png', fullPage: true });
    await page.locator('.section-nav button[title="지금 할 일"]').click();
    await page.getByText('Task Contract', { exact: false }).first().waitFor();
    assert.ok(await page.locator('.task-field dd').filter({ hasText: '확인 필요' }).count());
    await page.locator('.timeline-list').getByText('증빙 검토 회의', { exact: true }).waitFor();
    await page.locator('.job-block').filter({ hasText: '정산 검토 요청' }).locator('summary').click();
    assert.ok(await page.locator('.job-block').filter({ hasText: '정산 검토 요청' }).getByText('의존성: 행사 정산 자료 정리', { exact: true }).count());
    await page.screenshot({ path: 'output/qa/tasks.png', fullPage: true });
    await page.locator('.section-nav button[title="열린 질문"]').click();
    await page.getByText('상충 가능성 · 최신 내용을 자동 채택하지 않았습니다.', { exact: true }).waitFor();
    await page.screenshot({ path: 'output/qa/conflicts.png', fullPage: true });
    await page.getByRole('button', { name: '온보딩 어시스턴트 열기' }).click();
    await page.getByPlaceholder('추가로 궁금한 내용을 입력하세요').fill('법인인데 왜 모임통장?');
    await page.getByRole('button', { name: '질문하기', exact: true }).click();
    await page.locator('.citation-row a').first().waitFor({ timeout: 30000 });
    assert.ok(await page.locator('.citation-row a').count() >= 2);
    await page.screenshot({ path: 'output/qa/answer.png', fullPage: true });
    assert.deepEqual(errors, []);
    await page.getByRole('button', { name: '어시스턴트 닫기' }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'output/qa/mobile.png', fullPage: true });
    console.log('UI smoke passed: generate, polling, people, timeline, task dependencies, conflict, ask, citations, no page errors.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
