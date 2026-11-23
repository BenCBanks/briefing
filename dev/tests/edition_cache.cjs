require('../test_guard.cjs').enforceTestEnvironment();

// Hold requests to prove cached reading does not wait for the network or enable stale audio.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8940';
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/legacy-state/generated', name), 'utf8'));
const original = fixture('editions.json').editions[0];
async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await seedInterestProfile(page);
    let hold = Promise.resolve(), release;
    let edition = structuredClone(original);
    let offline = false;
    const delay = () => { hold = new Promise(resolve => { release = resolve; }); };
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/content/*.json', async route => { await hold; return offline ? route.abort() : route.continue(); });
    await page.route('**/api/profile', async route => { await hold; return route.fulfill({ json: { profile: DEFAULT_PROFILE } }); });
    await page.route('**/api/edition**', async route => {
      await hold;
      return offline ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: { generated: true, edition, sources: fixture('sources.json').sources } });
    });
    await page.addInitScript(() => {
      window.__spoken = [];
      window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
      Object.defineProperty(window, 'speechSynthesis', { value: {
        speaking: false, paused: false, getVoices: () => [],
        speak(utterance) { this.speaking = true; window.__spoken.push(utterance.text); queueMicrotask(() => utterance.onstart?.()); },
        pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.speaking = false; },
      } });
    });
    await page.goto(`${baseURL}/#today`);
    await page.locator('.today-page').waitFor();
    await page.waitForFunction(() => localStorage.getItem('briefing:v2:edition-cache'));
    const cachedTitle = await page.locator('.today-page h1').innerText();
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    const networkBanner = page.locator('.network-banner');
    await networkBanner.waitFor();
    assert.match(await networkBanner.innerText(), /offline.*saved briefing is still here/i, 'Offline status calmly confirms the saved briefing remains available');
    const bannerGeometry = await networkBanner.evaluate(element => {
      const banner = element.getBoundingClientRect(), main = document.querySelector('main').getBoundingClientRect();
      return { bottom: banner.bottom, mainTop: main.top, retryHeight: element.querySelector('button').getBoundingClientRect().height };
    });
    assert.ok(bannerGeometry.bottom <= bannerGeometry.mainTop && bannerGeometry.retryHeight >= 44, 'Network banner sits above the route with a reachable retry control');
    await page.emulateMedia({ colorScheme: 'light' }); await capture(page, 'network-offline-light-390');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'network-offline-dark-390');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await networkBanner.waitFor({ state: 'detached' });
    delay();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor({ timeout: 1500 });
    assert.equal(await page.locator('.today-page h1').innerText(), cachedTitle, 'Cached text paints while all data requests are held');
    assert.equal(await page.locator('.today-listen-pill').isDisabled(), true, 'Cached audio waits for revalidation');
    assert.match(await page.locator('.today-listen-pill').innerText(), /Checking audio/);
    await page.locator('.network-banner').waitFor({ timeout: 7000 });
    assert.match(await page.locator('.network-banner').innerText(), /taking longer than usual.*saved briefing is still here/i, 'A slow refresh explains that cached reading remains available');
    if (await page.locator('.story-more-toggle').first().getAttribute('aria-expanded') === 'false') await page.locator('.story-more-toggle').first().click();
    await page.locator('.story-more [data-source]').first().evaluate(button => button.focus({ preventScroll: true }));
    await page.evaluate(() => window.scrollTo({ top: 300, behavior: 'instant' }));
    await page.evaluate(() => { window.__article = document.querySelector('.today-page'); window.__focus = document.activeElement; window.__y = scrollY; });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => { window.__y = scrollY; resolve(); }))));
    release();
    await page.waitForFunction(() => !document.querySelector('.today-listen-pill').disabled);
    assert.equal(await page.evaluate(() => window.__article === document.querySelector('.today-page')), true, 'Unchanged revalidation keeps the DOM');
    assert.equal(await page.evaluate(() => window.__focus === document.activeElement), true, 'Revalidation keeps keyboard focus');
    const scroll = await page.evaluate(() => ({ before: window.__y, after: scrollY }));
    assert.ok(Math.abs(scroll.before - scroll.after) < 2, `Revalidation keeps scroll (${scroll.before} → ${scroll.after})`);
    assert.equal(await page.locator('.story-more-toggle').first().getAttribute('aria-expanded'), 'true');
    assert.equal(await page.evaluate(() => window.__spoken.length), 0, 'Revalidation never starts speech');

    await page.locator('.today-listen-pill').click();
    await page.waitForFunction(() => window.__spoken.length > 0);
    await page.locator('.today-listen-pill').click();
    const selected = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio);
    delay();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio), selected, 'Cache paint retains the saved audio selection without restoring it');
    release();
    await page.waitForFunction(() => document.querySelector('.today-listen-pill')?.getAttribute('aria-label').startsWith('Resume'));
    assert.equal(await page.locator('.mini-player').count(), 0);
    assert.equal(await page.evaluate(() => window.__spoken.length), 0, 'Restoring verified audio stays paused');

    edition = { ...edition, revision_id: 'cache-corrected-r2', script_hash: 'cache-corrected-script', corrections: [{
      severity: 'material', revision_id: 'cache-corrected-r2', previous_revision_id: original.revision_id,
      corrected_at: '2026-11-17T14:00:00Z', section_ids: [edition.sections[0].id],
      what_changed: 'The fictional confirmation now requires two checks.', why_it_matters: 'The previous version missed a check.',
    }] };
    delay();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    assert.equal(await page.locator('.today-listen-pill').isDisabled(), true);
    release();
    await page.locator('.correction-notice').waitFor();
    assert.equal(await page.locator('.today-listen-pill').isDisabled(), true, 'Correction keeps the persistent audio control disabled');
    await page.locator('.story-row-link').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.story-listen').isDisabled(), true, 'Linked reader also disables corrected audio');
    assert.equal(await page.evaluate(() => window.__spoken.length), 0);
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();
    offline = true;
    await page.reload();
    await page.locator('.cache-notice').waitFor();
    assert.equal(await page.locator('.today-page h1').innerText(), cachedTitle, 'A failed refresh keeps saved reading');
    assert.equal(await page.locator('.today-listen-pill').isDisabled(), true);
    offline = false;
    await page.getByRole('button', { name: 'Retry updates' }).click();
    await page.locator('.cache-notice').waitFor({ state: 'detached' });
    const cacheChecks = await page.evaluate(async () => {
      const { readEditionCache } = await import('/src/edition/cache.js');
      const key = 'briefing:v2:edition-cache'; const raw = localStorage.getItem(key); const saved = JSON.parse(raw);
      const mismatched = readEditionCache('different-profile');
      localStorage.setItem(key, JSON.stringify({ ...saved, savedAt: Date.now() - 49 * 60 * 60 * 1000 }));
      const expired = readEditionCache(saved.profileKey);
      localStorage.setItem(key, '{broken'); const malformed = readEditionCache(saved.profileKey);
      localStorage.setItem(key, raw);
      localStorage.setItem('briefing:v1:edition-cache', JSON.stringify({ ...saved, pack: { obsolete: true } }));
      const obsoleteVersion = readEditionCache(saved.profileKey)?.editions?.find(item => item.kind === 'daily')?.id;
      return { mismatched, expired, malformed, obsoleteVersion };
    });
    assert.deepEqual(cacheChecks, { mismatched: null, expired: null, malformed: null, obsoleteVersion: original.id },
      'A v1 cache entry cannot override the v2 snapshot');
    const idleCorrection = await page.evaluate(async item => {
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); document.body.append(root);
      const player = createPlayer(root, () => {}, () => null);
      player.offer(item);
      const before = root.querySelector('button.mini-player-toggle') === null;
      player.invalidate(item.id);
      const after = root.querySelector('button.mini-player-toggle') === null;
      root.remove(); return { before, after };
    }, original);
    assert.deepEqual(idleCorrection, { before: true, after: true }, 'Idle and corrected offers never display a player');
    assert.deepEqual(errors, []);
    console.log('Held-network paint, stable revalidation, paused restore, correction gates, retry and bounded profile cache passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
