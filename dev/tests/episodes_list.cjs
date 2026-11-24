require('../test_guard.cjs').enforceTestEnvironment();

// Episode history controls use fictional API responses; no generation/provider calls.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const readyID = 'b'.repeat(32);
const makingID = 'a'.repeat(32);
const failedID = 'c'.repeat(32);
const cancelledID = 'd'.repeat(32);
const readyEpisode = require('./fixtures/episodes.json').episodes[0];
const jobs = [
  { job_id: readyID, topic: 'Payments and banking', length: 10, status: 'done', created_at: '2026-11-18T10:00:00Z', episode: { ...readyEpisode, id: readyID, title: readyEpisode.title } },
  { job_id: makingID, topic: 'Fictional weekend transit', length: 5, status: 'writing', stage: 'writing', created_at: '2026-11-18T11:00:00Z' },
  { job_id: failedID, topic: 'Fictional market history', length: 5, status: 'failed', created_at: '2026-11-17T10:00:00Z' },
  { job_id: cancelledID, topic: 'Fictional weather records', length: 5, status: 'cancelled', created_at: '2026-11-16T10:00:00Z' },
];

async function capture(page, filename) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ animations: 'disabled', caret: 'hide', path: path.join(process.env.BRIEFING_SHOT_DIR, filename) });
}

async function openList(browser, options = {}) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York', colorScheme: options.colorScheme || 'light' });
  page.setDefaultTimeout(7000);
  await page.addInitScript(({ saved }) => localStorage.setItem('briefing:v1:state', JSON.stringify({ savedIds: saved })), { saved: options.saved || [] });
  await page.route('**/api/episodes', route => route.fulfill({ json: { jobs: options.jobs ?? jobs, left_today: options.leftToday ?? 2 } }));
  await page.route('**/api/episodes/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1);
    const job = (options.jobs ?? jobs).find(item => item.job_id === id);
    if (!job) return route.fulfill({ status: 404, json: { error: 'job_not_found' } });
    return route.fulfill({ json: job });
  });
  await page.route('**/api/corrections?target_type=episode', route => route.fulfill({ json: { corrections: [{ target_type: 'episode', target_id: readyID, status: 'applied' }] } }));
  await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'profile_not_found' } }));
  await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: 'edition_not_found' } }));
  const initialHash = options.hash || '#episodes';
  await page.goto(`${baseURL}/${initialHash}`, { waitUntil: 'networkidle' });
  if (initialHash.startsWith('#episode-making/')) await page.locator('.episode-history-making').waitFor();
  else await page.locator('.episode-list-page').waitFor();
  return page;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const directMaking = await openList(browser, { hash: `#episode-making/${makingID}` });
    assert.equal(new URL(directMaking.url()).hash, `#episode-making/${makingID}`);
    assert.equal(await directMaking.locator('.episode-history-making h1').innerText(), 'Your episode is being made');
    assert.equal(await directMaking.locator('.episode-history-making .episode-topic').innerText(), 'Fictional weekend transit');
    assert.equal(await directMaking.locator('.episode-history-making .episode-job-card-status').innerText(), 'Writing your episode.', 'A cold deep link explains its current stage in reader language');
    await directMaking.close();

    const page = await openList(browser, { saved: [readyID] });
    const rows = page.locator('.episode-job-card');
    assert.equal(await rows.count(), 4);
    assert.equal(await rows.first().getAttribute('data-job-id'), makingID, 'Newest job sorts first');
    assert.equal(await rows.nth(1).getAttribute('data-job-id'), readyID);
    assert.deepEqual(await page.locator('.episode-status-chip').allTextContents(), ['Making', 'Ready', 'Failed', 'Cancelled']);
    assert.equal(await page.getByText('You can make 2 more episodes today.', { exact: true }).count(), 1);
    const statusLayout = await page.locator('.episode-status-chip').evaluateAll(chips => chips.map(chip => {
      const ring = chip.querySelector('.episode-stage-ring').getBoundingClientRect();
      const label = chip.lastElementChild.getBoundingClientRect();
      return { state: chip.dataset.state, display: getComputedStyle(chip).display,
        aligned: Math.abs((ring.top + ring.height / 2) - (label.top + label.height / 2)) < 1,
        color: getComputedStyle(chip).color, ringWidth: Number.parseFloat(getComputedStyle(chip.querySelector('.episode-stage-ring')).width) };
    }));
    assert.deepEqual(statusLayout.map(item => item.state), ['active', 'done', 'failed', 'cancelled']);
    assert.ok(statusLayout.every(item => ['flex', 'inline-flex'].includes(item.display) && item.aligned && Math.abs(item.ringWidth - 18) < 0.1),
      `Status icon and label form one compact row: ${JSON.stringify(statusLayout)}`);
    assert.equal(new Set(statusLayout.slice(0, 3).map(item => item.color)).size, 3,
      'Making, ready and failed use distinct semantic colors');
    assert.equal(await page.locator('.episode-job-card .episode-updated').count(), 1);
    const allFilter = page.locator('[data-episode-filter="all"]');
    await allFilter.focus(); await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
    assert.equal(await page.locator('[data-episode-filter="making"]').getAttribute('aria-pressed'), 'true', 'Filters can be operated by keyboard');
    await page.locator('[data-episode-filter="all"]').click();

    for (const [filter, expected] of [['Making', makingID], ['Ready', readyID], ['Saved', readyID], ['Corrected', readyID]]) {
      await page.locator('.episode-history-filters').getByRole('button', { name: filter, exact: true }).click();
      assert.equal(await rows.count(), 1, `${filter} filter returns one row`);
      assert.equal(await rows.first().getAttribute('data-job-id'), expected);
    }
    await page.locator('.episode-history-filters').getByRole('button', { name: 'All', exact: true }).click();
    const search = page.getByRole('searchbox', { name: 'Search episodes' });
    await search.fill('weekend transit');
    assert.equal(await rows.count(), 1, 'Search matches topic text');
    assert.equal(await rows.first().getAttribute('data-job-id'), makingID);
    await search.fill('nothing matches');
    await page.getByRole('heading', { name: 'No matching episodes' }).waitFor();
    await search.fill('');

    const makingRow = page.locator(`[data-job-id="${makingID}"]`);
    await makingRow.getByRole('button', { name: 'Save Fictional weekend transit' }).click();
    await page.locator('.episode-history-filters').getByRole('button', { name: 'Saved', exact: true }).click();
    assert.equal(await rows.count(), 2, 'Saved filter includes the previously saved and newly saved items');
    await makingRow.getByRole('button', { name: /Remove Fictional weekend transit from saved episodes/ }).click();
    assert.equal(await rows.count(), 1, 'Unsaving removes that item from Saved filter');
    await page.locator('.episode-history-filters').getByRole('button', { name: 'All', exact: true }).click();

    const failedRow = page.locator(`[data-job-id="${failedID}"]`);
    await failedRow.getByRole('button', { name: 'Delete Fictional market history from this list' }).click();
    assert.equal(await page.locator(`[data-job-id="${failedID}"]`).count(), 0, 'Delete hides the row');
    await page.getByRole('button', { name: 'Undo' }).click();
    assert.equal(await page.locator(`[data-job-id="${failedID}"]`).count(), 1, 'Undo restores the row');

    await page.locator(`[data-job-id="${makingID}"] .episode-job-title`).click();
    await page.locator('.episode-history-making').waitFor();
    assert.equal(new URL(page.url()).hash, `#episode-making/${makingID}`);
    await page.getByRole('link', { name: '‹ Episodes' }).click();
    await page.locator('.episode-list-page').waitFor();
    await page.locator(`[data-job-id="${readyID}"] .episode-job-title`).click();
    await page.locator('.episode-page').waitFor();
    assert.equal(new URL(page.url()).hash, `#episode/${readyID}`);

    await page.goto(`${baseURL}/#episodes`, { waitUntil: 'networkidle' });
    await page.locator('.episode-list-page').waitFor();
    await capture(page, 'episodes-list-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'episodes-list-dark.png');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'List fits the phone viewport without horizontal scrolling');
    assert.equal(await page.locator('.episode-history-filter').evaluateAll(items => items.every(item => item.getBoundingClientRect().height >= 44)), true, 'Filter controls meet the 44px target');
    await page.close();

    const exhausted = await openList(browser, { leftToday: 0 });
    await exhausted.getByText("You have used today's three episodes. More tomorrow.", { exact: true }).waitFor();
    assert.equal(await exhausted.getByText(/0 left today/, { exact: false }).count(), 0, 'A reader never sees a raw zero allowance');
    await exhausted.close();

    const empty = await openList(browser, { jobs: [] });
    await empty.getByRole('heading', { name: 'Ask for your first episode' }).waitFor();
    for (const [filter, heading] of [['Making', 'No episodes being made'], ['Ready', 'No ready episodes yet'], ['Saved', 'No saved episodes'], ['Corrected', 'No corrected episodes']]) {
      await empty.getByRole('button', { name: filter, exact: true }).click();
      await empty.getByRole('heading', { name: heading }).waitFor();
    }
    await empty.close();
    console.log('Episodes list: filters, search, status chips, newest-first order, save/unsave, delete/undo, making deep links, ready/making routes, empty states, phone layout and targets pass.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
