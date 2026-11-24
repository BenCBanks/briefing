require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8935';
const editions = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')).editions;
const sampleDaily = editions.find(item => item.kind === 'daily');
const topicId = 'custom-fernvale-housing';
const profile = { version: 1, words: 'Follow payments.', minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payments'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' },
] };
const section = { ...structuredClone(sampleDaily.sections[0]), id: 'fictional-html-preview-story', heading: 'Fernvale housing update',
  body: 'What happened: <p>Rates &amp; savings updated.</p> <a href="javascript:alert(1)">Read details</a>.',
  topic_ids: [topicId], source_ids: [] };
const edition = { ...sampleDaily, id: 'fictional-html-preview-edition', revision_id: 'fictional-html-preview-r1', script_hash: 'fictional-html-preview-hash',
  generated: true, local_date: '2026-11-17', generated_at: '2026-11-17T06:02:00-05:00', title: 'Fernvale housing briefing',
  coverage: [{ topic_id: topicId, label: topicId, state: 'covered' }], sections: [section] };

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await seedInterestProfile(page, profile);
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ json: { profile } }) : route.fulfill({ json: { saved: true } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources: [] } }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });

    const row = page.locator('.today-story-row').first();
    await row.waitFor();
    assert.equal(await page.locator('.story-kicker').count(), 0, 'Single-topic editions omit the repeated kicker');
    await row.locator('.today-transcript').evaluate(details => { details.open = true; });
    assert.equal(await row.locator('.edition-section p').first().innerText(), 'Rates & savings updated. Read details.', 'Inline text strips tags and decodes ampersands to plain text');
    assert.equal(await row.locator('.edition-section p p,.edition-section p a,.edition-section script').count(), 0, 'Raw paragraph and link markup never creates DOM elements');
    assert.match(await row.locator('.today-chapter-title').getAttribute('aria-label'), /^Play from chapter 1: Fernvale housing/, 'Accessible chapter labels use plain topic words');
    assert.doesNotMatch(await page.locator('.today-page').innerText(), /custom-fernvale-housing/, 'Today never displays the internal topic ID');
    const accessibleNames = await page.locator('.today-page [aria-label]').evaluateAll(elements => elements.map(element => element.getAttribute('aria-label')).join(' '));
    assert.doesNotMatch(accessibleNames, /custom-fernvale-housing/, 'Today accessible names never expose the internal topic ID');
    console.log('Topic labels and preview safety: plain custom-topic labels, stripped HTML, safe links and decoded ampersands passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
