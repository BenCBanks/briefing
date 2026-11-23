require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const shotDir = process.env.BRIEFING_SHOT_DIR;

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    await page.route('**/src/app.js', route => route.fulfill({ contentType: 'application/javascript', body: '' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(async () => {
      const { renderToday } = await import('/src/edition/edition.js');
      const sections = [
        { id: 'fictional-transit', heading: 'Transit agency changes its weekend timetable', headline: 'Transit agency changes its weekend timetable', topic_ids: ['transit'], source_ids: ['fictional-source'], lede: 'A fictional route update begins next week.', body: 'Riders can check the revised times before traveling.' },
        { id: 'fictional-calendar', heading: 'Council publishes a new service calendar', headline: 'Council publishes a new service calendar', topic_ids: ['civic'], source_ids: ['fictional-source-two'], lede: 'A fictional calendar adds several service dates.', body: 'The published schedule lists each date and location.' },
      ];
      const sources = sections.map((section, index) => ({ id: section.source_ids[0], publisher: `Fictional Publisher ${index + 1}`, title: 'Fictional service notice', published_at: '2026-11-17', url: `https://publisher-${index}.example.test/notice` }));
      const edition = { id: 'fictional-player-day', revision_id: 'fictional-r1', kind: 'daily', local_date: '2026-11-18', audio: { duration_seconds: 240 }, sections, coverage: [{ topic_id: 'transit', state: 'covered' }, { topic_id: 'civic', state: 'covered' }] };
      window.__edition = edition; window.__sources = sources;
      window.__renderToday = (available = true) => renderToday(document.querySelector('main'), edition, [], { sources, listeningAvailable: available, listenEdition: item => { window.__played = item.id; }, listenStory: sectionId => { window.__chapterPlayed = sectionId; } });
      window.__renderToday(true);
    });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-listen-pill').count(), 1, 'Today has one primary Play control');
    assert.match(await page.locator('.today-listen-pill').innerText(), /^Play/u);
    assert.equal(await page.locator('.today-primary .today-listen-pill').count(), 1);
    assert.equal(await page.locator('.today-transcript[open]').count(), 0, 'transcripts start collapsed per story');
    assert.equal(await page.locator('.today-chapter-title').count(), 2, 'each story appears once as a chapter');
    await saveState(page, 'play');
    await page.locator('.today-listen-pill').click();
    assert.equal(await page.evaluate(() => window.__played), 'fictional-player-day', 'Play starts the edition player');

    await page.locator('.today-chapter-title').nth(1).click();
    assert.equal(await page.evaluate(() => window.__chapterPlayed), 'fictional-calendar', 'chapter tap starts playback from that story');
    assert.equal(await page.evaluate(() => document.activeElement.classList.contains('today-chapter-title')), true, 'chapter tap focuses its title');
    await saveState(page, 'chapter-jump');

    await page.locator('.today-transcript').first().locator('summary').click();
    assert.equal(await page.locator('.today-transcript').first().evaluate(node => node.open), true, 'transcript opens on demand');
    assert.match(await page.locator('.today-transcript').first().innerText(), /Fictional Publisher 1/u, 'sources are available with the transcript');
    await saveState(page, 'transcript');

    await page.evaluate(() => window.__renderToday(false));
    assert.equal(await page.locator('.today-listen-pill').count(), 0, 'no nonworking Play control appears without audio');
    assert.match(await page.locator('.today-audio-unavailable').innerText(), /Audio isn’t ready yet/u);
    assert.equal(await page.locator('.today-transcript[open]').count(), 2, 'text is shown directly while audio is unavailable');
    assert.match(await page.locator('.today-transcript').first().innerText(), /Riders can check the revised times/u);
    await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
    await saveState(page, 'no-audio');
    console.log('Today player-first: Play, chapter jump, transcript on demand, and no-audio text passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

async function saveState(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: theme });
    await page.screenshot({ path: path.join(shotDir, `today-${name}-${theme}.png`) });
  }
  await page.emulateMedia({ colorScheme: 'light' });
}
