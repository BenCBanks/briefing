require('../test_guard.cjs').enforceTestEnvironment();

// Server-edition revalidation with a mocked clock and fixture API responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, builtProfileKey } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const root = path.join(__dirname, '../..');
const read = file => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
const staticTopics = read('content/topics.json').topics;
const staticDives = read('content/deep-dives.json').deep_dives;
const staticSources = read('content/sources.json').sources;
const staticEdition = read('content/editions.json').editions.find(item => item.kind === 'daily');
const staticWeekly = read('content/editions.json').editions.find(item => item.kind === 'weekly');

function makeEdition(localDate, revision, generatedAt, prefix) {
  return {
    ...structuredClone(staticEdition), id: `revalidation-${revision}`, generated: true,
    revision_id: revision, generated_at: generatedAt, local_date: localDate,
    sections: staticEdition.sections.map((section, index) => ({
      ...section,
      heading: `${prefix}: ${section.heading}`,
      body: `This fictional update keeps a reader's place while it arrives. ${section.body} `.repeat(2),
      lead_paragraphs: [`${prefix} introduces a fictional development.`, section.lead_paragraphs?.[0] || section.body.slice(0, 120)],
    })),
  };
}

async function main() {
  const parsed = new URL(baseURL);
  assert.ok(['127.0.0.1', 'localhost'].includes(parsed.hostname), 'Test must use a loopback preview');
  const profile = structuredClone(DEFAULT_PROFILE);
  const profileKey = builtProfileKey(profile);
  const identity = JSON.stringify([profileKey, profileKey]);
  const stale = makeEdition('2026-11-16', 'cached-r1', '2026-11-16T11:20:00Z', 'Yesterday cached');
  const stalePack = { topics: staticTopics, deepDives: staticDives, sources: staticSources, editions: [stale, staticWeekly] };
  let serverEdition = makeEdition('2026-11-17', 'server-r2', '2026-11-17T11:20:00Z', 'Server latest');
  let holdNext = true;
  let failNext = false;
  let releaseNext;
  let requests = 0;
  const nextResponse = new Promise(resolve => { releaseNext = resolve; });

  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(5000);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(({ profileData, key, pack }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profileData));
      localStorage.setItem('briefing:v1:built-profile-key', key);
      let fakeNow = Date.now();
      Date.now = () => fakeNow;
      window.__advanceClock = ms => { fakeNow += ms; };
      const nativeSetInterval = window.setInterval.bind(window);
      window.setInterval = (callback, delay, ...args) => {
        if (delay === 30 * 60 * 1000) { window.__revalidateTimer = callback; return 902; }
        return nativeSetInterval(callback, delay, ...args);
      };
      localStorage.setItem('briefing:v2:edition-cache', JSON.stringify({ version: 2, profileKey: JSON.stringify([key, key]), savedAt: Date.now(), pack }));
    }, { profileData: profile, key: profileKey, pack: stalePack });
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL && !['POST', 'PUT', 'DELETE'].includes(route.request().method()) ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile } }));
    await page.route('**/api/edition*', async route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('history') === '1') return route.fulfill({ json: { history: [], sources: [] } });
      requests += 1;
      await page.evaluate(count => { document.documentElement.dataset.editionRequests = String(count); }, requests);
      if (holdNext) { holdNext = false; await nextResponse; }
      if (failNext) {
        failNext = false;
        await route.fulfill({ status: 503, json: {} });
        await page.evaluate(count => { document.documentElement.dataset.editionResponses = String(count); }, requests);
        return;
      }
      await route.fulfill({ json: { generated: true, edition: structuredClone(serverEdition), sources: staticSources } });
      await page.evaluate(count => { document.documentElement.dataset.editionResponses = String(count); }, requests);
    });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor().catch(async error => {
      throw new Error(`Cached Today did not paint: ${await page.locator('main').innerText().catch(() => '')}; page errors: ${pageErrors.join(' | ')}; ${error.message}`);
    });
    assert.match(await page.locator('.today-contents').innerText(), /Yesterday cached:/, 'The bounded local cache paints before the held response');
    releaseNext();
    await page.locator('.today-contents').getByText(/Server latest:/).first().waitFor();
    assert.doesNotMatch(await page.locator('.today-contents').innerText(), /Yesterday cached:/, 'A newer server local_date replaces the stale cache');
    assert.equal(await page.locator('.today-title').innerText(), 'Your daily briefing', 'The daily title follows the date');
    const initialNode = await page.locator('.today-page').evaluate(element => { window.__todayNode = element; return true; });
    assert.equal(initialNode, true);

    await page.evaluate(() => {
      window.__advanceClock(6 * 60 * 1000);
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('pageshow'));
    });
    await page.waitForFunction(() => document.documentElement.dataset.editionRequests === '2');
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '2');
    assert.equal(await page.locator('.today-page').evaluate(element => element === window.__todayNode), true, 'An unchanged revalidation keeps Today DOM in place');

    await page.evaluate(() => { window.__advanceClock(60 * 1000); window.dispatchEvent(new Event('focus')); });
    await page.waitForTimeout(100);
    assert.equal(requests, 2, 'Focus within five minutes does not poll again');
    await page.evaluate(() => { window.__advanceClock(30 * 60 * 1000); window.__revalidateTimer(); });
    await page.waitForFunction(() => document.documentElement.dataset.editionRequests === '3');
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '3');

    failNext = true;
    await page.evaluate(() => {
      window.__advanceClock(6 * 60 * 1000);
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForFunction(() => document.documentElement.dataset.editionRequests === '4');
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '4');
    assert.match(await page.locator('.today-contents').innerText(), /Server latest:/, 'A failed revalidation keeps the current edition visible');

    serverEdition = makeEdition('2026-11-18', 'server-r3', '2026-11-18T11:20:00Z', 'New edition');
    const scrollBefore = await page.evaluate(() => {
      window.scrollTo({ top: 650, behavior: 'instant' });
      const current = window.scrollY;
      window.__advanceClock(6 * 60 * 1000);
      document.dispatchEvent(new Event('visibilitychange'));
      return current;
    });
    await page.waitForFunction(() => document.documentElement.dataset.editionRequests === '5');
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '5');
    await page.locator('.today-contents').getByText(/New edition:/).first().waitFor();
    await page.getByRole('heading', { name: 'Your daily briefing' }).waitFor();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.ok(scrollBefore >= 600, 'Replacement is checked while scrolled into the edition');
    const scrollAfter = await page.evaluate(() => window.scrollY);
    assert.ok(Math.abs(scrollAfter - scrollBefore) < 32, `Today keeps scroll near the same reading position during replacement (${scrollBefore} → ${scrollAfter})`);
    assert.doesNotMatch(await page.locator('.today-contents').innerText(), /Server latest:/, 'The older local_date is no longer shown');

    // A delayed/lagging response cannot move Today backwards, including within a day.
    const latest = serverEdition;
    serverEdition = stale;
    await page.evaluate(() => { window.__advanceClock(6 * 60 * 1000); window.dispatchEvent(new Event('pageshow')); });
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '6');
    assert.doesNotMatch(await page.locator('.today-contents').innerText(), /Yesterday cached:/);
    assert.match(await page.locator('.today-contents').innerText(), /New edition:/);
    serverEdition = makeEdition('2026-11-18', 'server-r4', '2026-11-18T12:20:00Z', 'Same-day latest');
    await page.evaluate(() => { window.__advanceClock(6 * 60 * 1000); window.dispatchEvent(new Event('focus')); });
    await page.locator('.today-contents').getByText(/Same-day latest:/).first().waitFor();
    serverEdition = latest;
    await page.evaluate(() => { window.__advanceClock(6 * 60 * 1000); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForFunction(() => document.documentElement.dataset.editionResponses === '8');
    assert.match(await page.locator('.today-contents').innerText(), /Same-day latest:/, 'An older same-day response cannot displace the newest known edition');

    const cacheState = await page.evaluate(async identityKey => {
      const { readEditionCache } = await import('/src/edition/cache.js');
      const saved = JSON.parse(localStorage.getItem('briefing:v2:edition-cache'));
      localStorage.setItem('briefing:v1:edition-cache', JSON.stringify({ ...saved, pack: { bad: true } }));
      localStorage.removeItem('briefing:v2:edition-cache');
      const oldOnly = readEditionCache(identityKey);
      localStorage.setItem('briefing:v2:edition-cache', JSON.stringify(saved));
      return { oldOnly, version: saved.version, date: saved.pack.editions.find(item => item.kind === 'daily')?.local_date,
        readDate: readEditionCache(identityKey)?.editions.find(item => item.kind === 'daily')?.local_date };
    }, identity);
    assert.equal(cacheState.oldOnly, null, 'A v1-only snapshot is discarded');
    assert.equal(cacheState.version, 2, 'Edition cache uses version 2');
    assert.equal(cacheState.date, '2026-11-18', 'Newest edition is persisted in the v2 cache');
    assert.equal(cacheState.readDate, '2026-11-18', 'The old v1 key cannot override the current cache');

    const shots = process.env.BRIEFING_SHOT_DIR;
    if (shots) {
      fs.mkdirSync(shots, { recursive: true });
      await page.evaluate(() => scrollTo(0, 0));
      await page.emulateMedia({ colorScheme: 'light' });
      await page.screenshot({ path: path.join(shots, 'today-latest-light-390.png'), fullPage: false });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(shots, 'today-latest-dark-390.png'), fullPage: false });
    }
    assert.equal(requests, 8);
    assert.deepEqual(pageErrors, []);
    await page.close();
  } finally { await browser.close(); }
  console.log('Edition revalidation: stale cache replacement, local Updated time, five-minute visibility/focus/pageshow checks, 30-minute visible polling, failed-refresh retention, update scroll preservation and v2 cache passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
