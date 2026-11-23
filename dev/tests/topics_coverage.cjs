require('../test_guard.cjs').enforceTestEnvironment();

// Limited-coverage copy on Topics comes from the current edition's coverage notes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const editionPack = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'));
const sourcePack = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8'));

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const profile = { version: 1, words: 'Savings rates, digital privacy and local transport.', minutes: 12, background: null, muted: [], topics: [
      { id: 'banking', label: 'Savings rates', scope: 'Deposit rates', include: ['savings rates'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'savings rates' },
      { id: 'digital', label: 'Digital privacy', scope: 'Data protection', include: ['digital privacy'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'digital privacy' },
      { id: 'custom-transport', label: 'Local transport', scope: 'Buses and rail', include: ['transport'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'transport' },
    ] };
    const builtProfileKey = JSON.stringify({ words: profile.words, minutes: profile.minutes,
      topics: profile.topics.map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude, depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })), muted: profile.muted });
    await page.addInitScript(({ value, key }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(value));
      localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { value: profile, key: builtProfileKey });
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'profile_not_found' } }));
    const daily = structuredClone(editionPack.editions.find(edition => edition.kind === 'daily'));
    daily.id = 'coverage-generated-fixture';
    daily.revision_id = 'coverage-generated-fixture-r1';
    daily.script_hash = 'coverage-generated-fixture-script';
    daily.generated = true;
    daily.local_date = '2026-11-18';
    daily.generated_at = '2026-11-18T06:00:00-05:00';
    daily.coverage.find(item => item.topic_id === 'banking').state = 'limited';
    daily.coverage.find(item => item.topic_id === 'banking').reason = 'Only one candidate passed source checks.';
    daily.coverage.find(item => item.topic_id === 'digital').state = 'covered';
    await page.route('**/api/edition**', route => route.fulfill({ json: {
      generated: true, edition: daily, sources: sourcePack.sources,
    } }));
    await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
    const rows = page.getByRole('listitem');
    const limitedRow = rows.filter({ hasText: 'Savings rates' });
    const limitedHint = limitedRow.getByText('Limited coverage today', { exact: true });
    await limitedHint.waitFor();
    await limitedHint.click();
    assert.match(await limitedRow.innerText(), /Only one candidate passed source checks\./, 'Limited status exposes its edition-supplied reason');
    const coveredRow = rows.filter({ hasText: 'Digital privacy' });
    assert.equal(await coveredRow.getByText('Plenty', { exact: true }).count(), 1, 'Covered status is shown plainly');
    const unreportedRow = rows.filter({ hasText: 'Local transport' });
    assert.equal(await unreportedRow.getByText(/coverage/i).count(), 0, 'Missing coverage stays unlabelled rather than implying it is limited');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Coverage copy fits the phone viewport');
    console.log('Topics coverage: limited label and reason follow the edition coverage note only for the affected topic.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
