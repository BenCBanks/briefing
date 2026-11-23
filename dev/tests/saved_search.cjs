require('../test_guard.cjs').enforceTestEnvironment();

// Earlier search, saved removal and topic matches use only fictional fixture data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const fixtureDir = path.join(__dirname, 'fixtures/legacy-state/generated');
const editions = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'editions.json'), 'utf8'));
const sources = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'sources.json'), 'utf8'));
const edition = editions.editions[0];
const savedStory = edition.sections[0].id;
async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.locator('h1').click();
  await page.mouse.move(0, 0);
  await page.evaluate(() => document.activeElement?.blur());
  await page.screenshot({ animations: 'disabled', caret: 'hide', path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}
async function expectSavedEmpty(page) {
  const empty = page.locator('.library-empty');
  assert.equal(await empty.locator('h2').innerText(), 'Nothing saved yet');
  assert.equal(await empty.locator('p').innerText(), 'Save a story or briefing to find it here.');
  assert.equal(await empty.locator('a[href],button').count(), 1, 'Saved empty state offers one action');
  assert.equal(await empty.getByRole('button', { name: 'Browse editions', exact: true }).count(), 1);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources: sources.sources } }));
    await seedInterestProfile(page);
    await page.addInitScript(({ savedId }) => {
      if (!localStorage.getItem('briefing:v1:state')) {
        localStorage.setItem('briefing:v1:state', JSON.stringify({
          savedIds: [savedId, savedId, null, 42, 'x'.repeat(101)],
          savedRevisions: { [savedId]: 'fixture-revision' },
        }));
      }
    }, { savedId: savedStory });

    await page.goto(`${baseURL}/#library`);
    const search = page.getByRole('searchbox', { name: 'Search earlier briefings, saved items, and topics' });
    await search.waitFor();
    assert.equal(await search.evaluate(input => getComputedStyle(input).fontSize), '16px', 'search input avoids mobile focus zoom');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'search fits the phone viewport');

    await capture(page, 'earlier-editions-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'earlier-editions-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.getByRole('button', { name: 'Saved', exact: true }).click();
    const savedRow = page.locator('.library-row').filter({ hasText: 'Confirm the payment before it moves' });
    await savedRow.waitFor();
    assert.equal(await page.locator('.library-list .library-row').count(), 1, 'duplicate and malformed saved IDs do not create duplicate rows');
    await capture(page, 'saved-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'saved-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'saved-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    const remove = savedRow.getByRole('button', { name: 'Remove Confirm the payment before it moves from saved items' });
    assert.ok(await remove.isVisible());
    assert.ok(await remove.evaluate(button => button.getBoundingClientRect().height >= 44), 'remove action has a 44px target');
    await remove.focus();
    await page.keyboard.press('Enter');
    await expectSavedEmpty(page);
    await capture(page, 'saved-empty-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'saved-empty-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await capture(page, 'saved-empty-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'saved-empty-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.deepEqual(await page.evaluate(() => {
      const state = JSON.parse(localStorage.getItem('briefing:v1:state'));
      return { savedIds: state.savedIds, savedRevisions: state.savedRevisions };
    }), { savedIds: [], savedRevisions: {} }, 'removing saves a normalized list and clears revision metadata');
    await page.reload({ waitUntil: 'networkidle' });
    await expectSavedEmpty(page);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedIds), [], 'an unsaved item stays removed after reload');

    await page.getByRole('button', { name: 'Editions', exact: true }).click();
    await search.fill('Confirm the payment');
    await page.getByText('Confirm the payment before it moves', { exact: true }).waitFor();
    assert.equal(await page.locator('.library-row').filter({ hasText: 'Confirm the payment before it moves' }).locator('.library-kind-label').innerText(), 'Story', 'Earlier stories are searchable by heading');
    assert.equal(await page.locator('.library-item-link .library-item-title').first().evaluate(title => getComputedStyle(title).textDecorationLine), 'underline', 'search result titles are visibly recognizable as links');
    await search.focus();
    for (let index = 0; index < 4; index++) await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('library-item-link')), true, 'keyboard users can tab from search through filters to a result');
    await page.evaluate(() => document.activeElement?.blur());
    await capture(page, 'earlier-search-results-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'earlier-search-results-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.getByRole('button', { name: 'Saved', exact: true }).click();
    await search.fill('banking');
    const topic = page.locator('.library-row').filter({ hasText: 'Banking' });
    await topic.waitFor();
    await capture(page, 'topic-search-light');
    assert.equal(await topic.locator('.library-kind-label').innerText(), 'Topic', 'topic labels are searchable from Saved');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'topic-search-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await search.focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement?.closest('.library-filters') !== null), true, 'segment controls follow search in the tab order');

    await search.fill('no fictional result matches this');
    await page.getByRole('heading', { name: 'No matching briefings' }).waitFor();
    assert.match(await page.locator('.library-search-status').innerText(), /No matches for/);
    await search.evaluate(input => input.blur());
    await capture(page, 'earlier-search-empty-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'earlier-search-empty-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('button', { name: 'Clear search', exact: true }).click();
    assert.equal(await search.inputValue(), '', 'Clear search returns to the selected Earlier segment');
    assert.equal(await search.evaluate(input => document.activeElement === input), true, 'Clear search returns focus to the search field');
    await search.fill('no fictional result matches this');
    await search.focus(); await page.keyboard.press('Escape');
    assert.equal(await search.inputValue(), '', 'Escape clears the active search');
    assert.equal(await page.getByRole('heading', { name: 'No matching briefings' }).count(), 0, 'Escape restores Earlier rows and exits the empty state');

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    const bookmark = page.locator(`[data-bookmark="${savedStory}"]`);
    await bookmark.waitFor();
    await bookmark.click();
    assert.equal(await bookmark.getAttribute('aria-pressed'), 'true', 'saving from Today updates the bookmark state');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedIds), [savedStory]);
    await page.locator('[data-nav="library"]').click();
    await page.locator('.library-filters').getByRole('button', { name: 'Saved', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search earlier briefings, saved items, and topics' }).fill('');
    const persistedStory = page.locator('.library-row').filter({ hasText: 'Confirm the payment before it moves' });
    await persistedStory.waitFor();
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.library-row').filter({ hasText: 'Confirm the payment before it moves' }).waitFor();
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedIds), [savedStory], 'a saved story remains saved after reload');
    console.log('Saved search: malformed state, unsave/save reload persistence, topic and earlier search, empty state, keyboard and phone sizing passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
