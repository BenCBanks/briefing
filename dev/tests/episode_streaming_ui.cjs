require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8937';
const generated = path.join(__dirname, 'fixtures/legacy-state/generated');
const edition = JSON.parse(fs.readFileSync(path.join(generated, 'editions.json'), 'utf8')).editions[0];
const sources = JSON.parse(fs.readFileSync(path.join(generated, 'sources.json'), 'utf8')).sources;
const tone = fs.readFileSync(path.join(__dirname, 'fixtures/audio/episode-tone.wav'));
const jobId = 'f00dbabe1234567890abcdef12345678';
const firstChapter = {
  id: 'chapter-one', title: 'The harbor update', audio_start_seconds: 0,
  duration_seconds: 4, audio_url: `/api/episodes/${jobId}/chapters/chapter-one/audio`,
};
const remainingChapters = [
  { id: 'chapter-two', title: 'What changed', audio_start_seconds: 4, duration_seconds: 4,
    audio_url: `/api/episodes/${jobId}/chapters/chapter-two/audio` },
  { id: 'chapter-three', title: 'What comes next', audio_start_seconds: 8, duration_seconds: 4,
    audio_url: `/api/episodes/${jobId}/chapters/chapter-three/audio` },
];

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ animations: 'disabled', caret: 'hide',
    path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}

async function openFlow(browser, streamingEnabled) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
  page.setDefaultTimeout(8000);
  const errors = [];
  let pollCount = 0;
  let finish = false;
  let audioRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
  await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE, ok: true } }));
  await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
  await page.route('**/api/generate**', route => route.fulfill({ status: 503, json: { error: 'fixture_generation_blocked' } }));
  await page.route('**/api/episodes', route => {
    if (route.request().method() !== 'POST') {
      const job = {
        job_id: jobId, topic: 'Fictional harbor updates', length: 5,
        status: finish ? 'done' : 'recording', stage: finish ? 'done' : 'recording',
        streaming: streamingEnabled ? (finish
          ? { status: 'complete', expected_chapters: 3, ready_chapters: [firstChapter, ...remainingChapters], progress: 1 }
          : { status: 'playable', expected_chapters: 3, ready_chapters: [firstChapter], progress: 0.333 }) : undefined,
      };
      return route.fulfill({ json: { jobs: streamingEnabled ? [job] : [], left_today: 2 } });
    }
    const streaming = streamingEnabled
      ? { status: 'making', expected_chapters: null, ready_chapters: [], progress: 0 }
      : undefined;
    return route.fulfill({ status: 202, json: { job_id: jobId, status: 'queued', stage: 'queued',
      counts: {}, ...(streaming ? { streaming } : {}) } });
  });
  await page.route(`**/api/episodes/${jobId}/chapters/*/audio`, route => {
    audioRequests += 1;
    return route.fulfill({ status: 200, contentType: 'audio/wav', body: tone });
  });
  await page.route(`**/api/episodes/${jobId}`, route => {
    if (finish) {
      const episode = {
        id: jobId, kind: 'episode', title: 'A fictional harbor briefing', topic_label: 'Fictional harbor updates',
        revision_id: 'fixture-stream-r1', script_hash: 'fixture-stream-hash', audio_status: 'recorded',
        duration_minutes: 0.2, duration_basis: 'measured rendered audio',
        audio: { status: 'recorded', revision_id: 'fixture-stream-r1', script_hash: 'fixture-stream-hash',
          url: '/dev/tests/fixtures/audio/episode-tone.wav', duration_seconds: 12 },
        sources: [], chapters: [
          { id: firstChapter.id, title: firstChapter.title, audio_start_seconds: 0, audio_duration_seconds: 4, sentences: [] },
          ...remainingChapters.map(row => ({ id: row.id, title: row.title, audio_start_seconds: row.audio_start_seconds,
            audio_duration_seconds: row.duration_seconds, sentences: [] })),
        ],
      };
      return route.fulfill({ json: { job_id: jobId, topic: 'Fictional harbor updates', length: 5,
        status: 'done', stage: 'done', counts: { chapters: 3, sentences: 0, sources: 0 }, episode,
        streaming: { status: 'complete', expected_chapters: 3, ready_chapters: [firstChapter, ...remainingChapters], progress: 1 } } });
    }
    pollCount += 1;
    return route.fulfill({ json: { job_id: jobId, topic: 'Fictional harbor updates', length: 5,
      status: 'recording', stage: 'recording', counts: { chapters: 3, sentences: 6, sources: 2 },
      ...(streamingEnabled ? { streaming: { status: 'playable', expected_chapters: 3,
        ready_chapters: [firstChapter], progress: 0.333 } } : {}) } });
  });
  await seedInterestProfile(page);
  await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
  await page.locator('.episode-request-page').waitFor();
  await page.locator('#episode-request-prompt').fill('What changed in the fictional harbor?');
  await page.getByRole('button', { name: 'Make episode' }).click();
  await page.locator('.episode-making-page').waitFor();
  return { page, errors, get pollCount() { return pollCount; }, get audioRequests() { return audioRequests; }, finish: () => { finish = true; } };
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const off = await openFlow(browser, false);
    await off.page.locator('.episode-stage-row[data-index="3"][data-state="active"]').waitFor();
    assert.equal(await off.page.locator('.episode-streaming-chapters').count(), 0,
      'the ordinary non-streaming job shows no early chapter controls');
    assert.equal(await off.page.locator('[data-on-demand-badge]').getAttribute('data-state'), 'making',
      'a job without streaming metadata stays marked as making');
    await off.page.close();

    const flow = await openFlow(browser, true);
    const page = flow.page;
    const chapters = page.locator('.episode-streaming-chapters');
    await chapters.waitFor();
    assert.equal(await chapters.locator('.episode-streaming-progress').innerText(), '1 of 3 chapters ready');
    assert.equal(await chapters.getByRole('button', { name: 'Play The harbor update' }).count(), 1,
      'the first playable chapter has a Play control');
    assert.deepEqual(await chapters.locator('.episode-streaming-status').allTextContents(), ['Still being made', 'Still being made'],
      'later chapters say plainly that they are still being made');
    const badge = page.locator('[data-on-demand-badge]');
    assert.equal(await badge.getAttribute('data-state'), 'making', 'the tab badge remains making while chapter one is playable');
    assert.equal(await page.locator('[data-nav="on-demand"]').getAttribute('aria-label'), 'On demand, episode in progress');
    await capture(page, 'episode-streaming-playable-light-390');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'episode-streaming-playable-dark-390');
    await page.emulateMedia({ colorScheme: 'light' });

    await chapters.getByRole('button', { name: 'Play The harbor update' }).click();
    await page.locator('.player-dialog[open]').waitFor();
    await page.waitForFunction(() => document.querySelector('.player-dialog [data-player-title]')?.textContent === 'What changed in the fictional harbor?');
    await page.waitForFunction(() => document.querySelector('.player-dialog [data-player-phase]')?.textContent.includes('The harbor update'));
    assert.ok(flow.audioRequests >= 1, 'playing chapter one requests its profile-scoped chapter audio URL');

    await page.locator('.player-dialog [data-player-close]').click();
    await page.locator('[data-nav="on-demand"]').click();
    await page.locator('.episode-request-page').waitFor();
    assert.equal(await badge.getAttribute('data-state'), 'making', 'the tab badge stays making when the reader returns to the list');
    const makingRow = page.locator('.ask-episode-card').filter({ has: page.getByText('Fictional harbor updates') }).first();
    assert.equal(await makingRow.getAttribute('data-status'), 'making');
    await makingRow.click();
    await page.locator('.episode-making-page').waitFor();

    flow.finish();
    await page.waitForFunction(() => document.querySelector('[data-on-demand-badge]')?.dataset.state === 'ready', null, { timeout: 8000 });
    assert.ok(flow.pollCount >= 1, 'the fake API was polled while chapter one streamed');
    assert.deepEqual(flow.errors, [], 'the streaming player flow has no browser errors');
    await page.close();
  } finally { await browser.close(); }
  console.log('Streaming UI: chapter one plays as soon as ready, later chapters stay marked as making, and the On demand badge switches only at completion.');
})().catch(error => { console.error(error); process.exitCode = 1; });
