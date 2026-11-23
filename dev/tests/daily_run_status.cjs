require('../test_guard.cjs').enforceTestEnvironment();

// The Today run summary uses only bounded fields from the fictional status fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/daily-run-status.json'), 'utf8'));
const failureFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/daily-run-failure.json'), 'utf8'));

function easternDate() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type)?.value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}

async function main() {
  const parsed = new URL(baseURL);
  assert.ok(['127.0.0.1', 'localhost'].includes(parsed.hostname), 'Test uses a loopback preview');
  let status = structuredClone(fixture);
  let statusReads = 0;
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'fixture profile absent' } }));
    await page.route('**/api/edition*', route => route.fulfill({ status: 404, json: { error: 'no generated fixture edition' } }));
    await page.route('**/api/status', route => { statusReads += 1; return route.fulfill({ status: 200, json: status }); });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    const summary = page.locator('.today-run-summary');
    await summary.waitFor().catch(async error => {
      throw new Error(`Run summary missing (status reads ${statusReads}, Today: ${await page.locator('main').innerText().catch(() => '')}): ${error.message}`);
    });
    assert.equal(await page.locator('.today-title').innerText(), 'Your daily briefing');
    assert.match(await summary.locator('.today-run-meta').innerText(), /^\d+ stories · about 5 min$/);
    assert.equal(await summary.evaluate(node => node.previousElementSibling.className), 'today-title', 'The length line follows the daily briefing title');

    status.daily_run.daily_briefing.duration_seconds = 300;
    status.daily_run.daily_briefing.spoken_words = 750;
    status.daily_run.daily_audio.duration_seconds = 240;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-run-summary').waitFor();
    await page.waitForFunction(() => document.querySelector('.today-run-meta')?.textContent === '4 stories · about 4 min');
    assert.match(await page.locator('.today-run-meta').innerText(), /^\d+ stories · about 4 min$/);

    status.daily_run.local_date = '2026-11-16';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    await page.waitForTimeout(50);
    assert.match(await page.locator('.today-run-meta').innerText(), /^\d+ stories · about \d+ min$/, 'Edition duration remains when status is for another date');
    assert.deepEqual(errors, [], `browser errors: ${errors.join('; ')}`);

    const failurePage = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const today = easternDate();
    const failedStatus = structuredClone(failureFixture);
    failedStatus.last_run_at = `${today}T10:00:00Z`;
    failedStatus.daily_run.local_date = today;
    let generationRequests = 0;
    await failurePage.addInitScript(profile => localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile)), {
      version: 1, words: 'I follow payments.', minutes: 10, background: null, muted: [], topics: [{
        id: 'payments', label: 'Payments', scope: 'Payment systems and settlement.', include: ['card payments'], exclude: [],
        depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments',
      }],
    });
    await failurePage.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'fixture profile absent' } }));
    await failurePage.route('**/api/edition*', route => route.fulfill({ status: 404, json: { error: 'no generated fixture edition' } }));
    await failurePage.route('**/api/status', route => route.fulfill({ status: 200, json: failedStatus }));
    await failurePage.route('**/api/generate', route => {
      generationRequests += 1;
      return route.fulfill({ status: 503, json: { error: 'fixture generation unavailable' } });
    });
    await failurePage.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await failurePage.getByRole('heading', { name: "Today's briefing is still being prepared" }).waitFor();
    assert.equal(await failurePage.locator('.today-service-preparing p').innerText(), 'The scheduled run did not finish. Please check back soon.');
    assert.equal(generationRequests, 0, 'A failed scheduled run does not start a second generation request');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
