require('../test_guard.cjs').enforceTestEnvironment();

// The Today run summary uses only bounded fields from the fictional status fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { builtProfileKey } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const shotDir = process.env.BRIEFING_SHOT_DIR;
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/daily-run-status.json'), 'utf8'));
const failureFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/daily-run-failure.json'), 'utf8'));
const sampleEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'))
  .editions.find(edition => edition.kind === 'daily');

function easternDate() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type)?.value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}

async function capture(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, name), animations: 'disabled' });
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
    await failurePage.getByRole('heading', { name: "Today's briefing isn't ready yet" }).waitFor();
    assert.match(await failurePage.locator('.today-service-preparing p').innerText(), /Today’s briefing did not finish/);
    const earlierAction = failurePage.locator('.today-service-preparing a.primary');
    await earlierAction.waitFor();
    assert.match(await earlierAction.innerText(), /^Listen to the latest briefing · /,
      'The failed state leads with the latest earlier edition');
    assert.match(await earlierAction.getAttribute('href'), /^#edition\/daily\//);
    await capture(failurePage, 'failed-run-phone-light.png');
    await failurePage.emulateMedia({ colorScheme: 'dark' });
    await capture(failurePage, 'failed-run-phone-dark.png');
    await failurePage.emulateMedia({ colorScheme: 'light' });
    await failurePage.setViewportSize({ width: 1440, height: 1000 });
    await capture(failurePage, 'failed-run-desktop-light.png');
    await failurePage.setViewportSize({ width: 390, height: 844 });
    assert.equal(generationRequests, 0, 'A failed scheduled run does not start a second generation request');

    const availableTodayPage = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const failedWithoutReportDate = structuredClone(failedStatus);
    failedWithoutReportDate.daily_run.local_date = null;
    const availableToday = { ...structuredClone(sampleEdition), id: 'generated-today-status-fixture',
      revision_id: 'generated-today-status-r1', generated: true, local_date: today,
      generated_at: `${today}T06:00:00-05:00`, title: 'A fictional briefing for today' };
    const availableProfile = {
      version: 1, words: 'I follow payments.', minutes: 10, background: null, muted: [], topics: [{
        id: 'payments', label: 'Payments', scope: 'Payment systems and settlement.', include: ['card payments'], exclude: [],
        depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments',
      }],
    };
    await availableTodayPage.addInitScript(({ profile, key }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
      localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { profile: availableProfile, key: builtProfileKey(availableProfile) });
    await availableTodayPage.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'fixture profile absent' } }));
    await availableTodayPage.route('**/api/edition*', route => route.fulfill({ status: 200, json: {
      generated: true, edition: availableToday, sources: [],
    } }));
    await availableTodayPage.route('**/api/status', route => route.fulfill({ status: 200, json: failedWithoutReportDate }));
    const failedStatusResponse = availableTodayPage.waitForResponse(response => response.url().includes('/api/status'));
    await availableTodayPage.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await availableTodayPage.locator('.today-page').waitFor();
    await failedStatusResponse;
    await availableTodayPage.waitForTimeout(30);
    assert.equal(await availableTodayPage.locator('.today-date').innerText(),
      new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' })
        .format(new Date(`${today}T12:00:00Z`)), 'Today keeps the actual date on its available edition');
    assert.equal(await availableTodayPage.locator('.today-service-preparing').count(), 0,
      'A failed status never covers an edition already dated today');
    assert.equal(await availableTodayPage.locator('.today-listen-pill').count(), 1,
      'Today keeps the available briefing action');
    await capture(availableTodayPage, 'today-edition-failed-status-phone-light.png');
    await availableTodayPage.emulateMedia({ colorScheme: 'dark' });
    await capture(availableTodayPage, 'today-edition-failed-status-phone-dark.png');
    await availableTodayPage.emulateMedia({ colorScheme: 'light' });
    await availableTodayPage.setViewportSize({ width: 1440, height: 1000 });
    await capture(availableTodayPage, 'today-edition-failed-status-desktop-light.png');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
