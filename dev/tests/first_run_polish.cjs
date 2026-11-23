require('../test_guard.cjs').enforceTestEnvironment();

// First-run onboarding polish uses fictional profile/edition fixtures and mocked APIs only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, devices } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const sampleEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'))
  .editions.find(item => item.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const outputDir = process.env.BRIEFING_SHOT_DIR;
const typedWords = 'I follow payments every day.';
const exampleSentence = 'I follow payments and banking, especially real-time payment rules and savings rates.';
const profile = { version: 1, words: `${typedWords} ${exampleSentence}`, minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Real-time payment rules', include: ['payments', 'real-time payment rules'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments and real-time payment rules' },
] };
const edition = { ...structuredClone(sampleEdition), id: 'first-run-polish-edition', revision_id: 'first-run-polish-r1',
  script_hash: 'fictional-first-run-polish', generated: true, local_date: '2026-11-18', generated_at: '2026-11-18T06:02:00-05:00',
  title: 'A fictional briefing about payment rules', coverage: [{ topic_id: 'payments', state: 'covered' }],
  sections: sampleEdition.sections.filter(section => section.topic_ids.includes('payments')) };
const editionResponse = { generated: true, edition, sources };

async function screenshot(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.screenshot({ path: path.join(outputDir, name), fullPage: false, animations: 'disabled' });
}

async function newPhone(browser, theme = 'light') {
  return browser.newPage({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York', colorScheme: theme });
}

async function installBaseMocks(page, { fail = false } = {}) {
  let ready = false;
  await page.route('**/api/profile', route => route.request().method() === 'GET'
    ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
    : route.fulfill({ json: { ok: true } }));
  await page.route('**/api/edition*profile=active*', route => ready
    ? route.fulfill({ json: editionResponse })
    : route.fulfill({ status: 404, json: { error: 'edition_not_ready' } }));
  await page.route('**/api/interpret', async route => {
    await new Promise(resolve => setTimeout(resolve, 220));
    const request = route.request().postDataJSON();
    return route.fulfill({ json: { profile: { ...profile, words: request.words }, question: null } });
  });
  let generateCalls = 0;
  await page.route('**/api/generate', async route => {
    generateCalls += 1;
    await new Promise(resolve => setTimeout(resolve, 300));
    return fail && generateCalls === 1
      ? route.fulfill({ status: 503, json: { error: 'temporarily_unavailable' } })
      : route.fulfill({ json: { job: { id: 'first-run-polish-job' } } });
  });
  await page.route('**/api/generate/first-run-polish-job', async route => {
    // The first two polls expose honest, observable work; the last returns the fixture edition.
    if (!installBaseMocks.polls) installBaseMocks.polls = 0;
    installBaseMocks.polls += 1;
    const poll = installBaseMocks.polls;
    if (poll === 1) return route.fulfill({ json: { status: 'running', progress: { stage: 'finding_stories', found: 2 } } });
    if (poll === 2) return route.fulfill({ json: { status: 'running', progress: { stage: 'writing', message: 'Writing story 2 of 3' } } });
    ready = true;
    return route.fulfill({ json: { status: 'complete', progress: { stage: 'checking' }, edition_ready: { edition } } });
  });
  return { getGenerateCalls: () => generateCalls };
}

async function successfulFirstRun(browser) {
  installBaseMocks.polls = 0;
  const page = await newPhone(browser, 'light');
  page.setDefaultTimeout(8000);
  await installBaseMocks(page);
  await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'What do you want to follow?' }).waitFor();
  assert.equal(await page.locator('.interest-example-chip').count(), 3, 'Describe shows three example chips');
  assert.equal(await page.getByText('Your daily 5-minute briefing is ready by 6 am ET.', { exact: true }).count(), 1,
    'Describe promises the finite five-minute daily briefing and its ready time');
  assert.equal(await page.getByText('Ask for an episode on anything, any time.', { exact: true }).count(), 1,
    'Describe explains that episodes are available on demand for any topic');
  assert.equal(await page.locator('.interest-delivery-note p').count(), 2, 'The two product promises stay in two short lines');
  const field = page.locator('#interest-words');
  const button = page.getByRole('button', { name: 'Build my briefing' });
  assert.equal(await button.isEnabled(), true);
  assert.equal(await button.isVisible(), true);
  assert.ok(await button.evaluate(el => { const box = el.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; }), 'primary action stays in the first viewport');
  assert.ok(await field.evaluate(el => parseFloat(getComputedStyle(el).fontSize) >= 16), 'field is at least 16px');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await screenshot(page, 'describe-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'describe-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await field.fill(typedWords);
  await page.getByRole('button', { name: 'Payments & banking' }).click();
  assert.equal(await field.inputValue(), `${typedWords} ${exampleSentence}`, 'example appends a sentence without replacing typed words');
  assert.equal(await page.getByRole('region', { name: 'Starter profile preview' }).getByRole('heading', { name: 'Starter profile preview' }).count(), 1);
  assert.deepEqual(await page.locator('.interest-starter-topic strong').allTextContents(), ['Payments', 'Banking'],
    'typed words and a suggestion immediately preview the starter profile');
  await screenshot(page, 'starter-profile-preview-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'starter-profile-preview-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await button.click();
  await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
  await page.locator('.build-progress-step[aria-current="step"]').filter({ hasText: 'Understanding your words' }).waitFor();
  await page.locator('.build-progress-step[aria-current="step"]').filter({ hasText: 'Finding today’s stories (2 found)' }).waitFor();
  await page.locator('.build-progress-step[aria-current="step"]').filter({ hasText: 'Writing story 2 of 3' }).waitFor();
  await screenshot(page, 'building-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'building-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.locator('.today-page').waitFor();
  assert.equal(await page.locator('.today-welcome-note').count(), 0, 'V3 greets once without a separate welcome banner');
  assert.equal(await page.locator('.today-story-row').count(), edition.sections.length, 'Today shows the generated first edition');
  await screenshot(page, 'today-welcome-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'today-welcome-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('.today-welcome-note').count(), 0, 'welcome is shown once for the first edition');
  await page.close();
}

async function failedFirstRun(browser) {
  installBaseMocks.polls = 0;
  const page = await newPhone(browser, 'light');
  page.setDefaultTimeout(8000);
  const mocks = await installBaseMocks(page, { fail: true });
  await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: 'edition_unavailable' } }));
  await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'networkidle' });
  await page.locator('#interest-words').fill(typedWords);
  await page.getByRole('button', { name: 'Build my briefing' }).click();
  await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
  await screenshot(page, 'building-failure-pending-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'building-failure-pending-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.getByRole('heading', { name: 'Today’s stories did not load' }).waitFor();
  assert.equal(await page.locator('.today-build-error').getByRole('button', { name: 'Retry' }).count(), 1, 'failed generation offers retry');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile'))?.words), typedWords,
    'interpreted profile remains saved after build failure');
  await screenshot(page, 'failure-retry-light-390.png');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, 'failure-retry-dark-390.png');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.locator('.today-build-error').getByRole('button', { name: 'Retry' }).click();
  await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
  await page.locator('.today-page').waitFor();
  assert.equal(mocks.getGenerateCalls(), 2, 'Retry starts a fresh mocked build');
  assert.equal(await page.locator('.today-story-row').count(), edition.sections.length, 'successful retry lands on the generated first edition');
  await page.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    await successfulFirstRun(browser);
    await failedFirstRun(browser);
    console.log('First-run polish: delivery explanation, append-only examples, honest delayed progress, one-time welcome, calm retry failure and phone light/dark captures passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
