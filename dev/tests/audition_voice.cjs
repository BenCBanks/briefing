require('../test_guard.cjs').enforceTestEnvironment();

// The audition page uses sample labels and persists a choice without exposing voice IDs.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8935';
const fixtureManifest = { samples: [...'ABCDEF'].map(letter => ({
  label: letter, file: `sample-${letter.toLowerCase()}.wav`, voice: `FixtureVoice${letter}`,
})) };

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(5000);
    page.on('pageerror', error => console.error(`pageerror: ${error.message}`));
    let saved = 'A';
    let posted = null;
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: fixtureManifest }));
    await page.route('**/api/voice', route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: { letter: saved } });
      posted = route.request().postDataJSON();
      saved = posted.letter;
      return route.fulfill({ json: { letter: saved } });
    });
    await page.goto(`${baseURL}/audition`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Use sample B voice' }).waitFor();
    assert.equal(await page.locator('.sample').count(), 6);
    await page.getByRole('button', { name: 'Sample A voice selected' }).waitFor();
    await page.getByRole('button', { name: 'Use sample B voice' }).click();
    await page.getByText('Sample B will be used for new episode audio.').waitFor();
    assert.deepEqual(posted, { letter: 'B' }, 'the audition page submits a sample letter, never a provider voice ID');
    await page.getByRole('button', { name: 'Sample B voice selected' }).waitFor();
    assert.equal(await page.locator('.sample').evaluateAll(rows => rows.every(row =>
      row.querySelector('.choose-voice').getBoundingClientRect().height >= 44)), true,
    'voice selection buttons meet the 44px target');
    const content = await page.locator('body').innerText();
    for (const voiceId of fixtureManifest.samples.map(sample => sample.voice)) assert.equal(content.includes(voiceId), false);
    console.log('audition voice: six labelled choices, GET selection, letter-only POST, selected state and 44px controls passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
