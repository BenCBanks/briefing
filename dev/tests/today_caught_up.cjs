require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8945';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function capture(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, `${name}.png`), animations: 'disabled' });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    await page.route('**/src/app.js', route => route.fulfill({ contentType: 'application/javascript', body: '' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(async () => {
      const { renderToday } = await import('/src/edition/edition.js');
      const { syncTodayCompletion } = await import('/src/player/integration.js');
      const edition = { id: 'fictional-day', revision_id: 'fixture-r1', kind: 'daily', local_date: '2026-11-18',
        audio: { duration_seconds: 120 }, sections: [{ id: 'fictional-story', heading: 'Fictional final story',
          body: 'This invented story is here only for a player fixture.', topic_ids: ['fictional-topic'] }],
        coverage: [{ topic_id: 'fictional-topic', state: 'covered' }] };
      renderToday(document.querySelector('main'), edition, [], { listeningAvailable: true, listeningComplete: false });
      window.__todayCompletion = { edition, syncTodayCompletion };
    });
    const ending = page.locator('[data-today-completion]');
    assert.equal(await ending.isVisible(), false, 'the caught-up ending stays hidden before playback finishes');
    await page.evaluate(() => window.__todayCompletion.syncTodayCompletion(document.querySelector('main'), {
      item: { ...window.__todayCompletion.edition, kind: 'episode' }, status: 'complete',
    }));
    assert.equal(await ending.isVisible(), false, 'finishing an on-demand episode does not complete Today');
    await page.evaluate(() => window.__todayCompletion.syncTodayCompletion(document.querySelector('main'), {
      item: { ...window.__todayCompletion.edition, revision_id: 'older-r0' }, status: 'finished',
    }));
    assert.equal(await ending.isVisible(), false, 'an older edition cannot show today’s completion');
    await page.evaluate(() => window.__todayCompletion.syncTodayCompletion(document.querySelector('main'), {
      item: window.__todayCompletion.edition, status: 'finished',
    }));
    assert.equal(await ending.isVisible(), true, 'finishing the current daily audio reveals the caught-up ending');
    assert.equal(await ending.locator('strong').innerText(), "You're caught up for today.");
    assert.equal(await ending.locator('span').innerText(), 'Next briefing tomorrow morning');
    await ending.scrollIntoViewIfNeeded();
    await capture(page, 'today-caught-up-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'today-caught-up-dark-390');
    console.log('Today caught-up ending: hidden until the current daily playback finishes.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
