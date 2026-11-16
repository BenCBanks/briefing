// Test-only two-revision correction preview. Fictional fixture packs and WAV stay outside app content.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8898';
const fixtureRoot = path.resolve(__dirname, '../fixtures/correction');
const files = ['topics.json', 'deep-dives.json', 'sources.json', 'editions.json'];
const captureDir = process.env.BRIEFING_CAPTURE_DIR;

async function capture(page, name) {
  if (!captureDir) return;
  fs.mkdirSync(captureDir, { recursive: true });
  await page.screenshot({ path: path.join(captureDir, name), fullPage: false });
}

function fixtureWav(seconds = 12) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(22050, 24);
  wav.writeUInt32LE(44100, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(2500 * Math.sin(2 * Math.PI * 440 * i / 22050)), 44 + i * 2);
  return wav;
}

async function installPack(page, getRevision, { recordedR1 = false, staleR2 = false } = {}) {
  await page.route('**/content/*.json', async route => {
    const name = path.basename(new URL(route.request().url()).pathname);
    if (!files.includes(name)) return route.continue();
    const revision = getRevision();
    const body = JSON.parse(fs.readFileSync(path.join(fixtureRoot, revision, name), 'utf8'));
    if (name === 'editions.json' && revision === 'r1' && recordedR1) {
      const item = body.editions.find(edition => edition.kind === 'daily');
      item.audio = { state: 'recorded', revision_id: item.revision_id, script_hash: item.script_hash,
        url: '/content/audio/correction-test.wav', duration_seconds: 12, chapters: [] };
    }
    if (name === 'editions.json' && revision === 'r2' && staleR2) {
      const item = body.editions.find(edition => edition.kind === 'daily');
      item.audio = { state: 'recorded', revision_id: 'fictional-r1', script_hash: 'old-script-hash',
        url: '/content/audio/correction-test.wav', duration_seconds: 12, chapters: [] };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/content/audio/correction-test.wav', async route => {
    const audio = fixtureWav();
    const header = route.request().headers().range || 'bytes=0-';
    const match = /^bytes=(\d+)-(\d*)$/.exec(header);
    const start = match ? Number(match[1]) : 0;
    const end = match && match[2] ? Math.min(Number(match[2]), audio.length - 1) : audio.length - 1;
    await route.fulfill({ status: 206, contentType: 'audio/wav', headers: {
      'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${audio.length}`,
    }, body: audio.subarray(start, end + 1) });
  });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    let revision = 'r1';
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installPack(page, () => revision);
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    await page.locator('.today-primary button.primary').click();
    await page.getByRole('button', { name: 'Save edition' }).click();

    revision = 'r2';
    await page.reload({ waitUntil: 'networkidle' });
    if (await page.locator('.today-primary button.primary').count()) {
      await page.locator('.today-primary button.primary').click();
    }
    assert.match(await page.locator('.correction-notice').innerText(), /120 to 12/);
    assert.match(await page.locator('.correction-notice').innerText(), /Updated audio is unavailable/);
    assert.equal(await page.locator('.edition-play').count(), 0, 'Corrected pending audio has no Play action');
    await capture(page, 'fictional-correction-today-light.png');
    await page.emulateMedia({ colorScheme: 'dark' });
    await capture(page, 'fictional-correction-today-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('button', { name: /Go to corrected section/ }).click();
    await page.locator('#correction-checkout-pilot').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'heading-correction-checkout-pilot', 'Corrected-section jump focuses the heading');
    assert.match(await page.locator('#correction-checkout-pilot').innerText(), /12 participants/);
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']?.revisionId === 'fictional-r2');
    const readingProgress = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']);
    await page.locator('#correction-checkout-pilot .section-sources summary').click();
    await page.locator('#correction-checkout-pilot [data-source="fictional-pilot-record"]').click();
    await page.getByRole('heading', { name: 'Fictional pilot record — demonstration source' }).waitFor();
    await page.waitForFunction(() => document.activeElement?.tagName === 'H1');
    await capture(page, 'fictional-correction-source-light.png');
    assert.equal(await page.getByRole('link', { name: /Open the original source/ }).count(), 0, 'Internal fictional source has no external link');
    assert.equal(await page.locator('.source-publisher').innerText(), 'Fictional demonstration source');
    assert.match(await page.locator('.source-published').innerText(), /2026-11-16/);
    assert.match(await page.locator('.source-title').innerText(), /Fictional pilot record/);
    await page.setViewportSize({ width: 195, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      'Source details reflow without horizontal overflow at narrow zoom width');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.reader-back').click();
    await page.locator('#correction-checkout-pilot').waitFor();
    const returnedSection = await page.locator('#correction-checkout-pilot').evaluate(section => section.getBoundingClientRect());
    assert.ok(returnedSection.top < 844 && returnedSection.bottom > 0, 'Source return restores the corrected reading area in view');
    await page.waitForFunction(() => document.activeElement?.dataset?.source === 'fictional-pilot-record');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress['fictional-correction-edition']), readingProgress, 'Source return keeps reading progress');
    await page.goto(`${baseURL}/#library`, { waitUntil: 'networkidle' });
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedIds), ['fictional-correction-edition'], 'The stable item remains exactly one saved Library entry');
    assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')).savedRevisions['fictional-correction-edition'])), 'fictional-r1', 'The saved revision is retained as metadata');
    assert.match(await page.locator('.library-list').innerText(), /Corrected/);
    await capture(page, 'fictional-correction-library-light.png');
    await page.getByRole('button', { name: 'Saved' }).click();
    assert.match(await page.locator('.library-list').innerText(), /Saved · Corrected · Updated since saved/);
    assert.doesNotMatch(await page.locator('.library-list').innerText(), /fictional-r1/, 'Revision IDs stay out of the reader');
    await page.getByRole('link', { name: 'A fictional checkout pilot' }).click();
    assert.match(await page.locator('#correction-checkout-pilot').innerText(), /12 participants/);

    // Recorded r1 audio exists only in this browser test; reload rejects its stale selection when r2 loads.
    revision = 'r1';
    const active = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installPack(active, () => revision, { recordedR1: true });
    await active.goto(baseURL, { waitUntil: 'networkidle' });
    await active.locator('.today-primary button.primary').click();
    await active.getByRole('button', { name: 'Play briefing' }).click();
    await active.waitForFunction(() => document.querySelector('.edition-play')?.textContent === 'Pause');
    revision = 'r2';
    await active.reload({ waitUntil: 'networkidle' });
    assert.equal(await active.locator('.edition-play').count(), 0, 'No old or updated Play is offered after correction');
    assert.equal(await active.locator('.mini-player').count(), 0, 'Old selected audio is not restored');
    assert.match(await active.locator('.correction-notice').innerText(), /prior recording is no longer available/i);
    const saved = await active.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')));
    assert.equal(saved.activeAudio, null, 'The superseded selection is cleared');
    await active.close();

    // A recorded asset whose revision/hash do not match is ignored while corrected text stays readable.
    revision = 'r2';
    const stale = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installPack(stale, () => revision, { staleR2: true });
    await stale.goto(baseURL, { waitUntil: 'networkidle' });
    await stale.locator('.today-primary button.primary').click();
    assert.equal(await stale.locator('.edition-play').count(), 0, 'Revision/hash mismatch never exposes Play');
    assert.equal(await stale.locator('.mini-player').count(), 0, 'Revision/hash mismatch cannot restore a player');
    assert.match(await stale.locator('#correction-checkout-pilot').innerText(), /12 participants/);
    await stale.close();
    await page.close();
    console.log('Correction preview: save across revisions, corrected-section/source return, stale audio rejection after reload and test-only WAV isolation passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
