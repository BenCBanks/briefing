require('../test_guard.cjs').enforceTestEnvironment();

// Generated history stays off Today and loads when the reader opens Earlier.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, seedInterestProfile } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const content = path.resolve(__dirname, '../../content');
const sample = JSON.parse(fs.readFileSync(path.join(content, 'editions.json'), 'utf8')).editions.find(item => item.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(content, 'sources.json'), 'utf8')).sources;
const current = { ...sample, id: 'lazy-history-current', revision_id: 'lazy-history-current-r1', generated: true,
  title: 'Fictional briefing today', local_date: '2026-11-17', coverage: [{ topic_id: 'payments', state: 'covered' }],
  sections: [
    { ...sample.sections[0], id: 'history-story-one', topic_ids: ['payments'], body: Array(110).fill('fictional').join(' ') },
    { ...sample.sections[1], id: 'history-story-two', topic_ids: ['payments'], body: Array(116).fill('fictional').join(' ') },
    { id: 'history-link', kind: 'link', heading: 'Related fictional link', topic_ids: ['payments'],
      source_ids: [...sample.sections[0].source_ids], body: Array(500).fill('link detail').join(' ') },
  ],
};
const older = { ...sample, id: 'lazy-history-earlier', revision_id: 'lazy-history-earlier-r1', generated: true,
  title: 'Fictional briefing from yesterday', local_date: '2026-11-16',
  corrections: [{ severity: 'material', revision_id: 'lazy-history-earlier-r1', previous_revision_id: 'lazy-history-earlier-r0',
    corrected_at: '2026-11-17T18:00:00Z', section_ids: [sample.sections[0].id],
    what_changed: 'A fictional date was clarified.', why_it_matters: 'The timing is easier to understand.' }] };

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const historyRequests = [];
    const todayPayloads = [];
    await seedInterestProfile(page, DEFAULT_PROFILE);
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE } }));
    await page.route('**/api/edition**', route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('history') === '1') {
        historyRequests.push('1');
        return route.fulfill({ json: { generated: true, edition: current, sources,
          history: [{ edition: older, sources }] } });
      }
      historyRequests.push(url.searchParams.get('history') || 'default');
      const payload = { generated: true, edition: current, sources };
      todayPayloads.push(payload);
      return route.fulfill({ json: payload });
    });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    assert.equal(historyRequests.length, 1, 'Today requests the current edition once');
    assert.ok(['0', 'default'].includes(historyRequests[0]), 'Today requests history=0 or relies on the lean server default');
    assert.equal(Object.hasOwn(todayPayloads[0], 'history'), false, 'Today receives no history payload');
    await page.locator('.today-earlier-briefings-link').click();
    await page.locator('.library-briefing-list').waitFor();
    await page.getByText(/Monday 16 November/).waitFor();
    assert.deepEqual(historyRequests, ['0', '1'], 'Earlier fetches history only after navigation');
    assert.equal(await page.locator('.library-history-status').count(), 0, 'Earlier status clears when history is loaded');
    const todayRow = page.locator('.library-briefing-list li').first();
    assert.match(await todayRow.innerText(), /Tuesday 17 November[\s\S]*2 stories · 2 min read/,
      'Earlier rows use the history edition date and count only readable stories for its read-time estimate');
    const yesterdayRow = page.locator('.library-briefing-list li').nth(1);
    assert.equal(await todayRow.locator('.library-status-tag.library-updated-link').count(), 0,
      'An unchanged current edition has no correction tag');
    assert.equal(await yesterdayRow.locator('.library-status-tag.library-updated-link').count(), 1,
      'A material correction on a history edition receives a correction link');
    console.log('Lazy edition history: Today receives current-only payload; Earlier requests and renders history on demand.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
