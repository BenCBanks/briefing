require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8935';
const editions = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')).editions;
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const sourceEdition = editions.find(item => item.kind === 'daily');
const generatedEdition = { ...sourceEdition, id: 'generated-payments-test', revision_id: 'generated-r1', script_hash: 'fictional-test-hash',
  generated: true, local_date: '2026-11-17', generated_at: '2026-11-17T06:02:00-05:00', title: 'A generated briefing for payments',
  coverage: [{ topic_id: 'payments', state: 'covered' }],
  sections: sourceEdition.sections.filter(section => section.topic_ids.includes('payments')) };
const noStoriesEdition = { ...generatedEdition, id: 'generated-local-planning-test', revision_id: 'generated-local-planning-r1',
  title: 'A generated briefing for local planning', coverage: [{ topic_id: 'custom-local-planning', label: 'Local planning', state: 'quiet', reason: 'No matching story passed checks.' }],
  sections: sourceEdition.sections.map(section => ({ ...section, topic_ids: ['custom-local-planning'] })) };
const editionResponse = { generated: true, edition: generatedEdition, sources };
const noStoriesEditionResponse = { generated: true, edition: noStoriesEdition, sources };
const generalEdition = structuredClone(sourceEdition);
generalEdition.id = 'generated-general-test'; generalEdition.revision_id = 'generated-general-r1'; generalEdition.script_hash = 'fictional-general-hash';
generalEdition.generated = true; generalEdition.local_date = '2026-11-17'; generalEdition.generated_at = '2026-11-17T06:02:00-05:00'; generalEdition.title = 'Today’s general briefing';
const generalEditionResponse = { generated: true, edition: generalEdition, sources };
const interpretedProfile = { version: 1, words: 'Follow payments and real-time payment rules every day.', minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Real-time payment rules', include: ['real-time payment rules'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'real-time payment rules every day' },
] };
const fallbackProfile = { version: 1, words: 'Follow savings rates and payments.', minutes: 12, background: null, muted: [], topics: [
  { id: 'banking', label: 'Banking', scope: 'Savings rates', include: ['savings rates'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'savings rates' },
  { id: 'payments', label: 'Payments', scope: 'Payment rules', include: ['payments'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' },
  { id: 'crypto', label: 'Crypto', scope: 'Crypto', include: [], exclude: ['crypto'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'skip crypto' },
] };
const unmatchedProfile = { version: 1, words: 'Follow local planning and transit.', minutes: 12, background: null, muted: [], topics: [
  { id: 'custom-local-planning', label: 'Local planning', scope: 'Planning and transit', include: ['local planning and transit'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'local planning and transit' },
] };

async function flow({ fail = false, noStories = false, missingEndpoints = false, fallbackUnavailable = false, longBuild = false, goTodayWhileBuilding = false } = {}) {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(6000);
    if (longBuild || goTodayWhileBuilding) await page.addInitScript(({ longBuild: long, goToday }) => {
      const nativeSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => nativeSetTimeout(callback,
        (long && delay === 1000) || (goToday && delay === 20000) ? 0 : delay, ...args);
    }, { longBuild, goToday: goTodayWhileBuilding });
    const profile = noStories ? unmatchedProfile : missingEndpoints ? fallbackProfile : interpretedProfile;
    let profilePost = null; let generatePost = null; let polls = 0;
    await page.route('**/api/edition**', route => fallbackUnavailable
      ? route.fulfill({ status: 404, json: { error: 'not_ready' } })
      : route.fulfill({ json: missingEndpoints || fail ? generalEditionResponse : noStories ? noStoriesEditionResponse : editionResponse }));
    await page.route('**/api/interpret', route => route.fulfill({ json: { profile, question: null } }));
    await page.route('**/api/profile', async route => {
      if (route.request().method() === 'GET') return route.fulfill({ status: 404, json: { error: 'profile_not_found' } });
      profilePost = route.request().postDataJSON();
      await route.fulfill(missingEndpoints ? { status: 404, json: { error: 'not_available' } } : { json: { ok: true } });
    });
    await page.route('**/api/generate', async route => {
      if (missingEndpoints) return route.fulfill({ status: 404, json: { error: 'not_available' } });
      generatePost = route.request().postDataJSON(); await route.fulfill({ json: { job_id: 'job-test-1', status: 'queued' } });
    });
    await page.route('**/api/generate/job-test-1', async route => {
      polls += 1;
      if (goTodayWhileBuilding && polls === 1) await new Promise(resolve => setTimeout(resolve, 700));
      return route.fulfill({ json: fail ? { status: 'failed' } : longBuild && polls === 1
        ? { status: 'running', progress: { stage: 'fetching', completed: 1, total: 126 } }
        : longBuild && polls <= 126
          ? { status: 'running', progress: { stage: 'writing', completed: polls, total: 126 } }
        : polls === 1
        ? { status: 'running', progress: { stage: 'fetching', completed: 1, total: 10, message: 'Fetching 1 of 10 feeds' } }
        : polls === 2 ? { status: 'running', progress: { stage: 'writing', completed: 3, total: 6, message: 'Writing story candidate 3 of 6' } }
          : { status: 'complete', progress: { stage: 'scoring', completed: 1, total: 1 }, edition_ready: { edition: noStories ? noStoriesEdition : generatedEdition } } });
    });
    await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'networkidle' });
    const inputWords = noStories ? unmatchedProfile.words : missingEndpoints ? fallbackProfile.words : interpretedProfile.words;
    await page.locator('#interest-words').fill(inputWords);
    await page.getByRole('button', { name: 'Build my briefing' }).click();
    if (!fail && !missingEndpoints && !goTodayWhileBuilding) {
      await page.locator('.build-interest-chip').first().waitFor();
      if (!noStories) assert.equal(await page.locator('.build-interest-chip').first().locator(':scope > span').innerText(), 'Real-time payment rules', 'Build chip keeps the reader’s specific source phrase');
      if (longBuild) {
        await page.getByText('Finding today’s stories', { exact: true }).waitFor();
        await page.getByText('Writing your briefing', { exact: true }).waitFor();
      } else {
        await page.locator('.build-progress-step[aria-current=step]').filter({ hasText: 'Finding today’s stories' }).waitFor();
        await page.locator('.build-progress-step[aria-current=step]').filter({ hasText: 'Writing story 3 of 6' }).waitFor();
      }
    }
    if (goTodayWhileBuilding) {
      await page.locator('.build-still-working').waitFor();
      await page.getByRole('button', { name: 'Go to Today' }).click();
      await page.locator('.today-page').waitFor();
      assert.equal(await page.locator('.today-date').innerText(), 'Tuesday 17 November',
        'Today keeps the available edition’s actual date while the new one is being written');
      assert.equal(await page.locator('.today-previous-edition-note').count(), 1,
        'The available edition is followed by a quiet update status');
      assert.equal(await page.locator('.today-build-progress').count(), 0,
        'A preparation panel does not replace an available edition');
    }
    if (fail || missingEndpoints) {
      if (fallbackUnavailable) {
        await page.getByRole('heading', { name: 'Today’s stories did not load' }).waitFor();
        assert.equal(await page.locator('.today-general-fallback-note').count(), 0, 'Unavailable general edition does not claim a fallback');
        assert.equal(await page.getByRole('button', { name: 'Retry' }).count(), 1);
      } else {
        await page.locator('.today-general-fallback-note').waitFor();
        assert.equal(await page.locator('.today-general-fallback-note').innerText(), 'Your daily briefing will switch to your topics as soon as they are ready.');
        assert.equal(await page.locator('.today-general-fallback-note').evaluate(el => el.scrollHeight <= parseFloat(getComputedStyle(el).lineHeight) * 2 + 1 && el.scrollWidth <= el.clientWidth), true, 'Small fallback notice wraps cleanly within two lines at phone width');
        assert.equal(await page.locator('.today-general-fallback-note').evaluate(el => Boolean(el.previousElementSibling?.matches('h1')) && parseFloat(getComputedStyle(el).fontSize) <= 13), true, 'Fallback note sits directly below the Today title');
        assert.equal(await page.locator('.today-build-error').count(), 0, 'Fallback does not show the generation error panel');
        if ((missingEndpoints || fail) && noStories) {
          const expectedHeadings = generalEdition.sections.map(section => section.heading);
          assert.equal(await page.locator('.today-page').count(), 1, 'A no-match profile still gets the general edition page');
          assert.deepEqual(await page.locator('.today-chapter-title').allTextContents(), expectedHeadings, 'No matching profile topic does not hide the general edition stories');
          assert.equal(await page.locator('.today-contents > ol > li').count(), generalEdition.sections.length, 'Every general edition story is visible in the fallback');
          assert.equal(await page.locator('.today-no-stories').count(), 0, 'General fallback stories replace the profile empty state');
          await page.reload({ waitUntil: 'networkidle' });
          await page.locator('.today-general-fallback-note').waitFor();
          assert.deepEqual(await page.locator('.today-chapter-title').allTextContents(), expectedHeadings, 'Unfiltered fallback stories remain visible after reload');
        } else if (missingEndpoints) {
          assert.deepEqual(await page.locator('.today-contents .today-chapter-kicker').allTextContents(), ['1 · Savings rates', '2 · Payments'], 'General edition rows are filtered and ordered by the reader profile');
          assert.equal(await page.locator('.today-contents > ol > li').count(), 2, 'Unmatched general stories are omitted');
          await page.reload({ waitUntil: 'networkidle' });
          await page.locator('.today-general-fallback-note').waitFor();
          assert.deepEqual(await page.locator('.today-contents .today-chapter-kicker').allTextContents(), ['1 · Savings rates', '2 · Payments'], 'Fallback profile and ordering persist across reload');
        }
      }
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile'))?.words), profile.words, 'Profile is saved when generation is unavailable');
      if (!missingEndpoints) assert.ok(profilePost?.profile?.words === profile.words, 'best-effort profile endpoint receives the profile');
      return;
    }
    if (noStories) {
      await page.locator('.today-no-stories').waitFor();
      assert.match(await page.locator('.today-no-stories').innerText(), /Nothing new today on Local planning.*next briefing is tomorrow morning/i);
      assert.equal(await page.locator('.today-page').count(), 0);
      assert.equal(await page.locator('.today-quiet').count(), 0, 'Unmatched topics do not become empty Quiet rows');
      assert.equal(await page.locator('.today-no-stories').innerText().then(text => /\b\d+ stories\b/i.test(text)), false, 'No story count appears when Today has no rows');
      return;
    }
    await page.locator('.today-page').waitFor();
    await page.waitForFunction(() => localStorage.getItem('briefing:v1:built-profile-key') !== null,
      null, { timeout: 10000 });
    const greetingHeadlines = generatedEdition.sections.slice(0, 3).map(section => section.heading);
    assert.equal(await page.locator('.today-page h1').innerText(), 'Your daily briefing');
    assert.equal(await page.locator('.today-chapter-title').count(), 1);
    assert.match(await page.locator('.today-date').innerText(), /^Tuesday 17 November$/, 'Generated Today shows the date and read time without a sample tag');
    assert.equal(await page.locator('.today-page .built-from-words,.today-page .edition-updated,.today-page .today-refresh,.today-page .today-meta,.today-page .edition-summary-dek').count(), 0, 'Generated Today adds no extra metadata, dek or refresh control');
    assert.equal(await page.locator('.today-listen-pill').count(), 1, 'Generated Today has one persistent Listen action');
    assert.equal(await page.locator('.story-kicker').count(), 0, 'One followed topic needs no repeated kicker');
    assert.equal(await page.locator('.today-story-row .today-chapter-title').count(), 1, 'The single chapter title is the row heading');
    assert.equal(generatePost.profile.words, profile.words, 'generate endpoint receives the interpreted profile');
    assert.equal(profilePost.profile.words, profile.words);
    assert.ok(polls >= 3, 'generation progress was polled through fetching, writing and scoring until complete');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:built-profile-key') !== null), true);
  } finally { await browser.close(); }
}

(async () => {
  await flow();
  await flow({ fail: true });
  await flow({ fail: true, noStories: true });
  await flow({ missingEndpoints: true });
  await flow({ missingEndpoints: true, noStories: true });
  await flow({ missingEndpoints: true, fallbackUnavailable: true });
  await flow({ noStories: true });
  await flow({ longBuild: true });
  await flow({ goTodayWhileBuilding: true });
  console.log('Build generation: custom-coverage completed edition, live profile/generate flow, truthful writing progress, Go to Today auto-update, jobs beyond 120 polls, job failure and general fallback passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
