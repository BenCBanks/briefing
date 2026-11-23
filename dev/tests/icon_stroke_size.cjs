require('../test_guard.cjs').enforceTestEnvironment();

// Fixture-only audit: navigation and row glyphs share one 24px box; outline marks share a stroke.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8936';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function screenshot(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, `${name}.png`), animations: 'disabled' });
}

async function auditIcons(page, routeName) {
  const tabs = await page.locator('.main-nav-icon').evaluateAll(icons => icons.map(icon => {
    const style = getComputedStyle(icon);
    const rect = icon.getBoundingClientRect();
    return { width: rect.width, height: rect.height, stroke: style.strokeWidth };
  }));
  assert.equal(tabs.length, 4, `${routeName}: all four tab icons render`);
  for (const icon of tabs) {
    assert.deepEqual(icon, { width: 24, height: 24, stroke: '1.8px' }, `${routeName}: tabs share 24px size and 1.8px stroke`);
  }

  const rows = await page.locator('.story-bookmark svg,.story-more-toggle svg,.today-listen-pill .mode-icon svg,.story-listen-icon svg,.library-listen-button svg,.topic-listen-button svg,.story-row-play-circle svg').evaluateAll(icons => icons.map(icon => {
    const style = getComputedStyle(icon);
    const rect = icon.getBoundingClientRect();
    return { cls: icon.parentElement?.className || '', width: rect.width, height: rect.height,
      stroke: style.strokeWidth, fill: style.fill, strokeValue: style.stroke };
  }));
  for (const icon of rows) {
    assert.deepEqual([icon.width, icon.height], [24, 24], `${routeName}: row icon ${icon.cls} uses the shared 24px box`);
    if (icon.cls.includes('bookmark') || icon.cls.includes('more-toggle')) {
      assert.equal(icon.stroke, '1.8px', `${routeName}: outline row icon ${icon.cls} uses the shared stroke`);
    } else {
      assert.equal(icon.strokeValue, 'none', `${routeName}: filled playback glyph ${icon.cls} keeps its solid form`);
    }
  }
  return rows.length;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: {} }));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: {} }));
    await page.route('**/api/episodes', route => route.fulfill({ json: { jobs: [] } }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();

    const pages = [
      ['today', '.today-page'], ['on-demand', '.episode-request-page'],
      ['earlier', '.library-results'], ['topics', '.topics-page'],
    ];
    for (const [key, selector] of pages) {
      if (key !== 'today') {
        const navKey = key === 'on-demand' ? 'on-demand' : key === 'earlier' ? 'library' : key;
        await page.locator(`[data-nav="${navKey}"]`).click();
        await page.locator(selector).waitFor();
        if (key === 'earlier') assert.equal(await page.locator('h1').first().innerText(), 'Earlier');
      }
      const rows = await auditIcons(page, key);
      await screenshot(page, `${key}-icons-light-390`);
      await page.emulateMedia({ colorScheme: 'dark' });
      await screenshot(page, `${key}-icons-dark-390`);
      await page.emulateMedia({ colorScheme: 'light' });
      console.log(`${key}: four consistent tab icons; ${rows} row icons audited`);
    }

    await page.locator('[data-nav="today"]').click();
    await page.locator('.today-page').waitFor();
    await page.locator('.today-story-row .story-row-link').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    const storyListenIcon = page.locator('.story-listen-icon svg');
    await storyListenIcon.waitFor();
    const listenGlyph = await storyListenIcon.evaluate(icon => {
      const style = getComputedStyle(icon); const rect = icon.getBoundingClientRect();
      return { width: rect.width, height: rect.height, stroke: style.stroke };
    });
    assert.deepEqual(listenGlyph, { width: 24, height: 24, stroke: 'none' }, 'story Listen from here uses the shared 24px filled glyph');
    await screenshot(page, 'story-listen-icons-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'story-listen-icons-dark-390');
    console.log('story reader: Listen from here uses one filled 24px glyph');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
