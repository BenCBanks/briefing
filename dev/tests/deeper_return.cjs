// Focused fictional-data browser check for the Today → explanation → Today journey.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8898';

function fixtureWav(seconds = 20) {
  const samples = seconds * 22050;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(22050, 24); wav.writeUInt32LE(44100, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 440 * i / 22050)), 44 + i * 2);
  return wav;
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const wav = fixtureWav();
    await page.route('**/content/editions.json', async route => {
      const response = await route.fetch();
      const pack = await response.json();
      const daily = pack.editions.find(item => item.kind === 'daily');
      const seed = daily.sections[0];
      daily.sections = Array.from({ length: 12 }, (_, index) => ({
        ...seed,
        id: `fictional-long-section-${index}`,
        heading: `Fictional section ${index + 1}`,
        topic_ids: [seed.topic_ids[0]],
        body: (`This is fictional test copy for long page layout and scroll restoration. `).repeat(36),
      }));
      daily.audio = { state: 'recorded', revision_id: daily.revision_id, script_hash: daily.script_hash, url: '/content/audio/fixture.wav' };
      await route.fulfill({ response, body: JSON.stringify(pack) });
    });
    await page.route('**/content/audio/fixture.wav', async route => {
      const range = route.request().headers().range || 'bytes=0-';
      const match = /^bytes=(\d+)-(\d*)$/.exec(range);
      const start = match ? Number(match[1]) : 0;
      const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: 206, contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` }, body: wav.subarray(start, end + 1) });
    });

    await page.goto(baseURL, { waitUntil: 'networkidle' });
    const longSection = page.locator('#fictional-long-section-8');
    await longSection.scrollIntoViewIfNeeded();
    const initiallyScrolledY = await page.evaluate(() => scrollY);
    const readDeeper = longSection.getByRole('button', { name: /Read deeper:/ });
    const topicID = await readDeeper.getAttribute('data-topic');
    assert.equal(await readDeeper.count(), 1, 'Available explanation gets a section-level action');
    assert.ok(initiallyScrolledY > 1000, 'Test section is below the first viewport');

    await page.getByRole('button', { name: 'Play briefing' }).click();
    await page.waitForFunction(() => document.querySelector('.edition-play')?.textContent === 'Pause');
    await page.waitForTimeout(1200);
    await page.locator('.edition-play').click();
    await page.waitForFunction(() => /^Resume/.test(document.querySelector('.edition-play')?.textContent || ''));
    await readDeeper.scrollIntoViewIfNeeded();
    const targetY = await page.evaluate(() => scrollY);
    const before = await page.evaluate(() => ({
      activeAudio: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      reading: JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress,
    }));
    assert.ok(before.activeAudio?.id, 'A daily audio item is selected before opening the explanation');
    assert.ok(Object.values(before.progress).some(item => item.seconds > 0), 'A nonzero audio position is saved before navigation');
    await readDeeper.focus();
    await page.keyboard.press('Enter');
    await page.locator('.deep-dive-sources').waitFor();
    assert.deepEqual(await page.locator('.back-link').allTextContents(),
      ['← Back to today’s briefing', '← Back to today’s briefing'], 'Both return controls identify the Today origin');
    await page.locator('.deep-dive-sources').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('.deep-dive-sources a').count(), 'Explanation retains its source links');
    await page.locator('.back-link-bottom').click();
    await page.waitForFunction(() => document.querySelector('#fictional-long-section-8'));
    await page.waitForTimeout(150);
    const after = await page.evaluate(() => ({
      y: scrollY,
      activeAudio: JSON.parse(localStorage.getItem('briefing:v1:state')).activeAudio,
      progress: JSON.parse(localStorage.getItem('briefing:v1:state')).progress,
      reading: JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress,
    }));
    assert.ok(Math.abs(after.y - targetY) < 3, `Scroll restored (${targetY} → ${after.y})`);
    assert.deepEqual(after.activeAudio, before.activeAudio, 'Reading deeper does not switch audio');
    assert.deepEqual(after.progress, before.progress, 'Reading deeper does not change audio position');
    assert.deepEqual(after.reading['daily-sample-001'], before.reading['daily-sample-001'],
      'Reading deeper does not replace the originating edition anchor');
    assert.equal(after.reading['dive-payments-basics']?.revisionId, 'payments-guide-2026-11-16-r2',
      'The explanation saves its own revision-specific reading place');

    await page.evaluate(id => { location.hash = `#topic/${id}`; }, topicID);
    await page.locator('.deep-dive-sources').waitFor();
    assert.deepEqual(await page.locator('.back-link').allTextContents(),
      ['← Back to Topics', '← Back to Topics'], 'A direct topic link must not retain an earlier Today origin');

    await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
    await page.locator('.topic-dive-link').first().click();
    await page.locator('.deep-dive-sources').waitFor();
    assert.deepEqual(await page.locator('.back-link').allTextContents(),
      ['← Back to Topics', '← Back to Topics'], 'Topics entry keeps ordinary Topics navigation');
    await page.setViewportSize({ width: 195, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      'Return actions remain usable without horizontal overflow at narrow zoom width');
    assert.deepEqual(errors, []);
    console.log('Deeper return: long fictional edition, sources, scroll, reading progress and audio state passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
