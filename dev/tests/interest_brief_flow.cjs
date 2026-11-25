require('../test_guard.cjs').enforceTestEnvironment();

// Topics update contract, entirely fictional; no backend generation is called.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { builtProfileKey } = require('./helpers/profile.cjs');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8941';
const profile = { version: 1, words: 'Follow Fernvale housing, local transport and savings rates. Skip celebrity news.', minutes: 12, background: null, muted: [], topics: [
  { id: 'custom-housing', label: 'Housing', scope: 'Housing in Fernvale', include: ['housing'], exclude: ['celebrity news'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Fernvale housing' },
  { id: 'custom-transport', label: 'Local transport', scope: 'Buses and rail in Fernvale', include: ['local transport'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'local transport' },
  { id: 'banking', label: 'Savings rates', scope: 'Deposit rates and changes', include: ['savings rates'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'savings rates' },
] };
const sample = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'))).editions.find(e => e.kind === 'daily');
const sources = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'))).sources;
const old = { ...sample, id: 'generated-topics-old', revision_id: 'old-r1', generated: true, local_date: '2026-11-17',
  sections: sample.sections.map(section => ({ ...section, topic_ids: ['custom-housing'] })),
  coverage: [{ topic_id: 'custom-housing', state: 'covered' }, { topic_id: 'custom-transport', state: 'limited', reason: 'Only one source met the checks today.' }],
};
const fresh = { ...old, id: 'generated-topics-new', revision_id: 'new-r1', sections: [{ ...sample.sections[0], heading: 'A new fictional housing briefing', topic_ids: ['custom-housing'] }] };
async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name + '.png') });
}
async function run() {
 const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
 try {
  for (const scenario of ['success', 'failure', 'stale', 'interpret-failure', 'save-failure', 'missing-ready', 'pause']) {
   const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
   page.setDefaultTimeout(8000);
   let serverProfile = structuredClone(profile), edition = old, allowJob = false, allowInterpret = false;
   let allowReading = false, allowWriting = false;
   const writes = []; let jobs = 0; let recovering = false; let progressPolls = 0;
   await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
   await page.addInitScript(({ profile, key }) => {
     if (!localStorage.getItem('test-seeded')) {
       localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
       localStorage.setItem('briefing:v1:built-profile-key', key); localStorage.setItem('test-seeded', 'yes');
     }
   }, { profile, key: builtProfileKey(profile) });
   await page.route('**/api/profile', route => {
     if (route.request().method() === 'POST') { if (scenario === 'save-failure' && !recovering) return route.fulfill({ status: 503, json: { error: 'offline' } }); serverProfile = route.request().postDataJSON().profile; writes.push(serverProfile); return route.fulfill({ json: { saved: true } }); }
     return route.fulfill({ json: { profile: serverProfile } });
   });
   await page.route('**/api/edition**', route => route.fulfill({ json: { generated: true, edition, sources } }));
   await page.route('**/api/interpret', async route => {
     while (!allowInterpret) await new Promise(resolve => setTimeout(resolve, 20));
     if (scenario === 'interpret-failure' && !recovering) return route.fulfill({ status: 503, json: { error: 'unavailable' } });
     return route.fulfill({ json: { question: null, profile: { ...profile, words: 'Follow Fernvale housing and local transport. Skip celebrity news.' } } });
   });
   await page.route('**/api/generate', route => { jobs++; return route.fulfill({ json: { job_id: 'topics-job' } }); });
   await page.route('**/api/generate/topics-job', async route => {
     if (progressPolls === 0) { progressPolls++; return route.fulfill({ json: { status: 'running', progress: { stage: 'finding', found: 2 } } }); }
     if (progressPolls === 1) { while (!allowReading) await new Promise(resolve => setTimeout(resolve, 20)); progressPolls++; return route.fulfill({ json: { status: 'running', progress: { stage: 'reading' } } }); }
     if (progressPolls === 2) { while (!allowWriting) await new Promise(resolve => setTimeout(resolve, 20)); progressPolls++; return route.fulfill({ json: { status: 'running', progress: { stage: 'writing', message: 'Writing story 1 of 1' } } }); }
     while (!allowJob) await new Promise(resolve => setTimeout(resolve, 20));
     edition = scenario === 'stale' && !recovering ? old : fresh;
     return route.fulfill({ json: scenario === 'failure' && !recovering ? { status: 'failed' } : scenario === 'missing-ready' && !recovering ? { status: 'complete' } : { status: 'complete', edition_ready: { edition } } });
   });
   // Explicit pending explanation object must be as invisible as an absent one.
   await page.route('**/content/deep-dives.json', async route => {
     const response = await route.fetch(); const json = await response.json();
     json.deep_dives.push({ ...json.deep_dives[0], id: 'placeholder-housing', topic_id: 'custom-housing', status: 'pending', sections: [{ id: 'pending-section', heading: 'Pending', body: 'An explanation is being prepared.', source_ids: [] }] });
     return route.fulfill({ json });
   });
   await page.goto(baseURL + '/#topics', { waitUntil: 'networkidle' });
   assert.equal(await page.locator('.topics-words').inputValue(), profile.words);
   assert.equal(await page.locator('.topics-heading h1').innerText(), 'Your topics');
   assert.equal(await page.locator('.topics-words-label').innerText(), 'Update topics saves these words; switches take effect right away.');
   const text = await page.locator('.topics-page').innerText();
   assert.doesNotMatch(text, /interest brief|evergreen|Rebuild from my words|Edit your interests|being prepared|custom-housing/);
   assert.equal(await page.locator('.topic-row').count(), 3, 'Topics with exclusions remain visible');
   assert.equal(await page.locator('.topic-description').first().innerText(), 'Fernvale housing');
   assert.equal(await page.locator('.topics-skipping').count(), 0, 'Topics keeps exclusions in the editor without negative summary copy');
   assert.equal(await page.locator('.topic-dive-link').count(), 1, 'Only the real banking explanation has Learn more');
   assert.equal(await page.locator('.topic-coverage-note').first().innerText(), 'Plenty');
   await page.locator('.topic-coverage-detail summary').click();
   assert.match(await page.locator('.topic-coverage-detail').innerText(), /Only one source/);
   await page.locator('.topic-coverage-detail summary').click();
   assert.ok(await page.locator('.topics-words').evaluate(el => parseFloat(getComputedStyle(el).fontSize) >= 16));
   assert.ok(await page.locator('.topics-page').evaluate(el => [...el.querySelectorAll('button,a,summary')].every(control => control.getBoundingClientRect().height >= 44)));
   assert.equal(await page.locator('.topics-heading .topics-settings').getAttribute('aria-label'), 'Settings');
   if (scenario === 'success') {
     await capture(page, 'topics-light'); await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'topics-dark'); await page.emulateMedia({ colorScheme: 'light' });
     await page.locator('.topics-settings').scrollIntoViewIfNeeded(); await capture(page, 'topics-settings-bottom'); await page.evaluate(() => scrollTo(0, 0));
     await page.setViewportSize({ width: 195, height: 422 });
     assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
     await page.setViewportSize({ width: 390, height: 844 });
   }
   const words = 'Follow Fernvale housing and local transport. Skip celebrity news.';
   if (scenario === 'pause') {
     await page.getByRole('switch', { name: 'Housing daily updates', exact: true }).click();
   } else {
     await page.locator('.topics-words').fill(words);
     await page.locator('[data-nav=library]').click(); await page.locator('[data-nav=topics]').click();
     assert.equal(await page.locator('.topics-words').inputValue(), words, 'Draft survives navigation');
     await page.locator('.topics-update').click();
   }
   await page.getByRole('button', { name: 'Updating… about a minute', exact: true }).waitFor();
   if (scenario === 'pause') await page.locator('.topics-update-stage').waitFor();
   else await page.locator('.topics-update-stage').filter({ hasText: 'Understanding your words…' }).waitFor();
   assert.ok(await page.locator('.edition-skeleton').count());
   assert.equal(await page.locator('.topic-coverage-note,.topic-coverage-detail').count(), 0, 'Old coverage is suppressed while updating');
   if (scenario === 'success') await capture(page, 'topics-updating');
   // Keep an available dated edition visible while updated topics are being prepared.
   await page.locator('[data-nav=today]').click();
   await page.locator('.today-page').waitFor();
   assert.equal(await page.locator('.today-date').innerText(), 'Tuesday 17 November');
   assert.equal(await page.locator('.today-previous-edition-note').count(), 1);
   assert.equal(await page.locator('.today-build-progress').count(), 0,
     'An available briefing stays above the update status without a preparation panel');
   await page.locator('[data-nav=topics]').click();
   allowInterpret = true;
   if (!['interpret-failure','save-failure'].includes(scenario)) { for (let wait = 0; jobs === 0 && wait < 100; wait++) await page.waitForTimeout(20); assert.ok(jobs); }
   if (['success','failure'].includes(scenario)) {
     await page.locator('.topics-update-stage').filter({ hasText: 'Finding stories · 2 found' }).waitFor();
     allowReading = true;
     await page.locator('.topics-update-stage').filter({ hasText: 'Reading sources…' }).waitFor();
     allowWriting = true;
     await page.locator('.topics-update-stage').filter({ hasText: 'Writing story 1 of 1' }).waitFor();
     assert.equal(await page.locator('.topics-update-stage').innerText(), 'Writing story 1 of 1', 'Topics shows the current server writing stage');
   } else {
     allowReading = true; allowWriting = true;
   }
   allowJob = true;
   if (['success','pause'].includes(scenario)) {
     await page.waitForURL('**/#today');
     await page.locator('.today-page').waitFor();
     assert.equal(new URL(page.url()).hash, '#today');
     assert.match(await page.locator('.today-page').innerText(), /A new fictional housing briefing/);
     assert.ok(writes.length > 0, 'Profile synced before generation');
     assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:topics-pending')), null);
     if (scenario === 'pause') {
       assert.ok(writes.at(-1).muted.some(item => item.label === 'Housing'));
       await page.locator('[data-nav=topics]').click();
       assert.equal(await page.getByRole('switch', { name: 'Housing daily updates', exact: true }).getAttribute('aria-checked'), 'false');
       // Resume uses the same protected profile/save/build path.
       fresh.revision_id = 'new-r2';
       await page.getByRole('switch', { name: 'Housing daily updates', exact: true }).click();
       await page.locator('.today-page').waitFor();
       assert.ok(!writes.at(-1).muted.some(item => item.label === 'Housing'));
     } else assert.equal(writes.at(-1).words, words);
   } else {
     await page.locator('.topics-update-error').waitFor();
     if (scenario === 'failure') {
       assert.equal(await page.locator('.topics-update-error').innerText(), "We couldn't build it right now. Your last briefing is still here.");
       await page.locator('.topics-update-retry').waitFor();
       assert.ok(await page.locator('.topics-update-retry').evaluate(el => el.getBoundingClientRect().height >= 44), 'Retry remains a 44px touch target');
     }
     assert.equal(await page.locator('.topics-words').inputValue(), words);
     if (scenario === 'failure') await capture(page, 'topics-failed');
     await page.locator('[data-nav=today]').click();
     await page.locator('.today-page').waitFor();
     assert.equal(await page.locator('.today-date').innerText(), 'Tuesday 17 November',
       'A failed update leaves the last available edition visible with its actual date');
     assert.equal(await page.locator('.today-previous-edition-note').count(), 1);
     if (scenario === 'failure') {
       await page.locator('[data-nav=topics]').click();
       recovering = true;
       await page.locator('.topics-update-retry').click();
       await page.locator('.today-page').waitFor();
       assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:topics-pending')), null, 'A successful Retry clears the durable gate');
     } else {
       await page.reload({ waitUntil: 'networkidle' });
       assert.equal(await page.locator('.today-page').count(), 1, 'The last available briefing survives a pending update reload');
       assert.equal(await page.locator('.today-previous-edition-note').count(), 1, 'The pending update stays clear after reload');
       assert.equal(await page.locator('.today-build-progress').count(), 0, 'Reload does not replace available content with a preparation panel');
       await page.locator('[data-nav=topics]').click();
       await page.locator('.topics-words').waitFor();
       assert.equal(await page.locator('.topics-words').inputValue(), words);
       if (scenario === 'save-failure') assert.equal(jobs, 0, 'Do not generate until the profile is saved');
       recovering = true;
       await page.locator('.topics-update').click();
       await page.locator('.today-page').waitFor();
       assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:topics-pending')), null, 'A successful retry clears the durable gate');
     }
   }
   await page.close();
  }
  console.log('Topics: calm layout, plain scope, real explanations, coverage reasons, targets, dark mode, profile sync, pause/resume, draft retention, skeleton, success, failure and stale-edition reload gate passed.');
 } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
