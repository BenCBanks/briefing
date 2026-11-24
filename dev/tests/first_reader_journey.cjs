require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const editions = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'));
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const baseEdition = editions.editions.find(item => item.kind === 'daily');
const edition = structuredClone(baseEdition);
edition.id = 'first-reader-edition';
edition.revision_id = 'first-reader-r1';
edition.generated = true;
edition.title = 'A fictional briefing about local water policy';
edition.coverage = [{ topic_id: 'local-water-policy', label: 'Local water policy', state: 'covered' }];
edition.sections = edition.sections.slice(0, 1).map(section => ({
  ...section,
  title: 'Worth knowing about local water policy',
  headline: 'Worth knowing about local water policy',
  text: 'A fictional local report describes a public water quality review.',
  body: 'A fictional local report describes a public water quality review.',
  topic_ids: ['local-water-policy'],
}));
const profile = { version: 1, words: 'Local water policy', minutes: 12, background: null,
  topics: [{ id: 'local-water-policy', label: 'Local water policy', scope: 'Local water policy',
    include: ['local water policy'], exclude: [], depth: 'standard', cadence: 'daily',
    mustFollow: false, sourcePhrase: 'local water policy' }], muted: [] };
const generated = { ...edition, local_date: '2026-11-24', generated_at: '2026-11-24T06:00:00-05:00' };
const jobId = 'f00dbabe1234567890abcdef12345678';
let finished = false;
let pollCount = 0;
const accessibilityFindings = [];
const chapters = [{ id: 'chapter-one', title: 'The short answer', audio_start_seconds: 0,
  duration_seconds: 4, audio_url: `/api/episodes/${jobId}/chapters/chapter-one/audio` }];

function assertPlainLanguage(text, step) {
  assert.doesNotMatch(text, /\b[a-z]+(?:_[a-z]+)+\b|insufficient evidence|usage cap reached|generation failed|script checks failed|\bfallback\b|\bverification\b/i,
    `${step}: visible copy uses reader-facing language`);
}

async function assertAccessibleAt(page, width, step) {
  await page.setViewportSize({ width, height: width === 160 ? 422 : 844 });
  await page.evaluate(() => document.fonts.ready);
  if (process.env.BRIEFING_SHOT_DIR) {
    fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
    const state = step.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, `${state}-${width}.png`) });
  }
  const result = await page.evaluate(() => {
    const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
    const controls = [...document.querySelectorAll('main button, main a[href], main input, main textarea, main select, .main-nav a')]
      .filter(visible);
    const smallTargets = controls.map(element => {
      const rect = element.getBoundingClientRect();
      return { label: element.getAttribute('aria-label') || element.innerText || element.tagName,
        className: element.className, width: rect.width, height: rect.height,
        parentWidth: element.parentElement?.getBoundingClientRect().width,
        display: getComputedStyle(element).display, cssWidth: getComputedStyle(element).width,
        ancestors: [element.parentElement, element.parentElement?.parentElement, element.parentElement?.parentElement?.parentElement, element.closest('main')]
          .filter(Boolean).map(parent => ({ tag: parent.tagName, className: parent.className,
            width: parent.getBoundingClientRect().width, display: getComputedStyle(parent).display })) };
    }).filter(item => item.width < 44 || item.height < 44);
    const clipped = [...document.querySelectorAll('main h1, main h2, main h3, main p, main button, main a, main label, main span, main strong, main small')]
      .filter(visible).filter(element => !element.classList.contains('episode-visually-hidden')).filter(element => {
        const style = getComputedStyle(element);
        const clamp = Number.parseInt(style.getPropertyValue('-webkit-line-clamp'), 10);
        return element.scrollWidth > element.clientWidth + 2 ||
          (['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 2) ||
          (Number.isFinite(clamp) && clamp > 0 && element.scrollHeight > element.clientHeight + 2);
      }).filter(element => !element.classList.contains('reader-back'))
      .map(element => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}: ${element.innerText}`);
    const brokenWords = [...document.querySelectorAll('.main-nav-label, .reader-back')].filter(visible).flatMap(element => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      return nodes.flatMap(node => [...node.textContent.matchAll(/\S+/g)].flatMap(match => {
        const range = document.createRange();
        range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
        return range.getClientRects().length > 1 ? [`${element.className || element.tagName}: ${match[0]}`] : [];
      }));
    });
    return { viewport: innerWidth, document: document.documentElement.scrollWidth,
      smallTargets, clipped, brokenWords };
  });
  if (result.document > result.viewport) accessibilityFindings.push({ step, width, issue: 'horizontal page overflow', details: result });
  if (result.smallTargets.length) accessibilityFindings.push({ step, width, issue: 'interactive targets under 44px', details: result.smallTargets });
  if (result.clipped.length) accessibilityFindings.push({ step, width, issue: 'visible text clips', details: result.clipped });
  if (result.brokenWords.length) accessibilityFindings.push({ step, width, issue: 'words wrap mid-word', details: result.brokenWords });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
      : route.fulfill({ json: { ok: true } }));
    await page.route('**/api/interpret', route => route.fulfill({ json: { profile, question: null } }));
    await page.route('**/api/generate', route => route.fulfill({ json: { job: { id: 'first-reader-build' } } }));
    await page.route('**/api/generate/first-reader-build', route => route.fulfill({ json: {
      status: 'complete', edition_ready: { edition: generated }, progress: { stage: 'complete', completed: 1, total: 1 },
    } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition: generated, sources } }));
    await page.route('**/api/episodes', route => {
      if (route.request().method() !== 'POST') return route.fulfill({ json: {
        jobs: finished ? [{ job_id: jobId, topic: 'Local water policy', length: 5, status: 'done', stage: 'done', episode: {
          id: jobId, title: 'A fictional local water policy episode', topic_label: 'Local water policy',
          revision_id: 'first-reader-episode-r1', script_hash: 'first-reader-script', audio_status: 'recorded',
          audio: { status: 'recorded', revision_id: 'first-reader-episode-r1', script_hash: 'first-reader-script',
            url: '/dev/tests/fixtures/audio/episode-tone.wav', duration_seconds: 4 },
          chapters: [{ id: 'chapter-one', title: 'The short answer', sentences: [
            { text: 'This is a fictional account of a town water review.', source_ids: [], source_numbers: [] },
          ] }], sources: [],
        } }] : [], left_today: 3,
      } });
      return route.fulfill({ status: 202, json: { job_id: jobId, status: 'queued', stage: 'queued', counts: {},
        streaming: { status: 'making', expected_chapters: null, ready_chapters: [], progress: 0 } } });
    });
    await page.route(`**/api/episodes/${jobId}`, route => {
      pollCount += 1;
      const episode = { id: jobId, title: 'A fictional local water policy episode', topic_label: 'Local water policy',
        revision_id: 'first-reader-episode-r1', script_hash: 'first-reader-script', audio_status: 'recorded',
        audio: { status: 'recorded', revision_id: 'first-reader-episode-r1', script_hash: 'first-reader-script',
          url: '/dev/tests/fixtures/audio/episode-tone.wav', duration_seconds: 4 },
        chapters: [{ id: 'chapter-one', title: 'The short answer', sentences: [
          { text: 'This is a fictional account of a town water review.', source_ids: [], source_numbers: [] },
        ] }], sources: [],
      };
      return route.fulfill({ json: finished
        ? { job_id: jobId, status: 'done', stage: 'done', topic: 'Local water policy', length: 5,
          episode, streaming: { status: 'complete', expected_chapters: 1, ready_chapters: chapters, progress: 1 } }
        : { job_id: jobId, status: 'recording', stage: 'recording', topic: 'Local water policy', length: 5,
          counts: { sources_found: 2, sources_read: 2 }, streaming: { status: 'playable', expected_chapters: 1,
            ready_chapters: chapters, progress: 1 } } });
    });
    await page.route(`**/api/episodes/${jobId}/chapters/*/audio`, route => route.fulfill({
      status: 200, contentType: 'audio/wav', body: fs.readFileSync(path.join(__dirname, 'fixtures/audio/episode-tone.wav')),
    }));

    await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'domcontentloaded' });
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:interest-profile')), null,
      'A new reader starts without saved topics');
    await page.locator('#interest-words').fill('Local water policy');
    await page.getByRole('button', { name: 'Build my briefing' }).click();
    await page.waitForFunction(() => location.hash === '#today' || document.querySelector('.interest-build-error'), null, { timeout: 10000 });
    await page.locator('.today-page').waitFor();
    assertPlainLanguage(await page.locator('body').innerText(), 'Today');
    assert.equal(await page.getByText('Worth knowing about local water policy', { exact: true }).count(), 1,
      'Today presents the thin-day story in plain language');
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await assertAccessibleAt(page, 320, `Today with a thin-day story ${theme}`);
      await assertAccessibleAt(page, 160, `Today with a thin-day story at 200% ${theme}`);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[data-nav="on-demand"]').click();
    await page.locator('.episode-request-page').waitFor();
    await page.locator('#episode-request-prompt').fill('What changed in local water policy?');
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    await page.locator('.episode-streaming-chapters').waitFor();
    assertPlainLanguage(await page.locator('body').innerText(), 'Making');
    assert.equal(await page.locator('.episode-streaming-progress').innerText(), '1 of 1 chapters ready');
    assert.equal(await page.locator('.episode-streaming-play').isVisible(), true);
    if (process.env.BRIEFING_SHOT_DIR) {
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, 'making-320.png') });
    }
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await assertAccessibleAt(page, 320, `Streaming episode ${theme}`);
      await assertAccessibleAt(page, 160, `Streaming episode at 200% ${theme}`);
    }
    await page.emulateMedia({ colorScheme: 'light' });

    await page.setViewportSize({ width: 390, height: 844 });
    finished = true;
    await page.waitForFunction(() => document.querySelector('.episode-done-card') !== null, null, { timeout: 10000 });
    assertPlainLanguage(await page.locator('body').innerText(), 'Ready');
    assert.ok(pollCount > 0, 'The fixture job was polled while it was being made');
    await page.getByRole('button', { name: 'Open episode' }).click();
    await page.locator('.episode-page').waitFor();
    assertPlainLanguage(await page.locator('body').innerText(), 'Episode');
    await page.getByRole('button', { name: /Play episode/ }).click();
    await page.locator('.player-dialog').waitFor();
    assertPlainLanguage(await page.locator('body').innerText(), 'Playing');
    assert.deepEqual(errors, [], 'The first-reader journey has no browser errors');
    assert.deepEqual(accessibilityFindings, [], 'Thin-day and streaming screens have no reflow, clipping, or small-target findings');
    console.log('Accessibility findings:', JSON.stringify(accessibilityFindings));
    console.log('First-reader journey: topics, Today, a thin-day story, making, ready, and playback passed; 320px and 200% checks were recorded.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
