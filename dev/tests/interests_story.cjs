require('../test_guard.cjs').enforceTestEnvironment();

// Fictional Stage A coverage for interests editing, explicit suggestions and story feedback.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const auditionManifest = require('./fixtures/audition_manifest.json');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const sourceEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')).editions.find(item => item.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const generatedEdition = { ...sourceEdition, id: 'generated-story-feedback-test', revision_id: 'generated-story-r1', script_hash: 'fictional-story-test', generated: true, local_date: '2026-11-17', generated_at: '2026-11-17T06:02:00-05:00' };
const seededProfile = { version: 1, words: 'I follow payments and banking.', minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payment rails'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'I follow payments' },
  { id: 'banking', label: 'Banking', scope: 'Deposit rates and stability', include: ['deposits'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'and banking' },
] };
const buildKey = value => JSON.stringify({ words: value.words || '', minutes: value.minutes || 12,
  topics: value.topics.map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude, depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })), muted: value.muted || [] });

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: auditionManifest }));
    await page.route('**/api/voice', route => route.fulfill({ json: {} }));
    await page.route('**/api/profile', route => route.fulfill({ json: { saved: true } }));
    let generateRequests = 0;
    await page.route('**/api/generate', route => { generateRequests += 1; return route.fulfill({ status: 404, json: { error: 'offline' } }); });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ profileValue, key }) => { localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profileValue)); localStorage.setItem('briefing:v1:built-profile-key', key); }, { profileValue: seededProfile, key: buildKey(seededProfile) });
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition: generatedEdition, sources } }));
    await page.goto(`${baseURL}/#preferences`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Choose your voice' }).waitFor();
    await page.locator('.narrator-sample').first().waitFor();
    await page.getByRole('link', { name: /Edit in my own words/ }).click();
    await page.locator('.interests-page').waitFor();
    await page.getByRole('heading', { name: 'Here is what I heard' }).waitFor();
    await page.setViewportSize({ width: 195, height: 422 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Interests reflows at 200% zoom');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.match(await page.locator('.interest-topic-row').first().innerText(), /From your words: “I follow payments”/);
    await page.route('**/api/interpret', async route => route.fulfill({ json: { question: null, flags: ['instruction-like text removed'], profile: {
      version: 1, words: 'I follow payments weekly, headlines only, and banking. Keep it to 10 minutes.', minutes: 10, background: null, muted: [], topics: [
        { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payment rails'], exclude: [], depth: 'headline', cadence: 'weekly', mustFollow: false, sourcePhrase: 'payments weekly, headlines only' },
        { id: 'banking', label: 'Banking', scope: 'Deposits, lending and financial stability', include: ['deposits'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'banking' },
      ],
    } } }));
    await page.locator('textarea[name=words]').fill('I follow payments weekly, headlines only, and banking. Keep it to 10 minutes.');
    const generationRequest = page.waitForResponse(response => response.url().endsWith('/api/generate') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Update my interests' }).click();
    await generationRequest;
    await page.waitForFunction(() => document.querySelectorAll('.interest-topic-row').length === 2);
    assert.equal(await page.evaluate(() => location.hash), '#interests', 'The reader stays in the interest editor while rebuilding in the background');
    assert.match(await page.locator('.interest-interpreter-flags').innerText(), /instruction-like text removed/i,
      'Settings uses the shared provider and displays returned processing flags');
    assert.equal(await page.locator('.interest-topic-row').count(), 2, 'editing words reinterprets selected interests');
    assert.equal(await page.locator('.interest-field').first().locator('select').inputValue(), 'headline');
    assert.equal(await page.locator('.interest-field').nth(1).locator('select').inputValue(), 'weekly');
    assert.match(await page.locator('.interest-topic-row').first().innerText(), /From your words: “payments weekly/i);

    await page.evaluate(() => localStorage.setItem('briefing:v1:signals', JSON.stringify(
      Array.from({ length: 4 }, (_, index) => ({ ts: Date.now() - index, type: 'skip', storyId: `banking-${index}`, topicId: 'banking' }))
    )));
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Move Banking to weekly' }).waitFor();
    await page.getByRole('button', { name: 'Not now' }).click();
    assert.equal(await page.locator('.suggestion-row').count(), 0, 'Not now snoozes the suggestion');
    const snoozes = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-snoozes')));
    assert.ok(snoozes['cadence:banking:weekly'] > Date.now());

    await page.evaluate(() => localStorage.setItem('briefing:v1:signals', JSON.stringify(
      [{ ts: Date.now(), type: 'more', storyId: 'banking-story', topicId: 'banking' }]
    )));
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Show more depth on Banking' }).waitFor();
    await page.locator('.suggestion-row').getByRole('button', { name: 'Accept' }).click();
    assert.equal(await page.locator('.interest-topic-row').nth(1).locator('select').first().inputValue(), 'deeper', 'Accept applies the suggestion');

    // Wait for the failed background edit before retrying with an available fixture.
    await page.locator('[data-nav=topics]').click();
    await page.locator('.topics-update-error').waitFor();
    // Finish a fresh build before testing feedback; an offline edit no longer permits old content.
    let revision = 1;
    await page.route('**/api/generate', route => {
      generatedEdition.revision_id = 'generated-story-r' + (++revision);
      return route.fulfill({ json: { job_id: 'feedback-ready' } });
    });
    await page.route('**/api/generate/feedback-ready', route => route.fulfill({ json: { status: 'complete', edition_ready: { edition: generatedEdition } } }));
    await page.locator('.topics-update').click();
    await page.locator('.today-page').waitFor();
    await page.route('**/api/generate', route => route.fulfill({ status: 503, json: { error: 'offline' } }));

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await page.locator('.today-full-story-link').first().click();
    await page.locator('.why-story-trigger').first().click();
    await page.locator('.why-story-dialog').waitFor();
    assert.match(await page.locator('.why-story-sheet').innerText(), /because you wrote/i);
    await page.setViewportSize({ width: 195, height: 422 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Why this story reflows at 200% zoom');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.why-story-dialog').getByRole('button', { name: 'More like this' }).click();
    await page.locator('.why-story-dialog').waitFor({ state: 'detached' });
    let signals = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')));
    assert.equal(signals.at(-1).type, 'more');
    assert.deepEqual(Object.keys(signals.at(-1)).sort(), ['storyId', 'topicId', 'ts', 'type']);

    await page.locator('.story-sources [data-source]').first().click();
    await page.locator('.source-title').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.why-story-trigger').first().waitFor();

    await page.locator('.why-story-trigger').first().click();
    await page.getByRole('button', { name: 'Not interested in this topic' }).click();
    assert.equal(await page.getByRole('button', { name: 'Confirm mute' }).isVisible(), true, 'mute asks for confirmation');
    await page.getByRole('button', { name: 'Confirm mute' }).click();
    const profile = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile')));
    assert.ok(profile.muted.some(item => item.label === 'Payments'));
    assert.ok(!profile.topics.some(item => item.id === 'payments'));
    signals = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')));
    assert.equal(signals.at(-1).type, 'mute');

    await page.locator('.reader-back').click();
    await page.locator('.today-build-progress').waitFor();
    assert.equal(await page.locator('.today-page').count(), 0, 'A topic change cannot expose the old briefing, even when generation fails');
    await page.locator('[data-nav=topics]').click();
    await page.locator('.settings-link').click();
    await page.getByRole('link', { name: /Edit in my own words/ }).click();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Clear what has been learned' }).click();
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:signals')), null);
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:interest-snoozes')), null);
    assert.ok(await page.evaluate(() => localStorage.getItem('briefing:v1:interest-profile')), 'clearing learned signals retains written interests');
    assert.deepEqual(errors, [], `browser errors: ${errors.join('; ')}`);
    console.log('Interests and story feedback browser checks passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
