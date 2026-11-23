require('../test_guard.cjs').enforceTestEnvironment();

// Generated fictional scripts for every requested length; no backend/provider work.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes-lengths.json'), 'utf8'));
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === baseURL && !url.pathname.startsWith('/api/') ? route.continue() : route.abort();
    });
    // A plain JSON document has no application startup or background jobs.
    await page.goto(`${baseURL}/dev/tests/fixtures/episodes-lengths.json`);
    for (const episode of fixture.episodes) {
      await page.evaluate(async value => {
        const { renderEpisodePage } = await import('/src/episodes/episodes.js');
        renderEpisodePage(document.body, value);
      }, episode);
      assert.equal(await page.getByRole('heading', { name: "What we couldn't confirm", exact: true }).count(), 1);
      assert.equal(await page.locator('.episode-uncertain').count(), 0, 'Narrated evidence notes are not repeated below the transcript');
      assert.match(await page.locator('.episode-transcript').innerText(), /shorter than requested/);
      assert.doesNotMatch(await page.locator('body').innerText(), /\[object Object\]/);
      assert.equal(episode.target_words, episode.requested_minutes * 140);
    }
    console.log('All four length fixtures render cited, honestly shorter transcripts without duplicate evidence notes.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
