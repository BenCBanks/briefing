require('../test_guard.cjs').enforceTestEnvironment();

// Fictional on-demand request states at phone width and enlarged text.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const loopbackHosts = new Set(['127.0.0.1', '::1', 'localhost']);
function assertLoopbackPreview(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('BRIEFING_URL must be a valid loopback URL');
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('BRIEFING_URL protocol must be http or https');
  }
  const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (!loopbackHosts.has(hostname)) {
    throw new Error('BRIEFING_URL hostname must be 127.0.0.1, ::1, or localhost');
  }
  const effectivePort = Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80));
  if (effectivePort === 8898) {
    throw new Error('BRIEFING_URL must not use port 8898');
  }
}
for (const url of ['http://127.0.0.1:8935', 'http://[::1]:8935', 'http://localhost:8935']) {
  assert.doesNotThrow(() => assertLoopbackPreview(url), `Allowed loopback URL: ${url}`);
}
for (const url of ['https://example.invalid', 'http://localhost.example.invalid', 'http://127.0.0.2']) {
  assert.throws(() => assertLoopbackPreview(url), /hostname must be/, `Rejected non-loopback URL: ${url}`);
}
for (const url of ['ftp://localhost:8935', 'file://localhost/tmp/preview', 'http://127.0.0.1:8898', 'https://[::1]:8898', 'http://localhost:8898']) {
  assert.throws(() => assertLoopbackPreview(url), /protocol must be|must not use port 8898/, `Rejected unsafe preview URL: ${url}`);
}
assertLoopbackPreview(baseURL);
const { chromium } = require('playwright');
const generatedDir = path.join(__dirname, 'fixtures/legacy-state/generated');
const edition = JSON.parse(fs.readFileSync(path.join(generatedDir, 'editions.json'), 'utf8')).editions[0];
const sources = JSON.parse(fs.readFileSync(path.join(generatedDir, 'sources.json'), 'utf8')).sources;
const jobIdForPost = postCount => `c0ffee${postCount.toString(16).padStart(26, '0')}`;

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}

async function captureThemes(page, name, anchor) {
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: theme });
    await anchor.scrollIntoViewIfNeeded();
    await capture(page, `${name}-large-text-${theme}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true,
    `${name}: no horizontal overflow at 390 px with enlarged text`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(8000);
    let requestState = 'reject';
    let pollState = 'making';
    let failureReason = 'episode_generation_failed';
    let failureDiagnostic = 'A fictional internal error that should not appear.';
    let postCount = 0;
    const acceptedJobIds = [];
    const pollPaths = [];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const realNow = Date.now.bind(Date);
      let offset = 0;
      window.advanceFixtureClock = ms => { offset += ms; };
      Date.now = () => realNow() + offset;
    });
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE, ok: true } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
    await page.route('**/api/generate**', route => route.fulfill({ status: 503, json: { error: 'fixture_generation_blocked' } }));
    await page.route('**/api/episodes', route => {
      if (route.request().method() !== 'POST') return route.fulfill({ json: { jobs: [], left_today: 3 } });
      postCount += 1;
      if (requestState === 'reject') return route.fulfill({ status: 400, json: { error: 'invalid_request' } });
      if (requestState === 'cost-cap') return route.fulfill({ status: 429, json: { error: 'cost_cap_reached' } });
      const jobId = jobIdForPost(postCount);
      acceptedJobIds.push(jobId);
      return route.fulfill({ status: 202, json: { job_id: jobId, status: 'queued', stage: 'queued', counts: {}, cost_estimate: { label: 'about 12 cents', kind: 'estimate' } } });
    });
    await page.route('**/api/episodes/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith('/cancel')) return route.fulfill({ json: { status: 'cancelled' } });
      pollPaths.push(pathname);
      const jobId = decodeURIComponent(pathname.split('/').at(-1));
      if (pollState === 'failed') return route.fulfill({ json: {
        job_id: jobId, status: 'failed', stage: 'failed', failure_reason: failureReason,
        message: failureDiagnostic, counts: { chapters: 2, sources: 4 },
      } });
      return route.fulfill({ json: {
        job_id: jobId, status: 'writing', stage: 'writing', counts: { chapters: 2, sentences: 6, sources: 4 },
      } });
    });
    await seedInterestProfile(page);
    await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
    await page.locator('.episode-request-page').waitFor();
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    await page.getByText('No on-demand briefings yet. Your first one will appear here.', { exact: true }).waitFor();
    await captureThemes(page, 'on-demand-empty', page.getByText('No on-demand briefings yet. Your first one will appear here.', { exact: true }));

    const prompt = page.locator('#episode-request-prompt');
    await prompt.fill('Fictional harbor transit schedules');
    await page.getByRole('button', { name: 'Create episode' }).click();
    const rejected = page.locator('.episode-request-error');
    await page.getByText('That request needs a little more detail. Edit the topic or question and try again.', { exact: true }).waitFor();
    assert.equal(await page.locator('.episode-making-page').count(), 0, 'A rejected request stays on the request page');
    assert.equal(await prompt.inputValue(), 'Fictional harbor transit schedules', 'The request remains available to edit');
    assert.equal(await page.getByRole('button', { name: 'Create episode' }).isDisabled(), false, 'The request can be tried again');
    assert.equal(await page.locator('.episode-request-start').count(), 1, 'The request page keeps one primary action');
    await page.evaluate(() => window.scrollTo(0, 0));
    await captureThemes(page, 'on-demand-rejected', rejected);

    requestState = 'cost-cap';
    await page.getByRole('button', { name: 'Create episode' }).click();
    const capNotice = page.locator('.episode-request-error');
    await page.getByText('We cannot make this episode right now. Your request is saved here; try again later.', { exact: true }).waitFor();
    assert.equal(await prompt.inputValue(), 'Fictional harbor transit schedules', 'A cost cap keeps the request intact');
    assert.equal(await page.locator('.episode-making-page').count(), 0, 'A cost refusal does not open a making screen');
    assert.equal(await page.locator('#episode-request-start').isDisabled(), true, 'A reached cost limit does not invite an immediate repeat request');
    await page.evaluate(() => window.scrollTo(0, 0));
    await captureThemes(page, 'on-demand-cost-cap', capNotice);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.episode-request-page').waitFor();
    await page.locator('#episode-request-prompt').fill('Fictional harbor transit schedules');
    requestState = 'create'; pollState = 'failed';
    await page.getByRole('button', { name: 'Create episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    const failed = page.locator('.episode-job-error');
    await page.getByRole('button', { name: 'Try again' }).waitFor();
    assert.equal(await failed.innerText(), 'We could not finish this episode this time. You can try again.');
    assert.equal(await page.getByText('A fictional internal error that should not appear.', { exact: true }).count(), 0);
    assert.equal(await page.locator('.episode-stage-list,.episode-live-panel').count(), 0, 'A failed episode shows only its status and next step');
    assert.equal(await page.locator('.episode-retry-button').evaluate(button => button.getBoundingClientRect().height >= 44), true,
      'Retry remains a comfortable touch target at enlarged text');
    await captureThemes(page, 'on-demand-failed', page.locator('.episode-retry-button'));

    requestState = 'create'; pollState = 'making';
    await page.getByRole('button', { name: 'Try again' }).click();
    await page.locator('.episode-making-page').waitFor();
    await page.locator('.episode-stage-row[data-state="active"]').waitFor();
    await page.evaluate(() => window.advanceFixtureClock(180000));
    const longWait = page.locator('.episode-long-wait');
    await longWait.waitFor({ state: 'visible' });
    await page.getByText('Still working', { exact: true }).waitFor();
    assert.equal(await page.locator('.reader-back').innerText(), '‹ On demand', 'A long-running request has a clear way back to its list');
    assert.equal(await page.locator('.episode-making-page').getByText('Fictional internal error', { exact: false }).count(), 0);
    await captureThemes(page, 'on-demand-long-making', longWait);

    for (const failure of [
      {
        reason: 'insufficient_evidence',
        diagnostic: 'Private source diagnostic: fictional full-text coverage is insufficient.',
        publicCopy: 'We could not find enough reliable information to finish this episode. Try a more specific question.',
        kickerCopy: 'Ready to try again',
        retryCount: 1,
      },
      {
        reason: 'cost_cap_reached',
        diagnostic: 'Private cost diagnostic: fictional reservation details must stay hidden.',
        publicCopy: 'We cannot make this episode right now. Your request is saved here; try again later.',
        kickerCopy: 'Try again later',
        retryCount: 0,
      },
    ]) {
      failureReason = failure.reason;
      failureDiagnostic = failure.diagnostic;
      requestState = 'create';
      pollState = 'failed';
      const postsBeforeTerminalFailure = postCount;
      await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
      await page.locator('.episode-request-page').waitFor();
      await page.locator('#episode-request-prompt').fill('Fictional harbor transit schedules');
      const pollsBeforeTerminalFailure = pollPaths.length;
      await page.getByRole('button', { name: 'Create episode' }).click();
      await page.locator('.episode-making-page').waitFor();
      assert.equal(await page.locator('.episode-job-error').getAttribute('role'), 'status',
        'The terminal failure message is exposed as a status');
      assert.equal(await page.locator('.episode-making-kicker').innerText(), failure.kickerCopy,
        'Terminal failure kicker matches the available next step');
      await page.getByText(failure.publicCopy, { exact: true }).waitFor();
      assert.equal(postCount, postsBeforeTerminalFailure + 1, 'Each terminal failure route creates one accepted job');
      const acceptedJobId = acceptedJobIds.at(-1);
      assert.ok(acceptedJobId, 'Each terminal failure starts a distinct accepted job');
      assert.deepEqual(pollPaths.slice(pollsBeforeTerminalFailure), [`/api/episodes/${acceptedJobId}`],
        'Each terminal failure polls only its newly accepted job');
      assert.equal(await page.locator('.episode-making-quote').innerText(), '“Fictional harbor transit schedules”');
      assert.equal(await page.locator('.reader-back').innerText(), '‹ On demand');
      assert.equal(await page.getByRole('button', { name: 'Try again' }).count(), failure.retryCount);
      assert.equal(await page.getByText(failure.reason, { exact: false }).count(), 0, 'Raw terminal reason stays private');
      assert.equal(await page.getByText(failure.diagnostic, { exact: true }).count(), 0, 'Private failure diagnostics stay hidden');
      assert.equal(await page.locator(
        '.episode-done-card,.episode-read-button,.episode-page,.episode-chapter-list,.episode-chapter,' +
        '.episode-transcript,.episode-player,.episode-listen-group,.episode-listen,audio,' +
        '.mini-player,.mini-player-open,.mini-player-toggle,.mini-player-back,.player-dialog,' +
        '.player-queue-row,.player-chapter-row,' +
        '.episode-ready-check,.episode-notification-note,.episode-stage-list,.episode-live-panel,' +
        '.episode-making-actions,.episode-cancel-button'
      ).count(), 0, 'A failed job exposes no fake result, player, ready, activity, progress, or cancel controls');
      assert.equal(await page.locator(
        '.episode-making-page audio[src],.episode-making-page source[src],.episode-making-page video[src],' +
        '.episode-making-page track[src],.episode-making-page a[href*="/api/episodes/"][href*="/audio"],' +
        '.episode-making-page a[href*=".wav"]'
      ).count(), 0, 'A failed job exposes no audio URL or media source');
      assert.equal(await page.locator('#content').getByText(
        /^(Episode ready|Your episode is ready(?: to play)?\.?|Your transcript is ready)$/
      ).count(), 0, 'A failed job exposes no completed-episode success copy');
      if (failure.reason === 'insufficient_evidence') {
        await captureThemes(page, 'on-demand-terminal-insufficient-evidence', page.locator('.episode-job-error'));
      } else if (failure.reason === 'cost_cap_reached') {
        await captureThemes(page, 'on-demand-terminal-cost-cap', page.locator('.episode-job-error'));
      }
    }

    assert.deepEqual(errors, []);
    console.log(`On-demand states: empty, rejected, cost cap, failed and long-running at 390 px enlarged text passed (${postCount} requests).`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
