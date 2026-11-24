require('../test_guard.cjs').enforceTestEnvironment();

// P0 episode entry, API workflow and first-run edition guard. All data and jobs are fictional.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8931';
const shots = process.env.BRIEFING_SHOT_DIR;
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/topics.json'), 'utf8')).topics;
const samplePack = {
  topics: JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/topics.json'), 'utf8')),
  dives: JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/deep-dives.json'), 'utf8')),
  sources: JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')),
  editions: JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')),
};
const jobId = 'c0ffee1234567890abcdef1234567890';

function profileFixture() {
  return { version: 1, words: 'I follow payments, banking, and digital privacy.', minutes: 12, background: null, muted: [],
    topics: catalog.slice(0, 5).map(topic => ({ id: topic.id, label: topic.label, scope: topic.scope,
      include: [topic.label], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: topic.label })) };
}

function generatedFixture() {
  const edition = structuredClone(samplePack.editions.editions.find(item => item.kind === 'daily'));
  edition.id = 'fictional-active-profile-edition'; edition.revision_id = 'fictional-active-profile-r1';
  edition.local_date = '2026-11-17'; edition.generated = true;
  return edition;
}

async function screenshot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`) });
}

async function auditLargeText(page, prefix) {
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  const audit = await page.evaluate(() => {
    const controls = [...document.querySelectorAll('main button, main a[href], main summary')]
      .filter(element => element.getClientRects().length)
      .map(element => ({ label: (element.innerText || element.getAttribute('aria-label') || '').trim(),
        width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }));
    const clipped = [...document.querySelectorAll('main :is(h1,h2,h3,p,a,button,label,summary,span,strong,small)')]
      .filter(element => element.textContent.trim() && element.getClientRects().length)
      .filter(element => {
        const style = getComputedStyle(element);
        return style.textOverflow === 'ellipsis' ||
          (['hidden', 'clip'].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1) ||
          (['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1);
      }).map(element => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')} clips text`);
    const canvas = document.createElement('canvas'); const context = canvas.getContext('2d');
    const tabs = [...document.querySelectorAll('.main-nav-label')].map(label => {
      const style = getComputedStyle(label); context.font = style.font;
      const longestWord = Math.max(...label.textContent.trim().split(/\s+/).map(word => context.measureText(word).width));
      return { label: label.textContent.trim(), width: label.clientWidth, longestWord,
        fontSize: parseFloat(style.fontSize), wrap: style.overflowWrap, wordBreak: style.wordBreak };
    });
    return { rootSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
      width: document.documentElement.scrollWidth, viewport: innerWidth, controls, clipped, tabs };
  });
  assert.ok(audit.rootSize >= 32, `${prefix}: large text scale applied (${audit.rootSize}px)`);
  assert.ok(audit.width <= audit.viewport, `${prefix}: no horizontal overflow (${audit.width} > ${audit.viewport})`);
  assert.deepEqual(audit.clipped, [], `${prefix}: all visible text wraps: ${audit.clipped.join('; ')}`);
  const small = audit.controls.filter(control => control.width < 44 || control.height < 44);
  assert.deepEqual(small, [], `${prefix}: controls meet 44px targets: ${JSON.stringify(small)}`);
  assert.ok(audit.tabs.every(tab => tab.fontSize >= 16 && tab.longestWord <= tab.width + 1 && tab.wrap === 'normal' && tab.wordBreak === 'normal'),
    `${prefix}: tab labels stay readable without splitting words: ${JSON.stringify(audit.tabs)}`);
  await screenshot(page, `${prefix}-large-text-light`);
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, `${prefix}-large-text-dark`);
  await page.emulateMedia({ colorScheme: 'light' });
  if (prefix === 'episode-page') {
    await page.locator('.episode-transcript').scrollIntoViewIfNeeded();
    await screenshot(page, 'episode-transcript-large-text-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'episode-transcript-large-text-dark');
    await page.emulateMedia({ colorScheme: 'light' });
  }
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
}

async function installBaseRoutes(page, { activeProfile = null, activeEdition = null, createStatus = 202, episodeListStatus = 200 } = {}) {
  let generatedCalls = 0;
  let pollCount = 0;
  let createdPayload = null;
  let cancelCalls = 0;
  await page.route('**/api/profile', async route => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
    return activeProfile
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ profile: activeProfile }) })
      : route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });
  await page.route('**/api/status', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
  }));
  await page.route('**/api/edition**', route => activeEdition
    ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: true, edition: activeEdition, sources: samplePack.sources.sources }) })
    : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: false, sources: [] }) }));
  await page.route('**/api/generate', async route => {
    generatedCalls += 1;
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'fixture_generation_blocked' }) });
  });
  await page.route('**/api/episodes/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/cancel')) {
      cancelCalls += 1;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'cancelled' }) });
    }
    const stageRows = [
      ['researching', { chapters: 0, sentences: 0, sources: 1 }],
      ['reading', { chapters: 1, sentences: 2, sources: 2 }],
      ['checking', { chapters: 2, sentences: 4, sources: 2 }],
      ['writing', { chapters: 3, sentences: 6, sources: 2 }],
      ['recording', { chapters: 3, sentences: 6, sources: 2 }],
    ];
    const selected = stageRows[Math.min(pollCount++, stageRows.length - 1)];
    const done = pollCount > stageRows.length;
    const episode = done ? {
      id: jobId, topic: createdPayload?.topic, topic_label: createdPayload?.topic,
      asked: createdPayload?.topic, title: `Fictional briefing on ${createdPayload?.topic}`,
      style: createdPayload?.style, duration_minutes: createdPayload?.length, made_date: '2026-11-18', audio_recorded: false,
      sources: [
        { id: 'fiction-source-1', outlet: 'Harbor Ledger · fictional', date: '18 Nov', url: 'https://harbor.example.test/story' },
        { id: 'fiction-source-2', outlet: 'Civic Desk · fictional', date: '18 Nov', url: 'https://civic.example.test/story' },
      ],
      chapters: [{ id: 'chapter-one', title: 'The fictional update', sentences: [{ text: 'A fictional topic received a local update.', source_ids: ['fiction-source-1'], source_numbers: [1] }] }],
    } : null;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      job_id: jobId, topic: createdPayload?.topic, length: createdPayload?.length,
      status: done ? 'done' : selected[0], stage: done ? 'done' : selected[0],
      counts: done ? { chapters: 1, sentences: 1, sources: 2 } : selected[1],
      episode, finished_at: done ? '2026-11-18T12:00:00Z' : null,
    }) });
  });
  await page.route('**/api/episodes', async route => {
    if (route.request().method() !== 'POST') return route.fulfill({ status: episodeListStatus, contentType: 'application/json', body: JSON.stringify(episodeListStatus === 200 ? { jobs: [], left_today: 3 } : { error: 'fixture_list_unavailable' }) });
    createdPayload = route.request().postDataJSON();
    if (createStatus === 429) return route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ error: 'daily_limit_reached', left_today: 2 }) });
    return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ job_id: jobId, status: 'queued', stage: 'queued', counts: {}, left_today: 2 }) });
  });
  return { get generatedCalls() { return generatedCalls; }, get createdPayload() { return createdPayload; }, get cancelCalls() { return cancelCalls; } };
}

async function newPage(browser) {
  return browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await newPage(browser);
    const mainRoutes = await installBaseRoutes(page);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    const prompt = page.getByRole('link', { name: /Brief me on something else/ }).first();
    await prompt.waitFor();
    await screenshot(page, 'today-episode-entry-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'today-episode-entry-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await prompt.click();
    await page.locator('.episode-request-page').waitFor();
    await screenshot(page, 'compose-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'compose-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.equal(await page.getByText('Preview only', { exact: true }).count(), 0);
    assert.equal(await page.getByText(/does not create or save an episode/i).count(), 0);
    await page.locator('#episode-request-prompt').fill('Fictional Alder Bay transit');
    await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '10 min' }).click();
    await page.getByRole('button', { name: 'Create episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    assert.equal((await page.locator('.episode-stage-row').count()), 5);
    await page.locator('.episode-stage-row[data-state="active"]').waitFor();
    await screenshot(page, 'episode-making-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-making-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.equal(await page.locator('.episode-making-meta').innerText(), 'You chose 10 min');
    await page.locator('.episode-progress-note').waitFor({ timeout: 10000 });
    assert.deepEqual(mainRoutes.createdPayload, { topic: 'Fictional Alder Bay transit', length: 10, style: 'news-brief' });
    await page.getByRole('button', { name: 'Open transcript' }).waitFor({ timeout: 15000 });
    const apiState = await page.evaluate(() => ({ hash: location.hash }));
    assert.equal(apiState.hash, '#episode-making');
    await screenshot(page, 'episode-done-light');
    await page.getByRole('button', { name: 'Open transcript' }).click();
    await page.locator('.episode-page h1').waitFor();
    assert.match(await page.locator('.episode-page h1').innerText(), /Fictional briefing on Fictional Alder Bay transit/);
    await screenshot(page, 'episode-page-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-page-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await auditLargeText(page, 'episode-page');
    assert.deepEqual((await page.locator('.episode-citation summary').first().getAttribute('aria-label')), 'Source 1, Harbor Ledger · fictional');

    const earlier = await newPage(browser);
    await installBaseRoutes(earlier);
    await earlier.goto(`${baseURL}/#library`, { waitUntil: 'domcontentloaded' });
    await earlier.locator('.library-page h1, main h1').first().waitFor();
    await auditLargeText(earlier, 'earlier-editions');
    await earlier.getByRole('button', {name:'Episodes',exact:true}).click();
    await earlier.getByRole('link', { name: 'Ask for a briefing', exact: true }).waitFor();
    assert.equal(await earlier.locator('.earlier-episodes a[href],.earlier-episodes button').count(), 1, 'Earlier empty state has one action');
    await screenshot(earlier, 'earlier-light');
    await earlier.emulateMedia({ colorScheme: 'dark' }); await screenshot(earlier, 'earlier-dark');
    await earlier.emulateMedia({ colorScheme: 'light' });
    await auditLargeText(earlier, 'earlier');
    await earlier.getByRole('link', { name: 'Ask for a briefing', exact: true }).click();
    await earlier.locator('.episode-request-page').waitFor();

    const earlierError = await newPage(browser);
    await installBaseRoutes(earlierError, { episodeListStatus: 503 });
    await earlierError.goto(`${baseURL}/#library`, { waitUntil: 'domcontentloaded' });
    await earlierError.locator('.library-filters').waitFor();
    await earlierError.getByRole('button', { name: 'Episodes', exact: true }).click();
    await earlierError.getByText('Earlier episodes could not be loaded. Try again in a moment.', { exact: true }).waitFor();
    assert.equal(await earlierError.locator('.earlier-episodes a[href],.earlier-episodes button').count(), 1, 'Earlier error state has one Retry action');
    await earlierError.evaluate(() => document.activeElement?.blur());
    await screenshot(earlierError, 'earlier-error-light');
    await earlierError.emulateMedia({ colorScheme: 'dark' });
    await screenshot(earlierError, 'earlier-error-dark');

    const topics = await newPage(browser);
    await installBaseRoutes(topics);
    await topics.goto(`${baseURL}/#topics`, { waitUntil: 'domcontentloaded' });
    await topics.locator('.topics-words-form').waitFor();
    assert.equal(await topics.locator('.topics-ask-entry').count(), 0, 'Topics has no lone Ask link');
    await screenshot(topics, 'topics-light');
    await topics.emulateMedia({ colorScheme: 'dark' }); await screenshot(topics, 'topics-dark');

    const limited = await newPage(browser);
    await installBaseRoutes(limited, { createStatus: 429 });
    await limited.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
    await limited.locator('#episode-request-prompt').fill('Fictional river ferry service');
    await limited.getByRole('button', { name: 'Create episode' }).click();
    await limited.getByRole('status').getByText('You can make 2 more episodes today.').waitFor();
    assert.equal(new URL(limited.url()).hash, '#ask-episode');

    const cancelPage = await newPage(browser);
    const cancelRoutes = await installBaseRoutes(cancelPage);
    await cancelPage.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
    await cancelPage.locator('#episode-request-prompt').fill('Fictional town library hours');
    await cancelPage.getByRole('button', { name: 'Create episode' }).click();
    await cancelPage.locator('.episode-making-page').waitFor();
    await cancelPage.getByRole('button', { name: 'Cancel this episode' }).click();
    await cancelPage.locator('.episode-request-page').waitFor();
    assert.equal(cancelRoutes.cancelCalls, 1, 'Cancel reaches the job API');

    const fresh = await newPage(browser);
    const profile = profileFixture();
    const existing = generatedFixture();
    const freshRoutes = await installBaseRoutes(fresh, { activeProfile: profile, activeEdition: existing });
    await fresh.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await fresh.locator('.today-page').waitFor();
    await fresh.locator('.today-chapter-title').filter({ hasText: existing.sections[0].heading }).waitFor();
    assert.equal(freshRoutes.generatedCalls, 0, 'existing active-profile edition does not start a build in a new browser');

    const missing = await newPage(browser);
    const missingRoutes = await installBaseRoutes(missing, { activeProfile: profile });
    const missingBuild = missing.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/generate' &&
      response.request().method() === 'POST');
    await missing.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    assert.equal((await missingBuild).status(), 503);
    await missing.getByRole('heading', { name: 'Today’s stories did not load' }).waitFor();
    assert.equal(missingRoutes.generatedCalls, 1, 'a build starts when the active profile has no edition');

    console.log('Episode entry P0: visible Today/Earlier/Topics entry points, API create/poll/done route, 429 allowance and fresh-browser edition guard passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
