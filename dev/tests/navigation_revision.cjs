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

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-page .edition-section').count(), 0, 'Today stays a short front page');
    assert.equal(await page.getByRole('button', { name: 'Read today’s briefing' }).count(), 1);
    assert.match(await page.locator('.today-weekly-link').textContent(), /The week in perspective.*Five systems, one useful question/s);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await capture(page, 'today-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'today-390-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.today-primary button.primary').click();
    await page.locator('.edition-section').first().waitFor();
    assert.equal(await page.locator('.main-nav').isVisible(), false, 'Root tabs are hidden in readers');
    assert.equal(await page.locator('.reader-sticky').isVisible(), true);
    await capture(page, 'reader-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'reader-390-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.edition-finish a[href="#weekly"]').click();
    await page.waitForFunction(() => location.hash === '#weekly' && document.querySelector('.reader-back')?.textContent.includes('Briefing'));
    assert.match(await page.locator('.reader-back').textContent(), /Briefing/, 'Weekly Back names its immediate reader parent');
    await page.locator('.reader-back').click();
    await page.waitForFunction(() => location.hash.startsWith('#edition/daily/'));
    assert.equal(await page.locator('.edition-finish a[href="#today"]').count(), 1, 'Daily reader has a clear return to Today');

    await page.locator('.reader-contents-open').click();
    assert.equal(await page.locator('.reader-contents-dialog').isVisible(), true);
    await page.locator('.reader-contents-dialog li button').nth(2).click();
    assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'H2', 'Contents returns to the chosen story heading');
    const sectionId = await page.locator('.edition-section').nth(2).getAttribute('id');
    assert.equal(await page.evaluate(() => document.activeElement?.id), `heading-${sectionId}`, 'Contents focuses the chosen story heading');
    await page.waitForFunction(id => {
      const top = document.getElementById(id).getBoundingClientRect().top;
      return top >= 54 && top < 120;
    }, sectionId);
    assert.equal(await page.evaluate(id => {
      const section = document.getElementById(id);
      const header = document.querySelector('.reader-sticky');
      return section.getBoundingClientRect().top >= header.getBoundingClientRect().bottom - 2;
    }, sectionId), true, 'Contents keeps the full section below the sticky header');

    const sourceLink = page.locator('.edition-section [data-source]').first();
    await page.locator('.section-sources summary').first().click();
    const sourceId = await sourceLink.getAttribute('data-source');
    const readerSourceScroll = await page.evaluate(() => scrollY);
    await sourceLink.click();
    await page.locator('.source-actions').waitFor();
    assert.equal(await page.locator('.main-nav').isVisible(), false);
    await page.goBack();
    await page.locator('.edition-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Today remains the selected root');
    await page.waitForFunction(id => document.activeElement?.dataset.source === id, sourceId);
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, readerSourceScroll);
    await page.goForward();
    await page.locator('.source-actions').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Browser Forward preserves the reader origin');
    await page.goBack();
    await page.locator('.edition-section').first().waitFor();

    const explanationAction = page.locator('.section-explanation').first();
    const explanationTopicId = await explanationAction.getAttribute('data-topic');
    const storyScroll = await page.evaluate(() => scrollY);
    await explanationAction.click();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1, 'Explanation retains its Today origin');
    await page.locator('.deep-dive-sources summary').click();
    const explanationSourceScroll = await page.evaluate(() => scrollY);
    await page.locator('.source-internal-link').first().click();
    await page.locator('.source-actions').waitFor();
    await page.goBack();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1);
    await page.waitForFunction(id => document.activeElement?.dataset.source === id, await page.locator('.source-internal-link').first().getAttribute('data-source'));
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, explanationSourceScroll);
    await page.locator('.reader-back').click();
    await page.locator('.edition-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="today"]').count(), 1);
    await page.waitForFunction(id => document.activeElement?.dataset.topic === id, explanationTopicId);
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, storyScroll);
    await page.goBack();
    await page.locator('.today-page').waitFor();
    await page.evaluate(() => window.scrollTo(0, 600));
    const todayScroll = await page.evaluate(() => scrollY);
    await page.locator('[data-nav="topics"]').click();
    await page.locator('.topics-heading').waitFor();
    await page.locator('[data-nav="today"]').click();
    await page.locator('.today-page').waitFor();
    await page.waitForFunction(expected => Math.abs(scrollY - expected) < 5, todayScroll);

    await page.locator('[data-nav="topics"]').click();
    await page.locator('.topics-heading').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1);
    await capture(page, 'topics-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'topics-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.topics-edit').click();
    await page.locator('.settings-list').waitFor();
    await page.getByRole('button', { name: 'Save topics' }).click();
    await page.locator('.topics-heading').waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Settings returns to its originating root');
    assert.equal(await page.locator('.settings-confirmation[role="status"]').textContent(), 'Topic choices saved.');
    await page.locator('.topic-dive-link').first().click();
    await page.locator('.deep-dive-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Direct explanation navigation retains Topics');
    await capture(page, 'explanation-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'explanation-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.deep-dive-save').click();
    await page.locator('.deep-dive-sources summary').click();
    const diveSource = page.locator('.source-internal-link').first();
    const diveSourceId = await diveSource.getAttribute('data-source');
    const topicSourceScroll = await page.evaluate(() => scrollY);
    await diveSource.click();
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
    assert.equal(await page.locator('[aria-current="page"][data-nav="library"]').count(), 1);
    await capture(page, 'library-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'library-390-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.library-list a[href^="#edition/"]').first().click();
    await page.locator('.edition-section').first().waitFor();
    assert.equal(await page.locator('[aria-current="page"][data-nav="library"]').count(), 1, 'Edition from Library retains Library');
    await page.locator('[data-save-edition]').scrollIntoViewIfNeeded();
    await page.locator('[data-save-edition]').click();
    await page.locator('.reader-back').click();
    await page.locator('.library-filters').waitFor();
    await page.locator('.library-filters button[aria-pressed="false"]').filter({ hasText: 'Saved' }).click();
    assert.ok(await page.locator('.library-list li').count() >= 2, 'Saved filter lists the saved edition and explanation');
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').textContent(), 'Saved', 'Library filter survives a refresh');
    assert.deepEqual(errors, []);

    const direct = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await direct.goto(`${baseURL}/#explanation/dive-payments-basics`, { waitUntil: 'networkidle' });
    await direct.locator('.deep-dive-section').first().waitFor();
    assert.equal(await direct.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Direct explanation URL defaults to Topics');
    await direct.reload({ waitUntil: 'networkidle' });
    assert.equal(await direct.locator('[aria-current="page"][data-nav="topics"]').count(), 1, 'Refresh preserves direct explanation root');
    await direct.locator('.reader-back').click();
    await direct.locator('.topics-heading').waitFor();
    await direct.close();

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await capture(page, 'today-desktop-light.png');
    await page.locator('.today-primary button.primary').click();
    await page.locator('.edition-section').first().waitFor();
    await capture(page, 'reader-desktop-light.png');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    console.log('Navigation revision: compact Today, reader contents, explanation origin, source browser return and root selection passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
