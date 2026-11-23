require('../test_guard.cjs').enforceTestEnvironment();

// Empty-browser first-run path with a fictional profile and generated edition.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, devices } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const sampleEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'))
  .editions.find(item => item.kind === 'daily');
const sourceFile = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8'));
const words = 'I follow payment rules every day. Skip crypto.';
const profile = { version: 1, words, minutes: 12, background: null, muted: [{ label: 'Crypto' }], topics: [
  { id: 'payments', label: 'Payments', scope: 'Payment rules', include: ['payment rules'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payment rules every day' },
] };
const generatedEdition = { ...structuredClone(sampleEdition), id: 'first-run-return-edition', revision_id: 'first-run-return-r1',
  script_hash: 'fictional-first-run-flow', generated: true, local_date: '2026-11-17', generated_at: '2026-11-17T06:02:00-05:00',
  title: 'A fictional briefing about payment rules', coverage: [{ topic_id: 'payments', label: 'Payments', state: 'covered' }],
  sections: sampleEdition.sections.filter(section => section.topic_ids.includes('payments')) };
const generatedResponse = { generated: true, edition: generatedEdition, sources: sourceFile.sources };

async function screenshot(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name), fullPage: false });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(7000);
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
      : route.fulfill({ json: { ok: true } }));
    await page.route('**/api/interpret', route => route.fulfill({ json: { profile, question: null } }));
    await page.route('**/api/generate', route => route.fulfill({ json: { job: { id: 'first-run-return-job' } } }));
    await page.route('**/api/generate/first-run-return-job', async route => {
      await new Promise(resolve => setTimeout(resolve, 250));
      return route.fulfill({ json: {
        status: 'complete', progress: { stage: 'scoring', completed: 1, total: 1 }, edition_ready: { edition: generatedEdition },
      } });
    });
    await page.route('**/api/edition*profile=active*', route => route.fulfill({ json: generatedResponse }));

    await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'networkidle' });
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:interest-profile')), null, 'First run starts without a saved local profile');
    assert.deepEqual(await page.evaluate(() => ({ width: innerWidth, height: innerHeight })), { width: 390, height: 844 }, 'QA uses the requested phone viewport');
    await page.getByRole('heading', { name: 'What do you want to follow?' }).waitFor();
    const field = page.locator('#interest-words');
    const build = page.getByRole('button', { name: 'Build my briefing' });
    assert.equal(await build.isVisible(), true, 'Build is visible in the 390×844 first viewport');
    assert.ok(await build.evaluate(button => { const box = button.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; }), 'Build sits fully inside the first viewport');
    assert.ok(await field.evaluate(textarea => Number.parseFloat(getComputedStyle(textarea).fontSize) >= 16), 'Sentence field uses a no-zoom phone font size');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'First-run has no horizontal overflow');
    await screenshot(page, 'first-run-describe-empty-390.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'first-run-describe-empty-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await field.focus();
    assert.equal(await page.evaluate(() => window.visualViewport?.scale || 1), 1, 'Focusing the sentence box does not zoom the phone viewport');
    await field.fill(words);
    await screenshot(page, 'first-run-describe-390.png');
    await build.click();
    await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
    await page.getByText('Payment rules', { exact: true }).waitFor();
    await screenshot(page, 'first-run-building-390.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'first-run-building-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('heading', { name: 'Your daily briefing' }).waitFor();
    await page.locator('.today-chapter-title').waitFor();
    assert.equal(await page.locator('.today-story-row').count(), 1, 'Today shows only the story built for this profile');
    assert.equal(await page.locator('.story-kicker').count(), 0, 'Single-topic editions omit the repeated kicker');
    await screenshot(page, 'first-run-today-390.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'first-run-today-dark-390.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await page.locator('.today-full-story-link').click();
    await page.locator('.reader-story-header h1').waitFor();
    assert.equal(await page.locator('.reader-back').innerText(), '‹ Today', 'The story names Today as its return parent');
    await screenshot(page, 'first-run-story-390.png');
    await page.locator('.reader-back').click();
    await page.locator('.today-page h1').waitFor();
    assert.equal(await page.locator('.today-story-row').count(), 1, 'Back restores the built Today story list');
    await screenshot(page, 'first-run-back-390.png');
    console.log('First-run story return: clean profile, fictional interpretation/build, Today story, and Back passed at 390×844.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
