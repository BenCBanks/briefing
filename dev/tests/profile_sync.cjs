require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8935';
const editions = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8')).editions;
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const sampleDaily = editions.find(item => item.kind === 'daily');
const legacyProfile = { version: 1, words: 'I follow payments and banking.', minutes: 12, background: null, muted: [], topics: [
  { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payment rails'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'I follow payments' },
  { id: 'banking', label: 'Banking', scope: 'Deposits and stability', include: ['deposits'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'and banking' },
] };
const serverProfile = { version: 1, words: 'Payments and banking news, NC health insurance and finance policy, AI in finance. Skip crypto hype.', minutes: 12, background: null, muted: [{ label: 'Crypto hype' }], topics: [
  { id: 'custom-payments-and-banking-news', label: 'Payments and banking', scope: 'Payments and banking news', include: ['payments and banking news'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Payments and banking news' },
  { id: 'custom-nc-health-insurance-and-finance-policy', label: 'NC health insurance and finance policy', scope: 'North Carolina health insurance and finance policy', include: ['NC health insurance and finance policy'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'NC health insurance and finance policy' },
  { id: 'custom-ai-in-finance', label: 'AI in finance', scope: 'AI in finance', include: ['AI in finance'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'AI in finance' },
] };
const profileKey = profile => JSON.stringify({ words: profile?.words || '', minutes: profile?.minutes || 12,
  topics: (profile?.topics || []).map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude, depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })), muted: profile?.muted || [] });
const generatedEdition = { ...sampleDaily, id: 'generated-server-profile-sync', revision_id: 'generated-server-profile-sync-r1', script_hash: 'fictional-profile-sync-hash', generated: true,
  local_date: '2026-11-17', generated_at: '2026-11-17T06:02:00-05:00', title: 'Briefing for the saved profile',
  coverage: serverProfile.topics.map(topic => ({ topic_id: topic.id, label: topic.label, state: 'covered' })),
  sections: sampleDaily.sections.map((section, index) => ({ ...section,
    topic_ids: [index === 2 ? serverProfile.topics[2].id : index === 3 ? serverProfile.topics[1].id : serverProfile.topics[0].id] })) };
const generatedResponse = { generated: true, edition: generatedEdition, sources };

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(7000);
    const generatePosts = [];
    await page.addInitScript(({ profile, key }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
      localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { profile: legacyProfile, key: profileKey(legacyProfile) });
    await page.route('**/api/profile', async route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: { profile: serverProfile } });
      await route.fulfill({ json: { saved: true } });
    });
    await page.route('**/api/edition*', route => route.fulfill({ json: generatedResponse }));
    await page.route('**/api/generate', async route => {
      generatePosts.push(route.request().postDataJSON());
      // The first build after profile sync must be newer than the already
      // active server edition, which correctly avoided a duplicate build.
      generatedEdition.revision_id = 'generated-server-profile-sync-r' + (generatePosts.length + 1);
      await route.fulfill({ status: 202, json: { job_id: 'legacy-profile-sync-job', status: 'queued' } });
    });
    await page.route('**/api/generate/legacy-profile-sync-job', route => route.fulfill({ json: {
      status: 'complete', progress: { stage: 'complete', message: 'Edition ready' }, edition_ready: { edition: generatedEdition },
    } }));

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    assert.equal(generatePosts.length, 0, 'the matching active-profile edition avoids a duplicate build after syncing a legacy browser profile');
    assert.equal(await page.locator('.today-page h1').innerText(), 'Your daily briefing');
    assert.equal(await page.locator('[data-jump-story]').first().getAttribute('data-jump-story'), generatedEdition.sections[0].id, 'Top stories point to the edition generated for the synced profile');
    assert.equal(await page.locator('.today-top-story-copy strong').first().innerText(), 'A faster checkout', 'Top story uses a short headline-derived subject');
    assert.deepEqual(await page.locator('.today-contents .story-row-copy strong').allTextContents(), generatedEdition.sections.map(section => section.heading), 'Today shows every story for the synced profile instead of filtering with old local topics');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:interest-profile')).words), serverProfile.words, 'saved server profile replaces the legacy browser profile');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:built-profile-key')), profileKey(serverProfile), 'completed edition is keyed to the synced profile');

    await page.evaluate(() => { location.hash = '#interests'; });
    const depthBuild = page.waitForResponse(response => response.url().includes('/api/generate/legacy-profile-sync-job'));
    await page.getByLabel('Depth').first().selectOption('deeper');
    await depthBuild;
    await page.waitForFunction(() => !localStorage.getItem('briefing:v1:topics-pending'));
    await page.locator('.interest-topic-row').first().waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#interests', 'Topic editing stays in place while the automatic rebuild runs');
    assert.equal(generatePosts.length, 1, 'editing a followed topic automatically starts a fresh profile build');
    assert.equal(generatePosts.at(-1)?.profile?.topics[0]?.depth, 'deeper', 'the automatic build uses the changed topic profile');

    await page.route('**/api/interpret', route => route.fulfill({ json: { profile: serverProfile, question: null } }));
    await page.evaluate(() => { location.hash = '#topics'; });
    await page.getByRole('button', { name: 'Update topics' }).click();
    await page.locator('.today-page').waitFor();
    assert.equal(generatePosts.length, 2, 'Topics rebuild action starts a new profile build');
    await page.evaluate(() => { location.hash = '#preferences'; });
    const rebuildFromSettings = page.getByRole('button', { name: 'Update my briefing' });
    await rebuildFromSettings.click();
    await page.locator('.topics-update').click();
    await page.locator('.today-page').waitFor();
    assert.equal(generatePosts.length, 3, 'Settings rebuild action starts another profile build');
    assert.ok(generatePosts.every(request => request.profile?.words === serverProfile.words), 'Topics and Settings rebuild actions use the stored words');
    console.log('Legacy profile sync: server profile replaced stale local topics, matching active edition avoided a duplicate build, and later edits/rebuilds used stored words.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
