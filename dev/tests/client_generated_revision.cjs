require('../test_guard.cjs').enforceTestEnvironment();

// Keep the newest client-visible generated edition when history repeats its ID.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, builtProfileKey } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const content = path.resolve(__dirname, '../../content');
const sample = JSON.parse(fs.readFileSync(path.join(content, 'editions.json'), 'utf8')).editions.find(item => item.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(content, 'sources.json'), 'utf8')).sources;
const current = {
  ...sample, id: 'same-day-generated-edition', revision_id: 'generated-20261117T173800Z-current', generated: true,
  generated_at: '2026-11-17T17:38:00Z', local_date: '2026-11-17', title: 'Latest fictional generated edition',
  coverage: [{ topic_id: 'payments', label: 'Payments', state: 'covered' }],
  sections: sample.sections.slice(0, 2).map(section => ({ ...section, topic_ids: ['payments'] })),
};
const older = {
  ...current, revision_id: 'generated-20261117T163300Z-older', generated_at: '2026-11-17T16:33:00Z',
  title: 'Older fictional generated edition',
};
const secondIdSameDate = { ...older, id: 'same-date-second-id', revision_id: 'generated-20261117T150000Z-second-id',
  generated_at: '2026-11-17T15:00:00Z', title: 'Older same-date separate ID' };
const responseSources = sources.map((source, index) => index === 0
  ? { ...source, note: 'Reviewed on 2026-11-17T14:35:00Z for this fictional test.' }
  : source);
const response = { generated: true, edition: current, sources: responseSources,
  history: [{ edition: older, sources: responseSources }, { edition: secondIdSameDate, sources: responseSources }] };

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.addInitScript(({ profile, key }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
      localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { profile: DEFAULT_PROFILE, key: builtProfileKey(DEFAULT_PROFILE) });
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: response }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-page h1').innerText(), 'Your daily briefing',
      'Today selects the latest same-ID generated edition rather than entering the build flow');
    assert.equal(await page.locator('.today-contents > ol > li').count(), 2);
    assert.doesNotMatch(await page.locator('main').innerText(), /did not load|Building your briefing/i);
    await page.locator('.today-earlier-briefings-link').click();
    await page.locator('.library-briefing-list').waitFor();
    assert.equal(await page.locator('.library-briefing-link').count(), 1,
      'Same-date revisions collapse into one current briefing in Library');
    assert.equal(await page.locator('.library-list .library-kind-label', { hasText: 'Weekly recap' }).count(), 0,
      'Library does not offer the fictional weekly sample to a profiled reader without a generated recap');
    await page.goto(`${baseURL}/#weekly`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'No weekly recap yet' }).waitFor();
    assert.equal(await page.locator('.weekly-recap-page').count(), 0,
      'A profiled reader without a generated recap never sees the fictional sample recap via a direct route');
    await page.goto(`${baseURL}/#source/${encodeURIComponent(responseSources[0].id)}`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'What this supports' }).waitFor();
    const note = await page.locator('main > p').allInnerTexts();
    assert.ok(note.some(text => text.includes('17 Nov') && !text.includes('2026-11-17T')),
      'Source support notes show a plain date instead of a raw ISO timestamp');
    console.log('Generated revision resolution: latest same-ID edition appears on Today and Library without a false build state.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
