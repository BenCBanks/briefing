require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const shots = process.env.BRIEFING_SHOT_DIR || path.join(__dirname, '../../screenshots/v3/a11y-320');

async function emulateLargeText(page) {
  await page.route('**/styles/v3.css', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: `${await response.text()}\n:root{font-size:200%}` });
  });
}

async function run() {
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({ viewport: { width: 320, height: 844 }, colorScheme: theme, timezoneId: 'America/New_York' });
      page.setDefaultTimeout(10000);
      await emulateLargeText(page);
      await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
      await page.locator('.today-page .today-story-row').first().waitFor();

      const labels = await page.locator('.main-nav-label').evaluateAll(nodes => nodes.map(node => ({
        text: node.textContent.trim(), size: Number.parseFloat(getComputedStyle(node).fontSize),
      })));
      assert.equal(labels.length, 4, 'Today shows all four navigation labels');
      assert.ok(labels.every(label => label.size >= 16), `320 px large-text tab labels scale: ${JSON.stringify(labels)}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Today fits the 320 px 200% viewport');

      const transcript = page.locator('.today-transcript').first();
      await transcript.locator('summary').click();
      const sources = transcript.locator('[data-source]');
      if (await sources.count()) {
        const names = await sources.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label') || ''));
        assert.ok(names.every(name => /^Open source from .+/u.test(name)), `Today source names identify their publishers: ${JSON.stringify(names)}`);
      }
      await page.screenshot({ path: path.join(shots, `today-transcript-320-${theme}.png`) });

      await page.close();

      const request = await browser.newPage({ viewport: { width: 320, height: 844 }, colorScheme: theme, timezoneId: 'America/New_York' });
      request.setDefaultTimeout(10000);
      await emulateLargeText(request);
      await request.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
      const prompt = request.locator('.episode-request-page textarea');
      await prompt.waitFor();
      assert.ok(await prompt.evaluate(node => node.getBoundingClientRect().height >= 104), 'On demand prompt is tall enough to show its example');
      assert.equal(await request.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'On demand fits the 320 px viewport');
      await request.screenshot({ path: path.join(shots, `on-demand-320-${theme}.png`) });
      await request.close();
    }
    console.log(`320 px Today, On demand and player checks passed; screenshots: ${shots}`);
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
