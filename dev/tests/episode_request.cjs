require('../test_guard.cjs').enforceTestEnvironment();

// Fixture-driven API-backed episode request and making flow. All provider calls are stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const generatedDir = path.join(__dirname, 'fixtures/legacy-state/generated');
const edition = JSON.parse(fs.readFileSync(path.join(generatedDir, 'editions.json'), 'utf8')).editions[0];
const sources = JSON.parse(fs.readFileSync(path.join(generatedDir, 'sources.json'), 'utf8')).sources;
const jobId = 'deedbeef1234567890abcdef12345678';

async function screenshot(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ animations: 'disabled', caret: 'hide', path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(8000);
    const errors = [];
    let createPayload = null;
    let createPostCount = 0;
    let createPostHold = false;
    let createPostStarted = null;
    let releaseCreatePost = null;
    let pollCount = 0;
    let cancelCount = 0;
    let rateLimitMode = false;
    let malformedCreateMode = false;
    let createInternalErrorMode = false;
    let creativeResponseMode = false;
    let episodeFailureMode = false;
    let genericCreateStatus = null;
    let episodeListStatus = 200;
    let episodeListJobs = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/profile', route => route.fulfill({ json: { profile: DEFAULT_PROFILE, ok: true } }));
    await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
    await page.route('**/api/generate**', route => route.fulfill({ status: 503, json: { error: 'fixture_generation_blocked' } }));
    await page.route('**/api/episodes', async route => {
      if (route.request().method() !== 'POST') return episodeListStatus === 404
        ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
        : route.fulfill({ json: { jobs: episodeListJobs, left_today: 2 } });
      createPostCount += 1;
      createPayload = route.request().postDataJSON(); pollCount = 0;
      if (createPostHold) {
        createPostStarted?.();
        await new Promise(resolve => { releaseCreatePost = resolve; });
      }
      if (rateLimitMode) return route.fulfill({ status: 429, json: { error: 'rate_limited' } });
      if (malformedCreateMode) return route.fulfill({ status: 202, json: { status: 'queued' } });
      if (createInternalErrorMode) return route.fulfill({ status: 500, json: {
        error: 'internal_error', detail: 'FIXTURE_INTERNAL_STACK: fictional provider token and private path',
      } });
      if (creativeResponseMode) return route.fulfill({ status: 422, json: {
        error: 'unsupported_format', format: 'poetry',
        diagnostics: { prompt: 'FIXTURE_PRIVATE_CREATIVE_PROMPT', trace: 'fictional internal validator path' },
      } });
      if (genericCreateStatus !== null) return route.fulfill({ status: genericCreateStatus, json: {
        error: 'invalid_request',
        diagnostics: { prompt: 'FIXTURE_PRIVATE_GENERIC_PROMPT', trace: 'fictional generic error detail' },
      } });
      return route.fulfill({ status: 202, json: { job_id: jobId, status: 'queued', stage: 'queued', counts: {}, left_today: 2, cost_estimate: { label: 'about 12 cents', kind: 'estimate' } } });
    });
    await page.route('**/api/episodes/**', route => {
      const requestPath = new URL(route.request().url()).pathname;
      if (requestPath.endsWith('/cancel')) { cancelCount += 1; return route.fulfill({ json: { status: 'cancelled' } }); }
      if (episodeFailureMode) return route.fulfill({ json: {
        job_id: jobId, topic: createPayload.topic, length: createPayload.length,
        status: 'failed', stage: 'failed', failure_reason: 'insufficient_evidence', counts: {}, episode: null,
      } });
      const stages = [
        ['researching', { chapters: 0, sentences: 0, sources: 1 }],
        ['reading', { chapters: 1, sentences: 2, sources: 8, sources_found: 8, sources_read: 6 }],
        ['checking', { chapters: 2, sentences: 4, sources: 2 }],
        ['writing', { chapters: 3, sentences: 6, sources: 2 }],
        ['recording', { chapters: 3, sentences: 6, sources: 2 }],
      ];
      const index = Math.min(pollCount++, stages.length - 1);
      const done = pollCount > stages.length;
      const episode = done ? {
        id: jobId, topic: createPayload.topic, topic_label: createPayload.topic, asked: createPayload.topic,
        title: `Fictional briefing on ${createPayload.topic}`, style: createPayload.style,
        duration_minutes: createPayload.length, made_date: '2026-11-18', audio_recorded: true, audio_status: 'recorded',
        revision_id: 'fixture-request-created-r1', script_hash: 'fixture-request-created-script-r1',
        audio: { state: 'recorded', status: 'recorded', revision_id: 'fixture-request-created-r1',
          script_hash: 'fixture-request-created-script-r1', url: '/dev/tests/fixtures/audio/episode-tone.wav', duration_seconds: 4 },
        shorter_than_requested: true, shortfall_notice: 'The verified script is shorter than requested.',
        sources: [
          { id: 'fiction-source-1', outlet: 'Civic Ledger · fictional', date: '18 Nov', url: 'https://civic.example.test/story' },
          { id: 'fiction-source-2', outlet: 'Open Systems Review · fictional', date: '18 Nov', url: 'https://open-systems.example.test/story' },
        ],
        chapters: ['The short answer', 'What sources show', 'What to remember'].map((title, n) => ({
          id: `chapter-${n + 1}`, title,
          sentences: [{ text: 'This is a fictional claim checked against fictional sources.', source_ids: ['fiction-source-1'], source_numbers: [1] }],
        })),
      } : null;
      return route.fulfill({ json: { job_id: jobId, topic: createPayload.topic, length: createPayload.length,
        status: done ? 'done' : stages[index][0], stage: done ? 'done' : stages[index][0],
        counts: done ? { chapters: 3, sentences: 6, sources: 2 } : stages[index][1],
        shorter_than_requested: done, shortfall_notice: done ? 'The verified script is shorter than requested.' : undefined, episode } });
    });
    await seedInterestProfile(page);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-page').waitFor();
    await page.getByRole('link', { name: /Brief me on something else/ }).click();
    await page.locator('.episode-request-page').waitFor();
    assert.match(await page.locator('.ask-page-intro').innerText(), /transcript and audio when recording is ready/);
    assert.equal(await page.locator('[aria-current=\"page\"][data-nav=\"on-demand\"]').count(), 1, 'On demand remains selected while making an episode');
    await page.getByRole('heading', { name: 'Brief me on anything' }).waitFor();
    assert.equal(await page.locator('.ask-back').count(), 0, 'On demand is a tab root without a Today back link');
    assert.equal(await page.locator('.ask-page-intro').innerText(), 'Any topic, any time. You choose the length. Your episode includes a transcript and audio when recording is ready.');
    assert.equal(new URL(page.url()).hash, '#ask-episode');
    assert.equal(await page.locator('.ask-prompt-box textarea').isDisabled(), false);
    assert.equal(await page.locator('#episode-request-prompt').getAttribute('placeholder'), 'e.g. U.S. payments regulation this month');
    assert.deepEqual(await page.locator('.on-demand-length-group .episode-request-chip').allTextContents(), ['3 min', '5 min', '10 min', '15 min']);
    assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), '5 min');
    assert.equal(await page.locator('.on-demand-cost-line').count(), 0, 'the request page hides internal cost wording');
    assert.equal(await page.getByRole('button', { name: 'Make episode' }).isDisabled(), true, 'Start is disabled until the prompt has text');
    assert.equal(await page.getByRole('button', { name: 'Make episode' }).evaluate(button => getComputedStyle(button).borderTopWidth), '1px', 'disabled Start has a distinct quiet outline');
    const prompt = page.locator('#episode-request-prompt');
    const promptLimit = page.locator('.episode-request-character-count');
    assert.equal(await prompt.getAttribute('aria-describedby'), null, 'the prompt does not announce a hidden character limit');
    assert.equal(await promptLimit.isVisible(), false, 'the prompt limit stays hidden below the limit');
    assert.equal(await page.getByText('No on-demand briefings yet. Your first one will appear here.', { exact: true }).count(), 1);
    assert.equal(await page.locator('.episode-request-example').evaluateAll(items => items.every(item => getComputedStyle(item).textAlign === 'left' && item.getBoundingClientRect().width === items[0].getBoundingClientRect().width)), true, 'Examples share an aligned full-width style');
    await screenshot(page, 'on-demand-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'on-demand-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await prompt.fill('A draft to replace');
    await page.locator('.episode-request-example').first().click();
    assert.equal(await prompt.inputValue(), 'How do contactless payments settle?', 'an example replaces the draft and fills the prompt');
    assert.equal(await page.getByRole('button', { name: 'Make episode' }).isDisabled(), false, 'choosing an example enables Start');
    assert.equal(await promptLimit.isVisible(), false, 'examples below the cap do not show a counter');
    const longQuestion = 'What practical changes follow if fictional harbor towns adopt a shared payment network, and how would businesses, customers, and local services notice the difference?';
    await prompt.fill(longQuestion);
    const longQuestionLayout = await prompt.evaluate(el => ({
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
      scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
      textOverflow: getComputedStyle(el).textOverflow, wrap: el.wrap || 'soft',
    }));
    assert.ok(longQuestionLayout.scrollHeight > longQuestionLayout.clientHeight,
      `The long question has additional scrollable lines at 390px: ${JSON.stringify(longQuestionLayout)}`);
    assert.ok(longQuestionLayout.scrollWidth <= longQuestionLayout.clientWidth,
      `The long question stays within the prompt width: ${JSON.stringify(longQuestionLayout)}`);
    assert.equal(longQuestionLayout.wrap, 'soft', 'The prompt wraps question text');
    assert.notEqual(longQuestionLayout.textOverflow, 'ellipsis', 'The long question is not styled with ellipsis');
    await prompt.fill('');
    assert.equal(await page.getByRole('button', { name: 'Make episode' }).isDisabled(), true, 'clearing the prompt disables Start again');
    await prompt.fill('x'.repeat(500));
    assert.equal(await prompt.inputValue(), 'x'.repeat(500), 'the prompt accepts up to its 500-character limit');
    assert.ok(await prompt.evaluate(el => el.getBoundingClientRect().height <= 144), 'the prompt scrolls internally instead of growing past its visual limit');
    assert.equal(await promptLimit.innerText(), '500-character limit reached', 'the limit message appears when the prompt reaches its cap');
    assert.equal(await promptLimit.evaluate(el => el.classList.contains('is-at-limit')), true, 'the limit message has a clear visual state');
    await promptLimit.scrollIntoViewIfNeeded();
    await screenshot(page, 'on-demand-limit-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'on-demand-limit-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await prompt.fill('');
    episodeListStatus = 404;
    await page.locator('[data-nav="today"]').click();
    await page.locator('.today-page').waitFor();
    await page.getByRole('link', { name: /Brief me on something else/ }).click();
    await page.locator('.episode-request-page').waitFor();
    await page.getByText('No on-demand briefings yet. Your first one will appear here.', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Try again' }).count(), 0, 'A missing list is empty, not a load failure');
    episodeListStatus = 200;
    await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '10 min' }).click();
    assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), '10 min');
    assert.equal(await page.locator('.episode-request-example').count(), 3);
    assert.equal(await page.locator('.ask-episodes h2').innerText(), 'Your on-demand briefings');
    assert.equal(await page.locator('.episode-request-narrator').count(), 0);
    assert.equal(await page.getByText('Preview only', { exact: true }).count(), 0);
    episodeListJobs = [
      { job_id: '11111111111111111111111111111111', status: 'researching', topic: 'Fictional water quality monitoring', length: 5 },
      { job_id: '22222222222222222222222222222222', status: 'done', topic: 'Fictional payment settlement systems', length: 3, episode: {
        title: 'How payment settlement works', revision_id: 'fixture-request-ready-r1', script_hash: 'fixture-request-ready-script-r1',
        audio: { state: 'recorded', revision_id: 'fixture-request-ready-r1', script_hash: 'fixture-request-ready-script-r1', url: '/dev/tests/fixtures/audio/episode-tone.wav' },
      } },
    ];
    await page.locator('[data-nav="today"]').click();
    await page.locator('.today-page').waitFor();
    await page.locator('[data-nav="on-demand"]').click();
    await page.locator('.episode-request-page').waitFor();
    await page.getByText('How payment settlement works', { exact: true }).waitFor();
    const demandRows = page.locator('.ask-episode-card');
    assert.equal(await demandRows.count(), 2, 'making and ready requests appear in the On demand list');
    assert.equal(await demandRows.nth(0).getAttribute('href'), '#episode-making/11111111111111111111111111111111');
    assert.equal(await demandRows.nth(0).locator('h3').innerText(), 'Fictional water quality monitoring');
    assert.equal(await demandRows.nth(0).locator('p').innerText(), 'Requested: 5 min');
    assert.equal(await demandRows.nth(0).locator('.episode-status-chip').innerText(), 'Making');
    assert.equal(await demandRows.nth(1).getAttribute('href'), '#episode/22222222222222222222222222222222');
    assert.equal(await demandRows.nth(1).locator('h3').innerText(), 'How payment settlement works');
    assert.equal(await demandRows.nth(1).locator('p').innerText(), 'Requested: 3 min');
    assert.equal(await demandRows.nth(1).locator('.episode-status-chip').innerText(), 'Ready · Listen');
    assert.equal(await demandRows.evaluateAll(items => items.every(item => getComputedStyle(item).display === 'grid' && item.getBoundingClientRect().width >= 340)), true, 'On demand requests display as full-width rows');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(80);
    await screenshot(page, 'on-demand-list-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'on-demand-list-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    episodeListJobs = [];
    await page.locator('#episode-request-prompt').fill('AI');
    assert.equal(await page.getByRole('button', { name: 'Make episode' }).isDisabled(), false, 'Start enables once the prompt has text');
    const suggestions = page.locator('.episode-topic-suggestion-chip');
    assert.equal(await suggestions.count(), 3, 'Broad prompts offer three narrower topic chips');
    assert.deepEqual(await suggestions.allTextContents(), ['AI in everyday life', 'Rules and policy for AI', 'Research and evidence about AI']);
    await screenshot(page, 'episode-broad-topic-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-broad-topic-dark'); await page.emulateMedia({ colorScheme: 'light' });
    await suggestions.first().click();
    assert.equal(await page.locator('#episode-request-prompt').inputValue(), 'AI in everyday life', 'Choosing a chip narrows the prompt without starting a job');
    assert.equal(await suggestions.count(), 0);
    await page.locator('#episode-request-prompt').fill('Why do contactless payments settle in batches?');
    await page.getByRole('button', { name: 'Make episode' }).click();
    assert.equal(await page.locator('.episode-style-dialog').count(), 0, 'Single send uses the default narrative style');
    await page.locator('.episode-making-page').waitFor();
    assert.equal(await page.locator('.episode-making-quote').innerText(), '“Why do contactless payments settle in batches?”');
    assert.equal(await page.locator('.episode-making-meta').innerText(), 'Episode length: 10 minutes');
    assert.equal(await page.locator('.episode-progress-note').count(), 1, 'making shows one plain progress note');
    assert.doesNotMatch(await page.locator('.episode-making-page').innerText(), /Work so far|Cost estimate/);
    assert.equal(await page.locator('.episode-stage-row').count(), 5);
    assert.equal(await page.locator('.episode-stage-row[data-state="active"]').count(), 1);
    assert.ok(await page.locator('[role="status"]').count() >= 1);
    assert.deepEqual(createPayload, { topic: 'Why do contactless payments settle in batches?', length: 10, style: 'news-brief' });
    await page.getByRole('button', { name: 'Cancel this episode' }).click();
    await page.locator('.episode-request-page').waitFor();
    assert.equal(cancelCount, 1, 'cancel request reaches the API');

    rateLimitMode = true;
    await page.locator('#episode-request-prompt').fill('A fictional rate limit topic');
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.getByText('Please wait a moment, then try again.', { exact: true }).waitFor();
    assert.equal(await page.locator('.episode-making-page').count(), 0, '429 keeps the reader on the request screen');
    const requestError = page.locator('.episode-request-error');
    assert.equal(await requestError.getAttribute('role'), 'status', 'the request failure is announced as a status message');
    assert.equal(await requestError.innerText(), 'Please wait a moment, then try again.', 'the request failure message remains readable');
    await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
    let promptKeyboardReachable = false;
    for (let index = 0; index < 48; index++) {
      await page.keyboard.press('Tab');
      if (await prompt.evaluate(element => element === document.activeElement)) { promptKeyboardReachable = true; break; }
    }
    assert.equal(promptKeyboardReachable, true, 'the request input remains reachable by keyboard after failure');
    assert.equal(await prompt.evaluate(element => element.matches(':focus-visible')), true, 'the failed request input shows keyboard focus');
    assert.equal(await prompt.evaluate(element => getComputedStyle(element).outlineStyle), 'solid', 'the failed request input has a visible focus indicator');
    rateLimitMode = false;
    malformedCreateMode = true;
    const malformedRequest = '  What would a fictional transit schedule change affect?  ';
    await page.locator('#episode-request-prompt').fill(malformedRequest);
    await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '15 min' }).click();
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.getByText('Your episode could not be started. Please try again.', { exact: true }).waitFor();
    assert.equal(await page.locator('#episode-request-prompt').inputValue(), malformedRequest, 'a malformed successful response preserves the exact question');
    assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), '15 min', 'a malformed successful response preserves the chosen length');
    assert.equal(await page.locator('.episode-making-page').count(), 0, 'missing job identity does not invent a making episode');
    assert.equal(await page.locator('.ask-episode-card').count(), 0, 'missing job identity does not invent a ready episode card');
    assert.equal(pollCount, 0, 'a malformed 202 without job identity never starts a status poll');
    assert.equal(new URL(page.url()).hash, '#ask-episode', 'malformed success keeps the reader on the request route');
    assert.equal(await page.locator('.episode-request-start').isDisabled(), false, 'malformed success clears busy state for deliberate retry');
    assert.deepEqual(createPayload, { topic: malformedRequest, length: 15, style: 'news-brief' });
    malformedCreateMode = false;
    createInternalErrorMode = true;
    await page.locator('#episode-request-prompt').fill('A fictional request that reaches an internal failure');
    await page.getByRole('button', { name: 'Make episode' }).click();
    const createFailure = page.locator('.episode-request-error');
    await page.getByText('Your episode could not be started. Please try again.', { exact: true }).waitFor();
    assert.equal(await createFailure.innerText(), 'Your episode could not be started. Please try again.', 'a failed create response uses the generic recovery message');
    assert.doesNotMatch(await page.locator('main').innerText(), /FIXTURE_INTERNAL_STACK|fictional provider token|private path/i,
      'internal create-response details never reach the reader');
    assert.equal(await page.locator('.episode-making-page').count(), 0, 'a failed create response does not invent a making state');
    createInternalErrorMode = false;
    await page.locator('#episode-request-prompt').fill('Why do contactless payments settle in batches?');

    for (const minutes of [3, 15]) {
      await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: `${minutes} min` }).click();
      assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), `${minutes} min`);
      await page.getByRole('button', { name: 'Make episode' }).click();
      await page.locator('.episode-making-page').waitFor();
      assert.equal(createPayload.length, minutes);
      assert.equal(await page.locator('.episode-making-meta').innerText(), `Episode length: ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`);
      await page.getByRole('button', { name: 'Cancel this episode' }).click();
      await page.locator('.episode-request-page').waitFor();
    }
    assert.equal(cancelCount, 3);
    await page.locator('#episode-request-prompt').fill('A fictional request submitted twice quickly');
    await page.getByRole('button', { name: '5 min', exact: true }).click();
    const postsBeforeDoubleSubmit = createPostCount;
    createPostHold = true;
    let signalCreatePostStarted;
    const createPostRequestStarted = new Promise(resolve => { signalCreatePostStarted = resolve; });
    createPostStarted = signalCreatePostStarted;
    await page.getByRole('button', { name: 'Make episode' }).click();
    await createPostRequestStarted;
    assert.equal(await page.locator('.episode-request-start').isDisabled(), true, 'the request action is disabled while the first submission is pending');
    await page.locator('.episode-request-start').dispatchEvent('click');
    await page.waitForTimeout(100);
    assert.equal(createPostCount, postsBeforeDoubleSubmit + 1, 'a second click while pending does not create a duplicate request');
    createPostHold = false;
    releaseCreatePost();
    await page.locator('.episode-making-page').waitFor();
    assert.deepEqual(createPayload, { topic: 'A fictional request submitted twice quickly', length: 5, style: 'news-brief' });
    await page.getByRole('button', { name: 'Cancel this episode' }).click();
    await page.locator('.episode-request-page').waitFor();
    creativeResponseMode = true;
    const creativeRequest = 'Write a short fictional poem about a bus route';
    await page.locator('#episode-request-prompt').fill(creativeRequest);
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.waitForFunction(() => {
      const start = document.querySelector('.episode-request-start');
      return start && !start.disabled;
    });
    const creativeResponse = page.locator('.episode-request-error');
    await creativeResponse.waitFor({ state: 'visible' });
    const unsupportedMessage = (await creativeResponse.innerText()).trim();
    assert.equal(unsupportedMessage, 'That kind of episode is not supported yet. Try a factual question.', 'a creative request receives an honest unsupported state');
    assert.equal(await page.locator('#episode-request-prompt').inputValue(), creativeRequest, 'a creative-format response preserves the reader request');
    assert.equal(await page.locator('.episode-making-page').count(), 0, 'an unsupported creative response does not invent a making job');
    assert.equal(await page.locator('.episode-done-card, .episode-waveform, a[href^="#episode/"], a[href^="#episode-making/"]').count(), 0,
      'an unsupported creative response creates no fictional success card, player or episode link');
    assert.equal(await page.locator('.episode-request-start').isDisabled(), false, 'the request remains editable after an unsupported creative response');
    assert.doesNotMatch(await page.locator('main').innerText(), /unsupported_format|FIXTURE_PRIVATE_CREATIVE_PROMPT|fictional internal validator path/i,
      'creative validation diagnostics are not exposed to the reader');
    assert.deepEqual(createPayload, { topic: creativeRequest, length: 5, style: 'news-brief' });
    await page.evaluate(() => window.scrollTo(0, 0));
    await screenshot(page, 'unsupported-format-390-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await screenshot(page, 'unsupported-format-390-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    creativeResponseMode = false;
    for (const { status, length, question } of [
      { status: 400, length: 15, question: 'A fictional factual request with a generic 400 response' },
      { status: 422, length: 3, question: 'A fictional factual request with a different generic 422 response' },
    ]) {
      genericCreateStatus = status;
      await page.locator('#episode-request-prompt').fill(question);
      await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: `${length} min` }).click();
      await page.getByRole('button', { name: 'Make episode' }).click();
      const genericResponse = page.locator('.episode-request-error');
      await page.getByText('That request needs a little more detail. Edit the topic or question and try again.', { exact: true }).waitFor();
      assert.equal(await genericResponse.innerText(), 'That request needs a little more detail. Edit the topic or question and try again.', `${status} keeps the generic request message`);
      assert.equal(await page.locator('#episode-request-prompt').inputValue(), question, `${status} retains the exact question`);
      assert.equal(await page.locator('#episode-request-prompt').isDisabled(), false, `${status} leaves the question editable`);
      assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), `${length} min`, `${status} retains the selected length`);
      assert.equal(await page.locator('.episode-request-start').isDisabled(), false, `${status} leaves Start available`);
      assert.equal(await page.locator('.episode-making-page, .episode-done-card, .episode-waveform, a[href^="#episode/"], a[href^="#episode-making/"]').count(), 0,
        `${status} creates no job, player, card, or episode link`);
      assert.doesNotMatch(await page.locator('main').innerText(), /invalid_request|FIXTURE_PRIVATE_GENERIC_PROMPT|fictional generic error detail/i,
        `${status} does not expose response diagnostics`);
      assert.deepEqual(createPayload, { topic: question, length, style: 'news-brief' });
    }
    genericCreateStatus = null;
    await page.locator('#episode-request-prompt').fill('Why do contactless payments settle in batches?');
    await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '10 min' }).click();
    await page.evaluate(() => {
      window.stageAnnouncements = [];
      const watch = () => {
        const region = document.querySelector('.episode-stage-announcement');
        if (!region || window.stageRegionObserved) return;
        window.stageRegionObserved = true;
        window.stageAnnouncements.push(region.textContent || '');
        new MutationObserver(() => window.stageAnnouncements.push(region.textContent || ''))
          .observe(region, { childList: true, characterData: true, subtree: true });
      };
      new MutationObserver(watch).observe(document.body, { childList: true, subtree: true });
      watch();
    });
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    assert.deepEqual(createPayload, { topic: 'Why do contactless payments settle in batches?', length: 10, style: 'news-brief' });
    await page.locator('.episode-stage-row[data-index="1"] strong').filter({ hasText: 'Reading' }).waitFor();
    await page.locator('.episode-stage-row[data-index="1"] .episode-stage-status').filter({ hasText: '6 sources read' }).waitFor();
    assert.deepEqual(await page.locator('.episode-stage-row .episode-stage-copy strong').allTextContents(), [
      'Finding sources', 'Reading', 'Writing', 'Recording', 'Ready',
    ], 'The progress list uses five plain-language stages, including Ready');
    await screenshot(page, 'episode-making-active-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-making-active-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.equal(await page.locator('.episode-making-meta').innerText(), 'Episode length: 10 minutes');
    assert.equal(await page.locator('.episode-ready-check,.episode-notification-note').count(), 0, 'the making state shows no inert notification control or message');
    await page.locator('.episode-progress-note').waitFor();
    await page.locator('.episode-done-card').waitFor({ timeout: 15000 });
    assert.equal(await page.locator('.episode-cancel-button').count(), 0, 'A finished job no longer offers cancellation');
    assert.equal(await page.locator('.episode-making-kicker').innerText(), 'Episode ready', 'The making screen updates its status label when done');
    assert.equal(await page.locator('.episode-done-meta').innerText(), 'Episode length: 10 minutes');
    assert.equal(await page.locator('.episode-stage-row[data-state="done"]').count(), 5);
    assert.equal(await page.locator('.episode-stage-ring[data-check="✓"]').count(), 5);
    const announcements = await page.evaluate(() => window.stageAnnouncements);
    for (const stage of ['Finding sources', 'Reading', 'Writing', 'Recording']) {
      assert.equal(announcements.filter(message => message.startsWith(stage)).length, 1, `${stage} is announced once`);
    }
    assert.equal(announcements.filter(message => message === 'Your episode is ready.').length, 1, 'Ready is announced once');
    assert.equal(await page.getByText('We found limited coverage; here is a shorter episode.', { exact: true }).count(), 1, 'Thin evidence is disclosed plainly without padding language');
    assert.equal(await page.locator('.episode-progress-note').count(), 1);
    assert.doesNotMatch(await page.locator('.episode-done-card').innerText(), /notifications are not available|tell me when it.s ready/i);
    await page.getByText('We found limited coverage; here is a shorter episode.', { exact: true }).scrollIntoViewIfNeeded();
    await screenshot(page, 'episode-thin-evidence-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-thin-evidence-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await screenshot(page, 'episode-making-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-making-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.episode-stage-ring').first().evaluate(el => getComputedStyle(el).animationName), 'none');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal overflow at 390 px');
    await page.getByRole('button', { name: 'Open episode' }).click();
    await page.locator('.episode-page h1').waitFor();
    assert.match(await page.locator('.episode-page h1').innerText(), /Fictional briefing on Why do contactless payments settle in batches/);
    assert.equal(await page.locator('.episode-page .episode-listen').isEnabled(), true, 'the ready episode offers its recorded player');
    const citations = page.locator('.episode-citation summary');
    assert.equal(await citations.count(), 3, 'each chapter lists its source once');
    assert.equal(await page.locator('.episode-inline-source-marker').count(), 3, 'each chapter paragraph has at most one small source marker');
    assert.equal(await page.locator('.episode-page-compact-transcript .episode-sentence').count(), 3, 'on-demand transcript groups prose by chapter');
    assert.equal(await page.locator('.episode-chapter-sources').count(), 3, 'sources are listed once under each chapter');
    assert.deepEqual(await citations.evaluateAll(items => items.map(item => item.getAttribute('aria-label'))), [
      'Source 1, Civic Ledger · fictional',
      'Source 1, Civic Ledger · fictional',
      'Source 1, Civic Ledger · fictional',
    ]);
    await page.locator('.episode-transcript').scrollIntoViewIfNeeded();
    await screenshot(page, 'episode-ready-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-ready-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.episode-chapter-disclosure').first().evaluate(details => { details.open = true; });
    await page.locator('.episode-citation').first().locator('summary').click();
    assert.equal(await page.locator('.episode-citation-detail strong').first().innerText(), 'Civic Ledger · fictional', 'the ready transcript source list opens its details');
    await page.locator('.episode-citation[open]').first().locator('summary').click();
    assert.equal(await page.getByRole('button', { name: `Play episode: Fictional briefing on Why do contactless payments settle in batches?` }).isEnabled(), true,
      'the requested episode is playable when its recording matches the ready transcript');
    await page.getByRole('button', { name: `Play episode: Fictional briefing on Why do contactless payments settle in batches?` }).click();
    const requestedPlayer = page.getByRole('dialog', { name: 'Fictional briefing on Why do contactless payments settle in batches?' });
    await requestedPlayer.locator('[data-player-phase]').filter({ hasText: /Playing/ }).waitFor();
    assert.ok(['The short answer', 'What sources show', 'What to remember'].includes(await requestedPlayer.locator('[data-player-now-title]').innerText()),
      'the player starts a chapter from the newly requested episode');
    await screenshot(page, 'episode-play-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-play-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await requestedPlayer.getByRole('button', { name: 'Pause episode audio' }).click();
    await requestedPlayer.locator('[data-player-phase]').filter({ hasText: /Paused/ }).waitFor({ timeout: 15000 });
    await page.keyboard.press('Escape');

    episodeFailureMode = true;
    await page.evaluate(() => { location.hash = '#ask-episode'; });
    await page.locator('.episode-request-page').waitFor();
    await page.locator('#episode-request-prompt').fill('A fictional question that cannot be verified');
    await page.getByRole('button', { name: 'Make episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    await page.getByText('We could not find enough reliable information to finish this episode. Try a more specific question.', { exact: true }).waitFor();
    assert.equal(await page.locator('.episode-job-error').evaluate(el => getComputedStyle(el).borderLeftWidth), '0px', 'failure copy is plain text');
    assert.equal(await page.getByRole('button', { name: 'Try again' }).count(), 1, 'An evidence failure offers a clear retry');
    await screenshot(page, 'episode-failed-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'episode-failed-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    assert.deepEqual(errors, []);
    console.log('Episode request: ask-to-play flow, evidence failure recovery, four lengths, plain progress, source lists, and 390px light/dark screenshots passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
