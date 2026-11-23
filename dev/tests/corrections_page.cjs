require('../test_guard.cjs').enforceTestEnvironment();

// Fictional corrections page: newest-first entries, retained revision diffs and safe return links.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8931';
const corrections = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/corrections.json'), 'utf8')).corrections;
const oldEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/correction/r1/editions.json'), 'utf8')).editions[0];
const newEdition = JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/correction/r2/editions.json'), 'utf8')).editions[0];
const episodeFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes.json'), 'utf8')).episodes[0];
const oldEpisode = structuredClone(episodeFixture);
oldEpisode.revision_id = 'episode-r1';
const newEpisode = structuredClone(episodeFixture);
newEpisode.revision_id = 'episode-r2';
newEpisode.chapters[0].sentences[0].text = 'The fictional pilot connects a single city to a shared instant payment network.';
newEpisode.corrections = [{ id: 'fictional-episode-correction-001', current_text: newEpisode.chapters[0].sentences[0].text }];

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name + '.png') });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York', serviceWorkers: 'block' });
    const page = await context.newPage();
    let rows = corrections;
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'profile_not_found' } }));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: 'edition_not_found' } }));
    await page.route('**/dev/tests/fixtures/episodes.json', route => route.fulfill({ json: { version: 1, episodes: [episodeFixture] } }));
    await page.route('**/api/corrections', route => route.fulfill({ json: { corrections: rows } }));
    await page.route('**/api/revisions/**', async route => {
      const url = new URL(route.request().url());
      const parts = url.pathname.split('/').slice(-3);
      const [type, target, revision] = parts;
      if (type === 'edition' && target === 'fictional-correction-edition') {
        const edition = revision === 'fictional-r1' ? oldEdition : newEdition;
        return route.fulfill({ json: { edition, historical: true, playback_allowed: false } });
      }
      if (type === 'episode' && target === 'episode-payment-rules') {
        const episode = revision === 'episode-r1' ? oldEpisode : newEpisode;
        return route.fulfill({ json: { episode, historical: true, playback_allowed: false } });
      }
      return route.fulfill({ status: 404, json: { error: 'revision_not_found' } });
    });

    await page.goto(`${baseURL}/#corrections`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Corrections', exact: true }).waitFor();
    assert.equal(await page.locator('.corrections-back').getAttribute('href'), '#today', 'The page has a clear route back into the briefing');
    await page.getByText('The participant count was corrected from 120 to 12.', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('.correction-date').allTextContents(), ['17 Nov 2026', '16 Nov 2026'], 'Corrections are newest first');
    const storyEntry = page.locator('.correction-card').filter({ hasText: 'A fictional checkout pilot' });
    assert.equal(await storyEntry.locator('.correction-updated').innerText(), 'Corrected', 'the dated correction entry uses the shared correction label');
    assert.equal(await storyEntry.locator('.correction-updated').evaluate(link => link.getBoundingClientRect().height >= 44), true, 'the corrected tag remains a comfortable tap target');
    assert.match(await storyEntry.locator('.correction-updated').getAttribute('aria-label'), /^Corrected: open /);
    assert.equal(await storyEntry.locator('.correction-updated').getAttribute('href'), '#edition/daily/fictional-correction-edition/correction-checkout-pilot');
    await storyEntry.locator('summary').click();
    assert.match(await storyEntry.locator('del').innerText(), /120 participants/);
    assert.match(await storyEntry.locator('ins').innerText(), /12 participants/);
    const episodeEntry = page.locator('.correction-card').filter({ hasText: 'How a fictional city checks instant payments' });
    await episodeEntry.locator('summary').click();
    assert.match(await episodeEntry.locator('del').innerText(), /plans to connect several local banks/);
    assert.match(await episodeEntry.locator('ins').innerText(), /single city/);
    assert.equal(await episodeEntry.getByText('See what changed', { exact: true }).count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await capture(page, 'corrections-phone-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'corrections-phone-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    const storyHistoryLink = await page.evaluate(async (edition) => {
      const { renderEdition } = await import('/src/edition/edition.js');
      const host = document.querySelector('#content');
      renderEdition(host, edition, [], {});
      return host.querySelector('.correction-history-link')?.getAttribute('href');
    }, newEdition);
    assert.equal(storyHistoryLink, '#corrections', 'Corrected story notice links to the dated log');

    rows = [];
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.goto(`${baseURL}/#corrections`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'No corrections yet' }).waitFor();
    assert.match(await page.locator('.corrections-empty-copy').innerText(), /will appear here/);
    await capture(page, 'corrections-empty-phone-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'corrections-empty-phone-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.goto(`${baseURL}/#preferences`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.settings-corrections-link').getAttribute('href'), '#corrections', 'Settings provides a route to the corrections history');

    await page.evaluate(async episode => {
      const { renderEpisodePage } = await import('/src/episodes/episodes.js');
      renderEpisodePage(document.querySelector('#content'), episode);
    }, episodeFixture);
    assert.equal(await page.locator('.episode-updated').getAttribute('href'), '#corrections', 'Updated episode badge opens the correction log');
    assert.equal(await page.locator('.episode-correction-history-link').getAttribute('href'), '#corrections', 'Episode correction notice links to the log');
    await page.locator('.episode-updated').click();
    await page.locator('.corrections-page').waitFor();
    console.log('Corrections page: dated story and episode diffs, updated links and empty state passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
