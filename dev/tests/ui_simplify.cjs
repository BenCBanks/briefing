require('../test_guard.cjs').enforceTestEnvironment();

// The daily path uses fictional saved-state fixtures; all non-loopback requests are blocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8940';
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/legacy-state/generated', name), 'utf8'));
const edition = fixture('editions.json').editions[0];
const sources = fixture('sources.json').sources;
edition.quick_hits = [{ title: 'Fictional transit office publishes a weekend timetable', outlet: 'Fictional Transit',
  url: 'https://transit.example.test/timetable', summary: 'Weekend connections will use the revised timetable. Riders can check the published schedule for each local route before travelling across the fictional city.' }];
async function capture(page, name) {
  if (process.env.BRIEFING_SHOT_DIR) {
    fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
  }
}
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
    await seedInterestProfile(page);
    await page.addInitScript(() => {
      window.__spoken = [];
      window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
      Object.defineProperty(window, 'speechSynthesis', { value: {
        speaking: false, paused: false, getVoices: () => [],
        speak(utterance) { this.speaking = true; window.__spoken.push(utterance.text); queueMicrotask(() => utterance.onstart?.()); },
        pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.speaking = false; },
      } });
    });
    await page.goto(`${baseURL}/#today`);
    await page.locator('.today-page').waitFor();
    assert.deepEqual(await page.locator('.main-nav-label').allTextContents(), ['Today', 'On demand', 'Earlier', 'Topics']);
    assert.equal(await page.locator('.inline-story .edition-section').count(), edition.sections.length);
    assert.equal(await page.locator('.today-mode-read,.story-row-play').count(), 0);
    assert.equal(await page.locator('.today-listen-pill').count(), 1);
    assert.equal(await page.locator('.mini-player').count(), 0);
    assert.equal(await page.evaluate(() => window.__spoken.length), 0, 'No speech starts on load');
    assert.equal(await page.locator('.today-quick-hits li').count(), 0);
    await capture(page, 'today-fixture-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'today-fixture-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    const summary = page.locator('.story-more-toggle').first();
    if (await summary.getAttribute('aria-expanded') === 'false') await summary.click();
    const source = page.locator('.story-more [data-source]').first();
    await source.click();
    await page.locator('.source-title').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-source'));
    assert.equal(await summary.getAttribute('aria-expanded'), 'true');
    await page.locator('.today-completion').scrollIntoViewIfNeeded();
    await capture(page, 'today-fixture-complete');
    await page.locator('.today-listen-pill').scrollIntoViewIfNeeded();
    await page.evaluate(() => { window.__article = document.querySelector('.today-page'); window.__scroll = scrollY; });
    await page.locator('.today-listen-pill').click();
    await page.waitForFunction(() => window.__spoken.length > 0);
    assert.equal(await page.evaluate(() => window.__article === document.querySelector('.today-page')), true, 'Playback keeps the reading DOM');
    assert.ok(await page.evaluate(() => Math.abs(scrollY - window.__scroll) < 2), 'Playback preserves scroll');
    assert.ok(await page.locator('.mini-player-open strong').evaluate(el => getComputedStyle(el).whiteSpace === 'normal' && getComputedStyle(el).textOverflow !== 'ellipsis'), 'The mini-player title wraps without truncation');
    assert.ok(await page.locator('.mini-player').evaluate(el => el.getBoundingClientRect().height >= 68), 'The mini-player grows to fit its wrapped title');
    await capture(page, 'player-mini-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'player-mini-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.mini-player-open').click();
    assert.ok(await page.locator('.player-dialog').evaluate(el => el.getBoundingClientRect().bottom >= innerHeight - 1), 'The full player sheet covers the pinned bottom bars');
    assert.ok(await page.locator('[data-player-close]').evaluate(el => el.getBoundingClientRect().height >= 44), 'The player minimize target is at least 44px');
    assert.ok(await page.locator('.player-controls button').evaluateAll(els => els.every(el => el.getBoundingClientRect().height >= 44)), 'Player transport targets are at least 44px');
    await capture(page, 'player-sheet-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'player-sheet-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.keyboard.press('Escape');
    await page.locator('[data-nav=library]').click();
    await page.locator('.library-briefing-list').waitFor();
    assert.ok(await page.locator('.library-filters').evaluate(el => Boolean(el.compareDocumentPosition(document.querySelector('.library-briefings')) & Node.DOCUMENT_POSITION_FOLLOWING)));
    assert.doesNotMatch(await page.locator('main').innerText(), /No weekly recaps yet/);
    await page.getByRole('button', { name: 'Saved', exact: true }).click();
    assert.equal(await page.locator('.library-briefings').isVisible(), false, 'Saved filters out edition dates');
    await page.getByRole('button', { name: 'Editions', exact: true }).click();
    await capture(page, 'earlier-fixture');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'earlier-fixture-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('[data-nav=topics]').click();
    await page.locator('.settings-link').waitFor();
    await capture(page, 'topics-fixture');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'topics-fixture-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.ok(await page.locator('main').evaluate(el => parseFloat(getComputedStyle(el).paddingBottom) >= 120));
    await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
    assert.ok(await page.locator('.topic-row').last().evaluate(el => el.getBoundingClientRect().bottom < document.querySelector('.mini-player').getBoundingClientRect().top), 'The last topic clears both bars at the end of the page');
    await capture(page, 'topics-bottom-clearance');
    await page.goBack(); await page.locator('.library-filters').waitFor();
    await page.goBack(); await page.locator('.today-page').waitFor();
    await page.goForward(); await page.locator('.library-filters').waitFor();
    await page.goForward(); await page.locator('.topics-page').waitFor();
    await page.locator('[data-nav=today]').click(); await page.locator('.today-page').waitFor();
    const story = page.locator('.story-row-link').first();
    await story.click(); await page.locator('.reader-story-header').waitFor();
    await capture(page, 'story-fixture-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'story-fixture-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
    assert.ok(await page.locator('.edition').evaluate(el => el.getBoundingClientRect().bottom < Math.min(document.querySelector('.mini-player').getBoundingClientRect().top, document.querySelector('.main-nav').getBoundingClientRect().top)), 'The story ending clears both pinned player and tab bars');
    await page.evaluate(() => scrollTo(0, 0));
    await page.goBack(); await page.locator('.today-page').waitFor();
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-read-section'));
    await page.locator('.today-completion').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('.today-completion').evaluate(el => el.getBoundingClientRect().bottom < document.querySelector('.mini-player').getBoundingClientRect().top), 'Ending clears the Listen bar');
    await page.locator('[data-nav=topics]').click(); await page.locator('.topics-page').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    // Exercise missing explanations with the real phone CSS and a fictional brief.
    await page.evaluate(async () => {
      const { renderTopics } = await import('/src/topics/topics.js');
      renderTopics(document.querySelector('main'), [{ id: 'fictional-transit', label: 'Local transport' }], [], ['fictional-transit'], [], {
        interestBrief: { geography: 'Fictional City', subjects: ['Transport'], exclusions: [], depth: 'standard' },
        editTopics() {}, rebuildFromWords() {}, saveInterestBrief() {},
      });
    });
    assert.equal(await page.locator('.topic-description').count(), 0, 'Topic name is not repeated');
    const save = page.locator('.topics-update');
    await save.scrollIntoViewIfNeeded();
    assert.ok(await save.evaluate(button => {
      const box = button.getBoundingClientRect();
      const nav = document.querySelector('.main-nav').getBoundingClientRect();
      const player = document.querySelector('.mini-player')?.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= Math.min(nav.top, player?.top ?? innerHeight);
    }), 'Update is fully visible above the pinned controls');
    await capture(page, 'topics-brief-fixture');
    assert.deepEqual(errors, []);
    console.log('Inline reading, labelled navigation, source return, explicit audio and stable playback passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
