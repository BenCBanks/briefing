require('../test_guard.cjs').enforceTestEnvironment();

// First-run preference and pending-job recovery; all responses use fictional fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, devices } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const typedWords = 'I follow payments and banking, with useful context.';
const profile = { version: 1, words: typedWords, minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payments'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' },
  { id: 'banking', label: 'Banking', scope: 'Banks and lending', include: ['banking'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'banking' },
] };
const sample = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')).editions.find(item => item.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const edition = { ...structuredClone(sample), id: 'first-run-resume-fixture', revision_id: 'first-run-resume-fixture-r1',
  script_hash: 'fictional-first-run-resume', generated: true, local_date: '2026-11-19', generated_at: '2026-11-19T06:00:00-05:00' };

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name) });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(10000);
    let generateCalls = 0;
    let polls = 0;
    let ready = false;
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
      : route.fulfill({ json: { ok: true } }));
    await page.route('**/api/interpret', async route => {
      const request = route.request().postDataJSON();
      await route.fulfill({ json: { profile: { ...profile, words: request.words }, question: null } });
    });
    await page.route('**/api/generate', async route => {
      generateCalls++;
      await route.fulfill({ json: { job: { id: 'first-run-resume-job' } } });
    });
    await page.route('**/api/generate/first-run-resume-job', async route => {
      polls++;
      if (polls === 1) return route.fulfill({ json: { status: 'running', progress: { stage: 'writing', message: 'Writing story 1 of 3' } } });
      if (polls === 2) return route.fulfill({ json: { status: 'running', progress: { stage: 'writing', message: 'Writing story 2 of 3' } } });
      ready = true;
      return route.fulfill({ json: { status: 'complete', progress: { stage: 'checking' }, edition_ready: { edition, sources } } });
    });
    await page.route('**/api/edition**', route => ready
      ? route.fulfill({ json: { generated: true, edition, sources } })
      : route.fulfill({ status: 404, json: { error: 'edition_not_ready' } }));

    await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'domcontentloaded' });
    const words = page.locator('#interest-words');
    const time = page.getByLabel('Preferred time');
    await words.waitFor();
    assert.equal(await time.inputValue(), '05:00', 'daily preference starts at 5 am');
    assert.equal(await page.locator('#interest-time-note').innerText(), 'Your briefing is ready by 6 am ET. Your preferred time is saved on this device.',
      'The selected reading time is clearly distinguished from the ready-by time');
    await time.fill('07:30'); await time.dispatchEvent('change');
    await page.reload({ waitUntil: 'domcontentloaded' });
    assert.equal(await page.getByLabel('Preferred time').inputValue(), '07:30', 'time choice survives a reload');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:daily-time')), '07:30');

    await page.locator('#interest-words').fill(typedWords);
    await capture(page, 'describe-light-390.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'describe-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('button', { name: 'Build my briefing' }).click();
    await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
    await page.getByText('Payments', { exact: true }).waitFor();
    await page.getByText('Banking', { exact: true }).waitFor();
    await page.getByText('Writing story 1 of 3', { exact: true }).waitFor();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('briefing:v1:first-run-build') || 'null')?.jobId === 'first-run-resume-job');
    assert.ok(await page.getByRole('heading', { name: 'Your first briefing is being written' }).count() === 0,
      'the first-run progress screen stays in view while the job is active');
    await capture(page, 'building-light-390.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'building-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('Writing story 2 of 3', { exact: true }).waitFor();
    await page.locator('[data-nav="today"]').click();
    await page.getByRole('heading', { name: 'Your first briefing is being written' }).waitFor();
    await capture(page, 'today-preparing-light-390.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'today-preparing-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.today-page').waitFor();
    assert.equal(generateCalls, 1, 'reload resumes the existing job instead of starting another');
    assert.equal(await page.locator('.today-story-row').count(), edition.sections.length, 'Today shows the completed first edition');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:first-run-build')), null, 'pending state clears after Today is ready');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:daily-time')), '07:30', 'selected time remains saved after the build');
    await capture(page, 'today-light-390.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'today-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-story-row').count(), edition.sections.length, 'completed Today remains populated after another reload');
    assert.equal(generateCalls, 1);
    console.log('First run: saved time, interpreted starter profile, pending job resumed after reload, and completed Today persisted.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
