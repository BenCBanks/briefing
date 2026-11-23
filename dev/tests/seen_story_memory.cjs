require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8942';
const edition = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'))
  .editions.find(row => row.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const profileId = 'd'.repeat(24);
const seenEdition = { ...edition, id: 'fictional-seen-edition', revision_id: 'fictional-seen-edition-r1',
  generated: true, profile_id: profileId, local_date: '2026-11-18',
  sections: edition.sections.slice(0, 3).map((section, index) => ({ ...section,
    id: `seen-story-${index}`, topic_ids: ['payments'] })) };

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const seenRequests = [];
    let resolveSeen;
    const seenPost = new Promise(resolve => { resolveSeen = resolve; });
    await seedInterestProfile(page, DEFAULT_PROFILE);
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ json: { profile: DEFAULT_PROFILE } })
      : route.fulfill({ json: { saved: true } }));
    await page.route('**/api/edition**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/edition/seen' && route.request().method() === 'POST') {
        seenRequests.push(route.request().postDataJSON());
        resolveSeen();
        return route.fulfill({ json: { seen: true, changed: true } });
      }
      return route.fulfill({ json: { generated: true, edition: seenEdition, sources } });
    });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    await page.locator(`.today-contents .edition[data-edition="${seenEdition.id}"]`).first().waitFor();
    await seenPost;
    assert.equal(seenRequests.length, 1, 'Displaying a generated daily edition records one seen marker');
    assert.deepEqual(seenRequests[0], {
      profile_id: profileId, edition_id: seenEdition.id, revision_id: seenEdition.revision_id,
    });
    if (process.env.BRIEFING_SHOT_DIR) {
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-seen-memory-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-seen-memory-dark.png') });
    }
    await page.locator('[data-nav=topics]').click();
    await page.locator('.topics-page').waitFor();
    await page.locator('[data-nav=today]').click();
    await page.locator('.today-page').waitFor();
    await page.waitForTimeout(100);
    assert.equal(seenRequests.length, 1, 'Rerendering the same edition does not repeat the marker request');
    console.log('Seen-story memory: Today marks the displayed generated edition once for its reader profile.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
