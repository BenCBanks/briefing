require('../test_guard.cjs').enforceTestEnvironment();

// Episode entry remembers length and saves a finished episode topic through the mocked profile API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, builtProfileKey } = require('./helpers/profile.cjs');
const episodeFixture = require('./fixtures/episodes.json').episodes[0];
const content = path.join(__dirname, '../../content');
const pack = {
  topics: JSON.parse(fs.readFileSync(path.join(content, 'topics.json'), 'utf8')),
  sources: JSON.parse(fs.readFileSync(path.join(content, 'sources.json'), 'utf8')),
  editions: JSON.parse(fs.readFileSync(path.join(content, 'editions.json'), 'utf8')).editions,
};
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8936';
const shots = process.env.BRIEFING_SHOT_DIR;

const profile = {
  ...DEFAULT_PROFILE,
  words: 'I follow community banking every day. Skip cryptocurrency hype.',
  topics: [{
    id: 'banking', label: 'Banking', scope: 'Deposits and lending.', include: ['community banking'], exclude: [],
    depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'community banking',
  }],
};
const edition = structuredClone(pack.editions.find(item => item.kind === 'daily'));
edition.id = 'fixture-episode-topic-daily'; edition.revision_id = 'fixture-episode-topic-r1';
edition.generated = true; edition.local_date = '2026-11-18';

async function saveShot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`) });
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York', colorScheme: 'light' });
  page.setDefaultTimeout(7000);
  let serverProfile = structuredClone(profile);
  let profileWrites = [];
  let failNextProfileWrite = false;
  let episodeRequest = null;
  await page.addInitScript(({ seededProfile, key }) => {
    localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(seededProfile));
    localStorage.setItem('briefing:v1:built-profile-key', key);
  }, { seededProfile: profile, key: builtProfileKey(profile) });
  await page.route('**/api/profile', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); profileWrites.push(body.profile);
      if (failNextProfileWrite) { failNextProfileWrite = false; return route.fulfill({ status: 503, json: { error: 'fixture_profile_unavailable' } }); }
      serverProfile = body.profile;
      return route.fulfill({ status: 200, json: { ok: true } });
    }
    return route.fulfill({ json: { profile: serverProfile } });
  });
  await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources: pack.sources.sources } }));
  await page.route('**/api/generate', route => route.fulfill({ status: 503, json: { error: 'fixture_only' } }));
  await page.route('**/api/interpret', route => route.fulfill({ status: 503, json: { error: 'fixture_only' } }));
  await page.route('**/api/corrections**', route => route.fulfill({ json: { corrections: [] } }));
  await page.route('**/api/episodes', async route => {
    if (route.request().method() === 'POST') {
      episodeRequest = route.request().postDataJSON();
      return route.fulfill({ status: 202, json: { job_id: 'f'.repeat(32), topic: episodeRequest.topic, length: episodeRequest.length, status: 'queued' } });
    }
    return route.fulfill({ json: { jobs: [{ job_id: 'e'.repeat(32), topic: 'Fictional payment rules', length: 10, status: 'done', episode: { ...episodeFixture, id: 'e'.repeat(32) } }], left_today: 2 } });
  });
  await page.route('**/api/episodes/*', route => route.fulfill({ json: { job_id: 'f'.repeat(32), topic: 'Fictional coastal radio protocol', length: 10, status: 'researching', stage: 'researching' } }));
  await page.route('**/dev/tests/fixtures/episodes.json', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 1, episodes: [episodeFixture] }) }));
  try {
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    await page.getByRole('link', { name: /Brief me on something else/i }).first().click();
    await page.locator('.episode-request-page').waitFor();
    const selectedLength = page.locator('.on-demand-length-group [aria-pressed="true"]');
    assert.equal(await selectedLength.innerText(), '5 min', 'The visible length buttons default to five minutes');
    await page.locator('.on-demand-length-group .episode-request-chip').filter({ hasText: '10 min' }).click();
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:last-episode-length')), '10');

    await page.getByRole('link', { name: 'Earlier' }).click();
    await page.locator('.library-filters').waitFor();
    await page.getByRole('group', { name: 'Earlier' }).getByRole('button', { name: 'Episodes' }).click();
    const earlierAsk = page.getByRole('link', { name: 'Ask for a briefing' });
    assert.equal(await earlierAsk.count(), 1, 'Earlier episodes keeps one clear request action above its list');
    await earlierAsk.click();
    await page.locator('.episode-request-page').waitFor();
    assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), '10 min', 'Ask opened from Earlier reuses the last selected length');
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.episode-request-page').waitFor();
    assert.equal(await page.locator('.on-demand-length-group [aria-pressed="true"]').innerText(), '10 min', 'The length choice survives reload');
    await page.locator('#episode-request-prompt').fill('Fictional coastal radio protocol');
    await page.getByRole('button', { name: 'Create episode' }).click();
    await page.locator('.episode-making-page').waitFor();
    assert.equal(episodeRequest.length, 10, 'The remembered length reaches the episode API');

    await page.goto(`${baseURL}/?episodes=1#episode/${episodeFixture.id}`, { waitUntil: 'networkidle' });
    await page.locator('.episode-page').waitFor();
    const toggle = page.getByRole('switch', { name: 'Add to my daily briefing' });
    assert.equal(await toggle.isChecked(), false);
    await toggle.check();
    await page.getByText('Added to your daily briefing.', { exact: true }).waitFor();
    assert.ok(profileWrites.some(saved => saved.words === profile.words && saved.topics.some(topic => topic.label === 'Payments')),
      'Saving the topic uses the existing profile API and preserves the reader-authored words');
    assert.equal(profileWrites.at(-1).words, profile.words, 'The reader-authored words stay exactly as written');
    assert.ok(profileWrites.at(-1).topics.some(topic => topic.label === 'Payments'), 'The episode topic is appended to profile topics');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile')).words), profile.words);
    await saveShot(page, 'episode-topic-light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await saveShot(page, 'episode-topic-dark');
    failNextProfileWrite = true;
    await toggle.uncheck();
    await page.getByText('We could not update your topics just now. Please try again.', { exact: true }).waitFor();
    assert.equal(await toggle.isChecked(), true, 'A failed profile save leaves the topic enabled locally');

    await page.getByRole('link', { name: 'Topics' }).click();
    await page.locator('.topics-daily-summary').waitFor();
    const summary = await page.locator('.topics-daily-summary').innerText();
    assert.match(summary, /^Your daily briefing covers:/);
    assert.match(summary, /Banking/); assert.match(summary, /Payments/);
    await saveShot(page, 'topics-daily-summary-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await saveShot(page, 'topics-daily-summary-light');
    console.log('Episode daily topics: Today/Earlier Ask entry, remembered length, profile API topic add, original words, and Topics summary passed.');
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
