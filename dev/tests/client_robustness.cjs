require('../test_guard.cjs').enforceTestEnvironment();

// Fuzzed API edges stay on fixture data and loopback, with calm retry states.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const fakeError = 'UPSTREAM STACK TRACE: provider key and internal path';
const topicsPayload = fs.readFileSync(path.join(__dirname, '../../content/topics.json'), 'utf8');

async function freshPage(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(8000);
  await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
  await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: fakeError } }));
  return page;
}

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}

async function assertCalmRetry(page, selector, title) {
  await page.getByRole('heading', { name: title }).waitFor();
  assert.equal(await page.locator(selector).count(), 1, 'failed screen exposes a retry button');
  assert.doesNotMatch(await page.locator('main').innerText(), /UPSTREAM STACK TRACE|provider key|internal path/i, 'server error details are not shown');
  assert.ok(await page.locator('main').innerText().then(text => text.length > 0), 'failure leaves a visible page');
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    // Slow initial content never leaves an empty main region.
    {
      const page = await freshPage(browser);
      let release;
      let entered;
      const requestEntered = new Promise(resolve => { entered = resolve; });
      await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: { error: fakeError } }));
      await page.route('**/content/topics.json', async route => {
        await new Promise(resolve => { release = resolve; entered(); });
        return route.fulfill({ contentType: 'application/json', body: topicsPayload });
      });
      await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('heading', { name: 'Your briefing is loading' }).waitFor();
      assert.match(await page.locator('main').innerText(), /Getting today’s stories ready/);
      assert.equal(await page.locator('.today-list-skeleton .today-story-skeleton').count(), 3);
      assert.equal(await page.locator('.today-list-skeleton').getAttribute('aria-hidden'), 'true');
      await capture(page, 'today-list-loading-light');
      await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'today-list-loading-dark');
      await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
      assert.equal(await page.locator('.today-skeleton-topic').first().evaluate(el => getComputedStyle(el).animationName), 'none', 'Reduced motion leaves Today skeleton shapes static');
      await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
      await requestEntered;
      release();
      await page.locator('.today-page').waitFor();
      await page.close();
    }

    // A malformed generated edition must leave the valid static Today pack usable.
    {
      const page = await freshPage(browser);
      await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition: { generated: true, id: 'broken-fixture', revision_id: null } } }));
      await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
      await page.locator('.today-page').waitFor();
      assert.ok(await page.locator('.today-story-row').count() > 0, 'bad optional API edition falls back to static stories');
      await page.close();
    }

    // Slow success shows a loading state; malformed rows and dates are contained.
    {
      const page = await freshPage(browser);
      let release;
      let entered;
      const requestEntered = new Promise(resolve => { entered = resolve; });
      await page.route('**/api/episodes', async route => {
        if (route.request().method() !== 'GET') return route.fulfill({ status: 405, json: {} });
        await new Promise(resolve => { release = resolve; entered(); });
        return route.fulfill({ json: { jobs: [null, {}, {
          job_id: 'fixture-job', status: 'ready', topic: 'Fictional permit records', created_at: 'not-a-date',
          episode: { title: 'Fictional permit records episode', duration_minutes: 5 },
        }] } });
      });
      await page.goto(`${baseURL}/#episodes`, { waitUntil: 'domcontentloaded' });
      await page.locator('.episode-loading').waitFor();
      assert.match(await page.locator('.episode-loading').innerText(), /Loading episode/);
      await requestEntered;
      release();
      const card = page.locator('.episode-job-card');
      await card.waitFor();
      assert.match(await card.innerText(), /Fictional permit records episode/);
      assert.doesNotMatch(await card.innerText(), /Invalid Date|not-a-date/);
      await page.close();
    }

    // A server failure has a retry, and retry can recover without reloading the app.
    {
      const page = await freshPage(browser);
      let calls = 0;
      await page.route('**/api/corrections?target_type=episode', route => route.fulfill({ status: 500, body: fakeError }));
      await page.route('**/api/episodes', route => {
        calls++;
        return calls === 1
          ? route.fulfill({ status: 500, body: fakeError })
          : route.fulfill({ json: { jobs: [{ id: 'recovered-fixture', status: 'ready', topic: 'Fictional transit rules', episode: { title: 'Fictional transit rules' } }] } });
      });
      await page.goto(`${baseURL}/#episodes`, { waitUntil: 'domcontentloaded' });
      await assertCalmRetry(page, '.episode-list-retry', 'Episodes');
      await page.locator('.episode-list-retry').click();
      await page.getByText('Fictional transit rules', { exact: true }).waitFor();
      assert.equal(calls, 2, 'Retry requests a fresh list');
      await page.close();
    }

    // The on-demand list uses meaningful skeleton cards while the fixture request is pending.
    {
      const page = await freshPage(browser);
      let release;
      let entered;
      const requestEntered = new Promise(resolve => { entered = resolve; });
      await page.route('**/api/episodes', async route => {
        if (route.request().method() !== 'GET') return route.fulfill({ status: 405, json: {} });
        await new Promise(resolve => { release = resolve; entered(); });
        return route.fulfill({ json: { jobs: [] } });
      });
      await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
      const cards = page.locator('.ask-episode-cards');
      await page.locator('.episode-list-skeleton').waitFor();
      await requestEntered;
      assert.equal(await cards.getAttribute('aria-busy'), 'true');
      assert.equal(await page.locator('.episode-card-skeleton').count(), 2);
      assert.equal(await page.locator('.episode-list-skeleton').getAttribute('aria-hidden'), 'true');
      assert.equal(await page.locator('.ask-episode-cards .state-pattern').count(), 0, 'The pending list has no bare Loading message');
      await page.locator('.ask-episodes').scrollIntoViewIfNeeded();
      await capture(page, 'on-demand-list-loading-light');
      await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'on-demand-list-loading-dark');
      await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
      assert.equal(await page.locator('.episode-skeleton-title').first().evaluate(el => getComputedStyle(el).animationName), 'none', 'Reduced motion leaves a static loading shape');
      release();
      await page.getByText('No on-demand briefings yet. Your first one will appear here.', { exact: true }).waitFor();
      assert.equal(await cards.getAttribute('aria-busy'), 'false');
      await page.close();
    }

    // The Ask screen also recovers when its related-episode list is unavailable or malformed.
    {
      const page = await freshPage(browser);
      let calls = 0;
      await page.route('**/api/episodes', route => {
        calls++;
        if (calls === 1) return route.fulfill({ status: 500, body: fakeError });
        if (calls === 2) return route.fulfill({ json: { jobs: [null] } });
        return route.fulfill({ json: { jobs: [{ job_id: 'ask-fixture-job', status: 'ready', topic: 'Fictional bus schedules' }] } });
      });
      await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
      await page.getByText('Your episodes could not be loaded. Try again in a moment.').waitFor();
      assert.doesNotMatch(await page.locator('main').innerText(), /UPSTREAM STACK TRACE|provider key/);
      await page.getByRole('button', { name: 'Try again' }).click();
      await page.getByText('Your episodes could not be loaded. Try again in a moment.').waitFor();
      await page.getByRole('button', { name: 'Try again' }).click();
      await page.getByRole('link', { name: /Fictional bus schedules/ }).waitFor();
      assert.equal(calls, 3);
      await page.close();
    }

    // Invalid detail payloads never produce a partial/raw page and can be retried.
    {
      const page = await freshPage(browser);
      const id = 'a'.repeat(32);
      await page.route(`**/api/episodes/${id}`, route => route.fulfill({ json: { status: 'done', message: fakeError, episode: { title: 'Fictional incomplete episode' } } }));
      await page.goto(`${baseURL}/?episodes=1#episode/${id}`, { waitUntil: 'domcontentloaded' });
      await assertCalmRetry(page, '.episode-route-retry', 'Episode unavailable');
      await page.close();
    }

    // A bad narrator manifest has a retry and recovers from a later valid response.
    {
      const page = await freshPage(browser);
      let calls = 0;
      await page.route('**/audition-audio/manifest.json', route => {
        calls++;
        if (calls === 1) return route.fulfill({ status: 500, body: fakeError });
        return route.fulfill({ json: { samples: ['A', 'B', 'C', 'D', 'E', 'F'].map(label => ({
          label, voice: `voice-${label}`, file: `sample-${label}.wav`,
        })) } });
      });
      await page.goto(`${baseURL}/#preferences`, { waitUntil: 'domcontentloaded' });
      await page.getByText('Voice samples are unavailable right now.').waitFor();
      await page.getByRole('button', { name: 'Try again' }).click();
      await page.locator('.narrator-use').first().waitFor();
      assert.equal(await page.locator('.narrator-use').count(), 6, 'valid retry restores all six samples');
      assert.doesNotMatch(await page.locator('main').innerText(), /UPSTREAM STACK TRACE|provider key/);
      await page.close();
    }

    // Invalid required content falls back to the app-level calm retry page.
    {
      const page = await freshPage(browser);
      await page.route('**/content/topics.json', route => route.fulfill({ contentType: 'application/json', body: '{not json' }));
      await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('heading', { name: 'Your briefing is unavailable' }).waitFor();
      await page.getByRole('button', { name: 'Retry' }).waitFor();
      assert.doesNotMatch(await page.locator('main').innerText(), /SyntaxError|Unexpected token|not json/i);
      assert.ok((await page.locator('main').innerText()).trim().length > 0);
      await page.close();
    }
    console.log('Client robustness: malformed editions, slow list responses, 500s, malformed rows/dates and detail payloads stay calm and recoverable.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
