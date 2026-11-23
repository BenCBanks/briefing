require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8963';
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/legacy-state/generated', name), 'utf8'));
const edition = structuredClone(fixture('editions.json').editions[0]);
const sources = fixture('sources.json').sources;
const profile = {
  ...DEFAULT_PROFILE,
  words: 'Fictional payments and public health updates.',
  topics: ['Payments', 'Public health', 'Workplace policy'].map((label, i) => ({
    ...DEFAULT_PROFILE.topics[0], id: `toast-topic-${i}`, label, include: [label], sourcePhrase: label,
  })),
};
edition.sections = edition.sections.slice(0, 3).map((section, i) => ({
  ...section,
  id: `toast-story-${i}`,
  topic_ids: [profile.topics[i].id],
  heading: ['Fictional payment network updates transfer timing', 'Public health office revises clinic guidance', 'Workplace board publishes a new reporting schedule'][i],
  source_ids: sources.slice(0, 1).map(source => source.id),
}));
edition.coverage = profile.topics.map(topic => ({ topic_id: topic.id, state: 'covered' }));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL && !['POST', 'PUT', 'DELETE'].includes(route.request().method()) ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
    await page.route('**/api/status', route => route.fulfill({ json: {} }));
    await page.route('**/api/episodes', route => route.fulfill({ json: { jobs: [] } }));
    await seedInterestProfile(page, profile);
    await page.goto(`${baseURL}/#today`);
    await page.locator('.today-story-row').first().waitFor();
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await page.locator('.story-bookmark').first().click();
      const toast = page.locator('.app-toast');
      await toast.waitFor();
      assert.match(await toast.innerText(), /Saved for later\./);
      const geometry = await toast.evaluate(el => {
        const rect = el.getBoundingClientRect();
        const nav = document.querySelector('.main-nav').getBoundingClientRect();
        const color = getComputedStyle(el).color;
        const background = getComputedStyle(el).backgroundColor;
        return { top: rect.top, bottom: rect.bottom, navTop: nav.top, left: rect.left, right: rect.right,
          viewportWidth: innerWidth, color, background, dismissTarget: el.querySelector('.app-toast-dismiss').getBoundingClientRect().width };
      });
      assert.ok(geometry.bottom <= geometry.navTop, `Toast stays above the tab bar: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.left >= 0 && geometry.right <= geometry.viewportWidth, 'Toast fits the phone viewport');
      assert.ok(geometry.dismissTarget >= 44, 'Dismiss control has a 44px target');
      if (process.env.BRIEFING_SHOT_DIR) {
        fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
        await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, `toast-${theme}.png`) });
      }
      await page.locator('.app-toast-dismiss').click();
      await toast.waitFor({ state: 'detached' });
      await page.locator('.story-bookmark').first().click();
      await toast.waitFor();
    }
    await page.waitForTimeout(4200);
    assert.equal(await page.locator('.app-toast').count(), 0, 'Toast dismisses after four seconds');
    console.log('Toast is consistent, dismissible, above the tabs, and expires after four seconds.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
