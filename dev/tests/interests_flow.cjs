require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const auditionManifest = require('./fixtures/audition_manifest.json');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8935';

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: auditionManifest }));
    const seeded = { version: 1, words: 'I follow savings rates and digital privacy.', minutes: 12, background: null, muted: [], behavior_weights: { banking: 1.3, digital: 0.8 }, topics: [
      { id: 'banking', label: 'Savings rates', scope: 'Deposit rates and changes', include: ['savings rates'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'savings rates' },
      { id: 'digital', label: 'Digital privacy', scope: 'Identity and data security', include: ['digital privacy'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'digital privacy' },
    ] };
    const profilePosts = [];
    await page.addInitScript(profile => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
      localStorage.setItem('briefing:v1:signals', JSON.stringify([{ ts: Date.now(), type: 'save', storyId: 'fictional', topicId: 'banking' }]));
    }, seeded);
    await page.route('**/api/profile', route => {
      if (route.request().method() === 'GET') return route.fulfill({ status: 404, json: { error: 'profile_not_found' } });
      profilePosts.push(route.request().postDataJSON().profile);
      return route.fulfill({ json: { saved: true } });
    });
    await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
    await page.locator('.topic-list').waitFor();
    assert.deepEqual(await page.locator('.topic-label').allTextContents(), ['Savings rates', 'Digital privacy'], 'Topics lists the saved profile topics');
    await page.goto(`${baseURL}/#preferences`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Settings' }).waitFor();
    await page.locator('.narrator-sample').first().waitFor();
    assert.match(await page.locator('.preferences-interests').innerText(), /Savings rates.*Digital privacy/);
    assert.equal(await page.locator('.settings-list input[type=checkbox]').count(), 0, 'Settings has no fixed topic checklist');
    assert.doesNotMatch(await page.locator('#content').innerText(), /one to five topics|fictional sample is fixed/i);
    assert.match(await page.locator('.preferences-voice').innerText(), /Narrator.*Sample A/s);
    assert.equal(await page.locator('.narrator-sample').count(), 6, 'Settings lists the six narrator audition samples');
    const resetSaved = page.waitForResponse(response => response.url().endsWith('/api/profile') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Reset topic balance' }).click();
    await resetSaved;
    await page.waitForFunction(() => localStorage.getItem('briefing:v1:signals') === null);
    assert.deepEqual(profilePosts.at(-1)?.behavior_weights, {}, 'reset sends an empty, neutral weighting summary');
    assert.doesNotMatch(JSON.stringify(profilePosts.at(-1)), /fictional/, 'raw signal events are never synced');
    assert.match(await page.locator('.preferences-behavior').innerText(), /reset to even shares/i);

    let calls = 0;
    let generateRequests = 0;
    await page.route('**/api/generate', route => { generateRequests += 1; return route.fulfill({ status: 503, json: { error: 'fixture unavailable' } }); });
    await page.route('**/api/interpret', async route => {
      calls += 1;
      await route.fulfill({ json: { question: null, profile: { version: 1, words: 'Follow payments every day.', minutes: 12, background: null, muted: [], topics: [
        { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payment systems'], exclude: [], depth: 'standard', cadence: 'weekly', mustFollow: true, sourcePhrase: 'payments every day' },
      ] } } });
    });
    await page.goto(`${baseURL}/#interests`, { waitUntil: 'networkidle' });
    await page.locator('textarea[name=words]').fill('Follow payments every day.');
    const generationRequest = page.waitForResponse(response => response.url().endsWith('/api/generate') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Update my interests' }).click();
    await generationRequest;
    await page.waitForFunction(() => document.querySelectorAll('.interest-topic-row').length === 1);
    assert.equal(calls, 1, 'Settings Interests uses the async /api/interpret adapter');
    assert.equal(generateRequests, 1, 'Saving a changed interpreted profile starts a rebuild automatically');
    const updated = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile')));
    assert.deepEqual(updated.topics.map(topic => topic.id), ['payments']);
    assert.equal(updated.topics[0].cadence, 'daily', 'Remote weekly cadence is ignored unless the reader said weekly');
    assert.equal(updated.topics[0].mustFollow, false, 'Remote must-follow is ignored unless the reader said it');
    console.log('Interests: saved topics, async interpretation, cadence/must-follow constraints and automatic profile rebuild passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
