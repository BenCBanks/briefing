require('../test_guard.cjs').enforceTestEnvironment();

// Focused fictional-content checks for the compact Today and dedicated reader flow.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8990';
const shotDir = process.env.BRIEFING_SHOT_DIR;

async function capture(page, name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, name) });
}

// Playwright may scroll the opener before clicking, and browser Back restores
// on an animation frame. Compare with the actual departure position, not a
// sample taken while either of those operations is still pending.
async function clickWithScroll(page, opener) {
  await opener.evaluate(element => {
    window.__departureScroll = null;
    element.addEventListener('click', () => { window.__departureScroll = scrollY; }, { once: true, capture: true });
  });
  await opener.click();
  const position = await page.evaluate(() => window.__departureScroll);
  assert.equal(typeof position, 'number', 'The opener records its actual departure scroll');
  return position;
}

async function installTodayFixtureRoutes(page) {
  await page.route('**/api/profile', route => route.fulfill({
    status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'fixture_profile_absent' }),
  }));
  await page.route('**/api/status', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
  }));
  await page.route('**/api/edition**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ generated: false }),
  }));
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // Keep Today on the fictional sample edition regardless of preview profile/status state.
    await installTodayFixtureRoutes(page);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-page .edition-section').count(), 4, 'Today includes every readable story');
    assert.equal(await page.locator('.today-mode-read').count(), 0, 'Reading needs no separate mode');
    assert.match(await page.locator('.today-date').innerText(), /^Sunday 15 November$/);
    assert.equal(await page.locator('.today-listen-pill').count(), 1, 'Today exposes the primary Play action');
    assert.equal(await page.locator('.today-listen-pill .mode-label').innerText(), 'Play briefing');
    assert.match(await page.locator('.today-listen-pill').getAttribute('aria-label'), /^Play briefing$/);
    assert.match(await page.locator('.today-run-meta').innerText(), /^\d+ stories(?: · about \d+ min)?$/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await capture(page, 'today-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'today-390-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await page.locator('.today-full-story-link').first().click();
    await page.locator('body.is-reader .edition-section').first().waitFor();
    assert.equal(await page.locator('.main-nav').isVisible(), true, 'V3 icon and label tabs remain available in readers');
    assert.equal(await page.locator('.reader-sticky').isVisible(), true);
    assert.match(await page.locator('.reader-sticky .story-progress').getAttribute('aria-label'), /Story 1 of 4/);
    assert.equal(await page.locator('.story-dots').getAttribute('aria-hidden'), 'true');
    await capture(page, 'reader-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'reader-390-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });

    const sourceLink = page.locator('.story-sources [data-source]').first();
    const sourceId = await sourceLink.getAttribute('data-source');
    await sourceLink.scrollIntoViewIfNeeded();
    const readerSourceScroll = await clickWithScroll(page, sourceLink);
    await page.locator('.source-actions').waitFor();
    assert.equal(await page.locator('.main-nav').isVisible(), true);
    await page.goBack();
    await page.locator('body.is-reader .edition-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Today remains the selected root');
    await page.waitForFunction(id => document.activeElement?.dataset.source === id, sourceId);
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, readerSourceScroll);
    await page.goForward();
    await page.locator('.source-actions').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Browser Forward preserves the reader origin');
    await page.goBack();
    await page.locator('body.is-reader .edition-section').first().waitFor();

    const explanationAction = page.locator('.section-explanation').first();
    const explanationTopicId = await explanationAction.getAttribute('data-topic');
    const storyScroll = await clickWithScroll(page, explanationAction);
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Explanation retains its Today origin');
    const explanationSourceScroll = await page.evaluate(() => scrollY);
    await page.locator('.source-internal-link').first().click();
    await page.locator('.source-actions').waitFor();
    await page.goBack();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1);
    await page.waitForFunction(id => document.activeElement?.dataset.source === id, await page.locator('.source-internal-link').first().getAttribute('data-source'));
    assert.equal(await page.locator('.deep-dive-sources').evaluate(element => { const rect = element.getBoundingClientRect(); return rect.top < innerHeight && rect.bottom > 0; }), true, 'Source return restores the explanation reading area');
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1);
    await page.waitForFunction(id => document.activeElement?.dataset.topic === id, explanationTopicId);
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, storyScroll);
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    const scrollPage = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await installTodayFixtureRoutes(scrollPage);
    await scrollPage.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await scrollPage.locator('.today-page').waitFor();
    await scrollPage.evaluate(() => window.scrollTo({ top: 600, behavior: 'instant' }));
    const todayScroll = await scrollPage.evaluate(() => scrollY);
    await scrollPage.locator('[data-nav="topics"]').click();
    await scrollPage.locator('.topics-heading').waitFor();
    await scrollPage.locator('[data-nav="today"]').click();
    await scrollPage.locator('.today-page').waitFor();
    await scrollPage.waitForFunction(expected => Math.abs(scrollY - expected) < 5, todayScroll);
    await scrollPage.close();

    await page.locator('[data-nav="topics"]').click();
    await page.locator('.topics-heading').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1);
    await capture(page, 'topics-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'topics-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('[data-nav="today"]').click();
    await page.locator('[data-nav=topics]').click();
    await page.locator('.settings-link').click();
    await page.locator('.settings-list').waitFor();
    assert.equal(await page.locator('[data-page-title]').innerText(), 'Settings', 'Settings keeps its shared screen header');
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Settings remains under Topics in navigation');
    await page.getByRole('button', { name: 'Save time zone' }).click();
    await page.locator('.topics-heading').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Topics is the Settings entry point');
    assert.equal(await page.locator('.app-toast[role="status"] .app-toast-message').textContent(), 'Time zone saved.');
    await page.locator('[data-nav="topics"]').click();
    await page.locator('.topics-heading').waitFor();
    await page.locator('.topic-dive-link').first().click();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Direct explanation navigation retains Topics');
    await capture(page, 'explanation-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'explanation-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.deep-dive-save').click();
    const diveSource = page.locator('.source-internal-link').first();
    const diveSourceId = await diveSource.getAttribute('data-source');
    const topicSourceScroll = await clickWithScroll(page, diveSource);
    await page.locator('.source-actions').waitFor();
    assert.match(await page.locator('.reader-back').textContent(), /Explanation/);
    await capture(page, 'source-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'source-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.goBack();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1);
    await page.waitForFunction(id => document.activeElement?.dataset.source === id, diveSourceId);
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, topicSourceScroll);
    await page.locator('.reader-back').click();
    await page.locator('.topics-heading').waitFor();

    await page.locator('[data-nav="library"]').click();
    await page.locator('.library-filters').waitFor();
    assert.equal(await page.locator('[data-page-title]').innerText(), 'Earlier', 'Earlier keeps its shared screen header');
    assert.equal(await page.locator('[aria-current="page"][data-nav="library"]').count(), 1);
    await page.locator('.library-filters button').filter({ hasText: 'Editions' }).click();
    await capture(page, 'library-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'library-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.earlier-weekly-card').first().click();
    await page.locator('.weekly-recap-page').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="library"]').count(), 1, 'Edition from Library retains Library');
    await page.locator('[data-save-edition]').scrollIntoViewIfNeeded();
    await page.locator('[data-save-edition]').click();
    await page.locator('.reader-back').click();
    await page.locator('.library-filters').waitFor();
    await page.locator('.library-filters button[aria-pressed="false"]').filter({ hasText: 'Saved' }).click();
    assert.ok(await page.locator('.library-list li').count() >= 2, 'Saved filter lists the saved edition and explanation');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.library-filters button[aria-pressed="true"]').waitFor();
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').textContent(), 'Saved', 'Library filter survives a refresh');
    assert.deepEqual(errors, []);

    const direct = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await direct.goto(`${baseURL}/#explanation/dive-payments-basics`, { waitUntil: 'domcontentloaded' });
    await direct.locator('.deep-dive-section').first().waitFor();
    assert.equal(await direct.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Direct explanation URL defaults to Topics');
    await direct.reload({ waitUntil: 'domcontentloaded' });
    await direct.locator('.deep-dive-section').first().waitFor();
    assert.equal(await direct.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Refresh preserves direct explanation root');
    await direct.locator('.reader-back').click();
    await direct.locator('.topics-heading').waitFor();
    await direct.close();

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    await capture(page, 'today-desktop-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'today-desktop-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await page.locator('.today-full-story-link').first().click();
    await page.locator('body.is-reader .edition-section').first().waitFor();
    await capture(page, 'reader-desktop-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'reader-desktop-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    console.log('Navigation revision: compact Today, reader contents, explanation origin, source browser return and root selection passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
