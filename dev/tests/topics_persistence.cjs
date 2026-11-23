require('../test_guard.cjs').enforceTestEnvironment();

// Topic edits persist through the profile API and survive a fresh page load.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const profile = {
  version: 1,
  words: 'I follow real-time payment rules and local banks. Skip crypto.',
  minutes: 12,
  background: null,
  muted: [],
  topics: [
    { id: 'payments', label: 'Payments', scope: 'Payment systems and regulation', include: ['real-time payment rules'], exclude: ['crypto'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'real-time payment rules' },
    { id: 'banking', label: 'Banking', scope: 'Local banks and lending', include: ['local banks'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'local banks' },
  ],
};
const profileKey = value => JSON.stringify({ words: value.words || '', minutes: value.minutes || 12,
  topics: value.topics.map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude,
    depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })), muted: value.muted || [] });
const copy = value => JSON.parse(JSON.stringify(value));

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(8000);
    let serverProfile = copy(profile);
    const profileWrites = [];
    await page.addInitScript(({ value, key }) => {
      if (!localStorage.getItem('briefing:v1:interest-profile')) localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(value));
      if (!localStorage.getItem('briefing:v1:built-profile-key')) localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { value: profile, key: profileKey(profile) });
    await page.route('**/api/profile', route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: { profile: copy(serverProfile) } });
      serverProfile = copy(route.request().postDataJSON().profile);
      profileWrites.push(copy(serverProfile));
      return route.fulfill({ json: { saved: true } });
    });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: 'edition_not_found' } }));
    await page.route('**/api/generate', route => route.fulfill({ status: 503, json: { error: 'fixture_generation_unavailable' } }));

    await page.goto(`${baseURL}/#interests`, { waitUntil: 'networkidle' });
    await page.locator('.interest-topic-row').filter({ has: page.getByRole('heading', { name: 'Banking', exact: true }) }).waitFor();
    const bankingRow = page.locator('.interest-topic-row').filter({ has: page.getByRole('heading', { name: 'Banking', exact: true }) });
    const removeWrite = page.waitForResponse(response => response.url().endsWith('/api/profile') && response.request().method() === 'POST');
    await bankingRow.getByRole('button', { name: 'Remove topic' }).click();
    await removeWrite;
    await page.waitForFunction(() => {
      const value = JSON.parse(localStorage.getItem('briefing:v1:interest-profile') || '{}');
      return value.topics?.length === 1 && value.muted?.some(item => item.label === 'Banking');
    });
    assert.equal(serverProfile.topics.some(topic => topic.id === 'banking'), false, 'Removing a topic is saved to the server profile');

    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.interests-page').waitFor();
    await page.locator('.interest-muted').getByText('Banking', { exact: true }).waitFor();
    assert.equal(await page.getByRole('heading', { name: 'Banking', exact: true }).count(), 0, 'A removed topic stays out after reload');
    const addWrite = page.waitForResponse(response => response.url().endsWith('/api/profile') && response.request().method() === 'POST');
    await page.locator('.interest-muted').getByRole('button', { name: 'Add back' }).click();
    await addWrite;
    await page.waitForFunction(() => {
      const value = JSON.parse(localStorage.getItem('briefing:v1:interest-profile') || '{}');
      return value.topics?.some(topic => topic.id === 'banking') && !value.muted?.some(item => item.label === 'Banking');
    });
    assert.equal(serverProfile.topics.some(topic => topic.id === 'banking'), true, 'Adding a topic back is saved to the server profile');

    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.interest-topic-row').filter({ has: page.getByRole('heading', { name: 'Banking', exact: true }) }).waitFor();
    await page.locator('[data-nav="topics"]').click();
    await page.locator('.topic-list').waitFor();
    const paymentsRow = page.locator('.topic-row').filter({ has: page.getByRole('heading', { name: 'Payments', exact: true }) });
    const pauseWrite = page.waitForResponse(response => response.url().endsWith('/api/profile') && response.request().method() === 'POST');
    await paymentsRow.getByRole('switch', { name: 'Payments daily updates' }).click();
    await pauseWrite;
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile') || '{}').muted?.some(item => item.label === 'Payments'));
    assert.equal(serverProfile.muted.some(item => item.label === 'Payments'), true, 'Pausing a topic is saved to the server profile');

    await page.reload({ waitUntil: 'networkidle' });
    const pausedPayments = page.locator('.topic-row').filter({ has: page.getByRole('heading', { name: 'Payments', exact: true }) });
    await pausedPayments.getByRole('switch', { name: 'Payments daily updates' }).waitFor();
    assert.equal(await pausedPayments.getByRole('switch', { name: 'Payments daily updates' }).getAttribute('aria-checked'), 'false', 'The paused state survives reload');
    assert.equal(await page.locator('.topics-skipping').count(), 0, 'Topic rows avoid redundant negative exclusion copy');
    const reloaded = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile')));
    assert.deepEqual(reloaded.topics.find(topic => topic.id === 'payments').exclude, ['crypto']);
    assert.ok(profileWrites.length >= 3, 'remove, add back, and pause all persist through the profile API');
    console.log('Topics persistence: remove, add back, pause, and exclusions survive reload with fixture profile API.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
