require('../test_guard.cjs').enforceTestEnvironment();

// Fictional-only integration coverage for local route engagement events.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.evaluate(() => localStorage.removeItem('briefing:v1:signals'));
    await page.setViewportSize({ width: 390, height: 300 });
    await page.locator('.today-contents [data-read-section]').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    assert.ok(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals') || '[]').some(event => event.type === 'skip')), 'leaving before the second paragraph records skip');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.today-contents [data-read-section]').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('[data-save-edition]').click();
    await page.locator('[data-source]').first().click();
    await page.locator('.source-title').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('.section-explanation').click();
    await page.locator('.deep-dive-page').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    const events = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals') || '[]'));
    for (const type of ['save', 'sourceOpen', 'explainOpen', 'skip']) assert.ok(events.some(event => event.type === type), `${type} event was not recorded`);
    assert.ok(events.every(event => Number.isFinite(event.ts) && typeof event.storyId === 'string' && typeof event.topicId === 'string'), 'route signals use the documented local event shape');
    assert.ok(events.some(event => event.type === 'sourceOpen' && event.topicId === 'payments'), 'source opening keeps its story topic');
    assert.ok(events.some(event => event.type === 'explainOpen' && event.storyId === 'daily-payments-001'), 'explanation opening points back to its story');

    await page.locator('.today-contents [data-read-section]').first().click();
    for (let index = 0; index < 4; index += 1) {
      await page.locator('body.is-reader .edition-section').waitFor();
      if (index < 3) await page.locator('.next-story').click();
    }
    await page.locator('[data-finish]').click();
    const finished = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals') || '[]'));
    assert.ok(finished.some(event => event.type === 'finish' && event.storyId === 'daily-insurance-001' && event.topicId === 'insurance'), 'finish event is recorded for the final story');
    await page.close();
    console.log('signals routes: finish, skip, save, sourceOpen and explainOpen were stored locally with topic/story identifiers.');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
