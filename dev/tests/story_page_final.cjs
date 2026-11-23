require('../test_guard.cjs').enforceTestEnvironment();

// Story-page checks use the bundled fictional edition and one added fictional source.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8934';
const secondSource = {
  id: 'story-page-source-two', publisher: 'Fictional Clearing House',
  title: 'A second fictional settlement note', published_at: '2026-11-14',
  url: 'https://clearing.example.test/settlement-note', kind: 'fixture', note: 'Fictional test source',
};

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`), animations: 'disabled' });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL
      ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'profile_not_found' } }));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: 'edition_not_found' } }));
    await page.route('**/api/episodes', route => route.fulfill({ json: { jobs: [] } }));
    await page.route('**/content/sources.json', async route => {
      const response = await route.fetch();
      const payload = await response.json();
      payload.sources.push(secondSource);
      await route.fulfill({ response, body: JSON.stringify(payload) });
    });
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const payload = await response.json();
      const edition = payload.editions.find(item => item.kind === 'daily');
      const section = edition.sections.find(item => item.kind !== 'link');
      section.source_ids = [section.source_ids[0], secondSource.id];
      section.read_takeaway = 'This is the one-sentence fictional takeaway. A second sentence is not shown.';
      await route.fulfill({ response, body: JSON.stringify(payload) });
    });
    await page.addInitScript(() => {
      window.__spoken = [];
      window.__shared = [];
      window.__copied = [];
      Object.defineProperty(navigator, 'share', { configurable: true, value: async data => { window.__shared.push(data); } });
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.__copied.push(text); } } });
      class Utterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, value: Utterance });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        speaking: false, paused: false, getVoices: () => [],
        speak(item) { this.speaking = true; window.__spoken.push(item.text); setTimeout(() => item.onstart?.(), 0); },
        pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.speaking = false; this.paused = false; },
      } });
    });

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    const storyOpener = page.locator('.today-full-story-link').first();
    const sectionId = await storyOpener.getAttribute('data-read-section');
    await storyOpener.click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.reader-back').innerText(), '‹ Today', 'Story page names Today as its parent');
    assert.equal(await page.locator('.reader-story-header .story-context-line').count(), 1, 'Topic and date share one quiet context line');
    assert.equal(await page.locator('.reader-story-header .story-source-status,.edition-summary-dek').count(), 0, 'Source authority stays with Citations, below the headline');
    assert.equal(await page.locator('.story-lead p').count(), 1, 'Story page keeps the shared lede block');
    assert.ok(await page.locator('.story-so-what').count(), 'Story page keeps the shared So what block');
    assert.equal(await page.locator('.story-takeaway').innerText(), 'Read: This is the one-sentence fictional takeaway.', 'Read uses only the first supplied sentence');
    assert.equal(await page.locator('.story-more').isVisible(), true, 'More is already open on the story page');
    assert.equal(await page.locator('.story-sources').getAttribute('aria-label'), 'Citations');
    assert.deepEqual(await page.locator('.story-sources .citation-number').allTextContents(), ['1.', '2.'], 'Multiple sources receive a numbered list');
    assert.ok(await page.locator('.story-sources [data-source]').first().evaluate(el => el.getBoundingClientRect().height >= 44), 'Citation links have a 44px touch target');
    assert.equal(await page.locator('.story-listen .story-listen-label').innerText(), 'Listen from here');
    assert.equal(await page.locator('.story-listen').isDisabled(), false);
    const actionsTrigger = page.locator('.story-actions-trigger');
    await actionsTrigger.click();
    const actionsDialog = page.locator('.story-actions-dialog');
    await actionsDialog.waitFor({ state: 'visible' });
    assert.equal(await actionsDialog.locator('h2').innerText(), 'Share this story');
    assert.ok(await actionsDialog.locator('.story-actions-status').evaluate(el => el.getBoundingClientRect().height <= 1), 'the empty feedback live region does not leave a blank block in the sheet');
    assert.equal(await actionsDialog.locator('.story-report-form h3').innerText(), 'Report a mistake');
    assert.equal(await actionsDialog.locator('.story-report-note').innerText(), 'What should we check? Nothing is sent automatically; copy the details into your message to the editor.');
    await capture(page, 'story-actions-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'story-actions-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await actionsDialog.getByRole('button', { name: 'Share story' }).click();
    const shared = await page.evaluate(() => window.__shared[0]);
    assert.equal(shared.title, await page.locator('.reader-story-header h1').innerText());
    assert.equal(shared.url, page.url());
    await actionsDialog.getByRole('button', { name: 'Copy link' }).click();
    assert.equal(await page.evaluate(() => window.__copied.at(-1)), page.url());
    await actionsDialog.locator('#story-report-note').fill('The publication date appears incorrect.');
    await actionsDialog.getByRole('button', { name: 'Copy report details' }).click();
    const report = await page.evaluate(() => window.__copied.at(-1));
    assert.match(report, /^Story: .+\nLink: http:\/\//);
    assert.match(report, /What to check:\nThe publication date appears incorrect\.$/);
    assert.ok(!report.includes('This is the one-sentence fictional takeaway.'), 'Report details omit article body text');
    await actionsDialog.getByRole('button', { name: 'Close story actions' }).click();
    await page.waitForFunction(() => document.activeElement?.classList.contains('story-actions-trigger'));
    await capture(page, 'story-page-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'story-page-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    await page.waitForFunction(id => document.activeElement?.dataset.readSection === id, sectionId);
    assert.equal(await page.locator('.today-story-row').count(), 4, 'Back restores Today’s list');

    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await page.locator('.today-full-story-link').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('.story-listen').click();
    await page.waitForFunction(() => window.__spoken.length > 0);
    await page.waitForFunction(id => {
      const state = JSON.parse(localStorage.getItem('briefing:v1:state'));
      return state?.readingProgress?.['daily-sample-001']?.storyId === id && state.readingProgress['daily-sample-001'].mode === 'listen';
    }, sectionId);
    assert.ok((await page.evaluate(() => window.__spoken.join(' '))).length > 0, 'Listen from here uses the shared speech player');
    assert.deepEqual(errors, [], 'Story navigation and playback produce no browser errors');
    console.log('Story page: shared blocks, one-line Read takeaway, source numbering, focus return, and Listen from here passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
