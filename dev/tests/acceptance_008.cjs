require('../test_guard.cjs').enforceTestEnvironment();

// Design 008 web acceptance harness. Run with BRIEFING_URL set to a preview origin.
// All added audio/chapter metadata and WAV bytes are fictional and exist only in this test context.
const assert = require('node:assert/strict');
const auditionManifest = require('./fixtures/audition_manifest.json');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8931';
const timeout = Number(process.env.BRIEFING_TEST_TIMEOUT || 4500);
const results = [];

async function check(name, run) {
  try {
    const detail = await run();
    results.push({ name, status: 'PASS', detail: detail || '' });
  } catch (error) {
    results.push({ name, status: 'FAIL', detail: String(error.message || error).split('\n')[0] });
  }
}

async function newPage(browser, viewport = { width: 390, height: 844 }) {
  const page = await browser.newPage({ viewport, timezoneId: 'America/New_York' });
  page.setDefaultTimeout(timeout);
  await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: auditionManifest }));
  return page;
}

async function gotoRoot(page, root = 'today') {
  await page.goto(`${baseURL}/#${root}`, { waitUntil: 'networkidle' });
  await page.locator('main h1, #content h1').first().waitFor({ state: 'visible' });
}

function firstStoryRow(page) {
  return page.locator('.today-contents button, [data-story-row], .story-row').first();
}

async function openFirstStory(page) {
  await gotoRoot(page);
  const opener = firstStoryRow(page);
  assert.ok(await opener.count(), 'Today has no story opening control');
  await opener.scrollIntoViewIfNeeded();
  const openingScroll = await page.evaluate(() => window.scrollY);
  const lines = (await opener.innerText()).split('\n').map(value => value.trim()).filter(Boolean);
  const title = await opener.locator('strong').first().textContent().catch(() => null) || lines[1] || lines[0];
  const before = page.url();
  await opener.click();
  await page.waitForFunction(previous => location.href !== previous, before);
  await page.locator('body.is-reader .edition-section').waitFor({ state: 'visible' });
  await page.locator('.reader-sticky .story-progress').waitFor({ state: 'visible' });
  return { opener, openingScroll, title: title.trim(), hash: new URL(page.url()).hash };
}

async function openFirstSource(page) {
  const summaries = page.locator('details summary');
  for (let i = 0; i < await summaries.count(); i += 1) {
    const summary = summaries.nth(i);
    if (/sources/i.test(await summary.innerText()) && !(await summary.evaluate(element => element.parentElement?.open))) {
      await summary.click();
    }
  }
  const sourceOpener = page.locator('[data-source]').first();
  assert.ok(await sourceOpener.count(), 'Story has no source opening control');
  assert.equal(await sourceOpener.isVisible(), true, 'Expanded story keeps its source link visible');
  await sourceOpener.scrollIntoViewIfNeeded();
  const openingScroll = await page.evaluate(() => window.scrollY);
  const sourceId = await sourceOpener.getAttribute('data-source');
  const before = page.url();
  await sourceOpener.click();
  await page.waitForFunction(previous => location.href !== previous, before);
  await page.locator('.source-title').waitFor({ state: 'visible' });
  return { sourceOpener, sourceId, openingScroll, hash: new URL(page.url()).hash };
}

async function openFirstExplanation(page) {
  await gotoRoot(page, 'topics');
  const opener = page.locator('[data-topic]').first();
  assert.ok(await opener.count(), 'Topics has no explanation opening control');
  await opener.scrollIntoViewIfNeeded();
  const openingScroll = await page.evaluate(() => window.scrollY);
  const before = page.url();
  await opener.click();
  await page.waitForFunction(previous => location.href !== previous, before);
  await page.locator('.deep-dive-page').waitFor({ state: 'visible' });
  return { opener, openingScroll, hash: new URL(page.url()).hash };
}

function backControl(page) {
  return page.locator('.reader-back, .page-back, .source-back, [data-back]').filter({ visible: true }).first();
}

async function backToParent(page) {
  const control = backControl(page);
  assert.ok(await control.count(), 'Pushed page has no visible Back control');
  await control.click();
}

async function visibleSampleTag(page) {
  return page.locator('[class*="sample" i], [data-sample], .today-date').evaluateAll(elements => elements.some(element => {
    const text = `${element.innerText || ''} ${element.getAttribute('aria-label') || ''}`;
    return element.getClientRects().length > 0 && /\bsample\b/i.test(text);
  }));
}

async function activeNavNames(page) {
  return page.locator('[data-nav]').evaluateAll(elements => elements.filter(element => {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }).map(element => element.innerText.trim()));
}

async function currentRouteDescriptors(browser) {
  const page = await newPage(browser);
  const routes = {};
  try {
    const story = await openFirstStory(page);
    routes.story = { hash: story.hash, title: story.title };
    const source = await openFirstSource(page);
    routes.source = { hash: source.hash, id: source.sourceId };
    await gotoRoot(page, 'topics');
    const explanation = await openFirstExplanation(page);
    routes.explanation = { hash: explanation.hash };
    routes.library = { hash: '#library' };
  } finally { await page.close(); }
  return routes;
}

function fixtureWav(seconds = 12) {
  const sampleRate = 22050;
  const samples = seconds * sampleRate;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i += 1) wav.writeInt16LE(Math.round(1800 * Math.sin(2 * Math.PI * 440 * i / sampleRate)), 44 + i * 2);
  return wav;
}

async function installFictionalChapters(page) {
  await page.route('**/content/editions.json', async route => {
    const response = await route.fetch();
    const data = await response.json();
    const item = data.editions.find(edition => edition.kind === 'daily');
    assert.ok(item, 'Test fixture could not find the fictional daily edition');
    item.audio = {
      state: 'recorded', revision_id: item.revision_id, script_hash: item.script_hash,
      url: '/content/audio/acceptance-008.wav', duration_seconds: 12,
      chapters: [
        { id: 'acceptance-opening', title: 'A fictional overview of choices in a sample checkout pilot', start_seconds: 0 },
        { id: 'acceptance-follow-up', title: 'Fictional follow-up and alternate ways to resolve duplicate authorizations', start_seconds: 6 },
      ],
    };
    await route.fulfill({ response, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.route('**/content/audio/acceptance-008.wav', async route => {
    const wav = fixtureWav();
    const range = route.request().headers().range || 'bytes=0-';
    const match = /^bytes=(\d+)-(\d*)$/.exec(range);
    const start = match ? Number(match[1]) : 0;
    const end = match && match[2] ? Math.min(Number(match[2]), wav.length - 1) : wav.length - 1;
    await route.fulfill({ status: 206, contentType: 'audio/wav', headers: {
      'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}`,
    }, body: wav.subarray(start, end + 1) });
  });
}

async function installPreviewStyles(page, css) {
  await page.route('**/styles/theme.css', async route => {
    const response = await route.fetch();
    const original = await response.text();
    await route.fulfill({ response, contentType: 'text/css', body: `${original}\n/* acceptance test text scale */\n${css}` });
  });
}

async function chapterPage(browser, css = '', viewport = { width: 390, height: 844 }) {
  const page = await newPage(browser, viewport);
  await page.addInitScript(() => {
    class FixtureUtterance { constructor(text) { this.text = text; this.rate = 1; } }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: FixtureUtterance, configurable: true });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      paused: false, speaking: false,
      speak(item) { this.speaking = true; setTimeout(() => item.onstart?.(), 0); },
      pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.paused = false; this.speaking = false; },
    } });
  });
  if (css) await installPreviewStyles(page, css);
  await installFictionalChapters(page);
  await gotoRoot(page);
  const listen = page.locator('.today-listen-pill');
  assert.ok(await listen.count(), 'No Today Listen control for the player chapter journey');
  await listen.click().catch(error => { throw new Error(`Start fixture narration: ${error.message.split('\n')[0]}`); });
  const mini = page.locator('.mini-player-open');
  await mini.waitFor({ state: 'visible' }).catch(error => { throw new Error(`Mini-player did not appear: ${error.message.split('\n')[0]}`); });
  await mini.click().catch(error => { throw new Error(`Open full player: ${error.message.split('\n')[0]}`); });
  const dialog = page.locator('.player-dialog[open], dialog[open]');
  await dialog.waitFor({ state: 'visible' }).catch(error => { throw new Error(`Full player did not open: ${error.message.split('\n')[0]}`); });
  await page.getByRole('button', { name: /Chapters/i }).click().catch(error => { throw new Error(`Open Chapters: ${error.message.split('\n')[0]}`); });
  await page.locator('[data-player-chapters-view]:not([hidden]), .player-chapters-view:visible').first().waitFor({ state: 'visible' }).catch(error => { throw new Error(`Chapter list did not render: ${error.message.split('\n')[0]}`); });
  return page;
}

function clipReport(page) {
  return page.evaluate(() => {
    const candidates = [
      ...document.querySelectorAll('main h1, #content h1, .reader-back, .page-back, .source-back, [data-back], .player-chapter-row strong'),
    ].filter(element => element.getClientRects().length > 0);
    const clipped = candidates.filter(element => {
      const style = getComputedStyle(element);
      const horizontal = element.scrollWidth > element.clientWidth + 2 && /(hidden|clip)/.test(style.overflowX);
      const vertical = element.scrollHeight > element.clientHeight + 2 && /(hidden|clip)/.test(style.overflowY);
      const rect = element.getBoundingClientRect();
      return horizontal || vertical || rect.left < -1 || rect.right > innerWidth + 1;
    }).map(element => (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 70));
    return { clipped, scrollWidth: document.documentElement.scrollWidth, innerWidth };
  });
}

async function verifyPageHeading(page, title = null) {
  const heading = page.locator('main h1, #content h1').first();
  await heading.waitFor({ state: 'visible' });
  const text = (await heading.innerText()).trim();
  if (title) assert.equal(text, title, `Unexpected page heading for ${page.url()}`);
  return text;
}

async function run() {
  let parsed;
  try { parsed = new URL(baseURL); }
  catch { throw new Error(`Invalid BRIEFING_URL: ${baseURL}`); }
  assert.ok(['127.0.0.1', 'localhost'].includes(parsed.hostname), 'Acceptance runs are limited to loopback preview servers');
  assert.ok(['8931', '8932', '8933', '8935', '8940', '18940', '18941', '18942'].includes(parsed.port) || Number(parsed.port) >= 32768, `Run only against an assigned loopback preview port (got :${parsed.port})`);

  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  let routes = {};
  try {
    await check('Route discovery for story, source and explanation', async () => {
      routes = await currentRouteDescriptors(browser);
      assert.ok(routes.story.hash.startsWith('#edition/'), `Unexpected story route ${routes.story.hash}`);
      assert.ok(routes.source.hash.startsWith('#source/'), `Unexpected source route ${routes.source.hash}`);
      assert.ok(routes.explanation.hash.startsWith('#explanation/') || routes.explanation.hash.startsWith('#topic/'),
        `Unexpected explanation route ${routes.explanation.hash}`);
      return `${routes.story.hash} · ${routes.source.hash} · ${routes.explanation.hash}`;
    });

    await check('Heading levels: Today greeting, story h2s and full-reader h1', async () => {
      const page = await newPage(browser);
      try {
        await gotoRoot(page);
        assert.equal(await page.locator('main h1, #content h1').count(), 1, 'Today should expose one h1');
        assert.ok(await page.locator('.today-story-row h2').count() > 0, 'Every Today story has a semantic heading');
        const story = await openFirstStory(page);
        const headings = await page.locator('main h1, #content h1').allInnerTexts();
        const h2 = await page.locator('main h2, #content h2').allInnerTexts();
        assert.ok(headings.some(text => text.trim() === story.title), `Story title is not an h1: ${headings.join(' | ')}`);
        assert.equal(await page.locator('.edition-section h2').count(), 0, 'Prose is not split into labelled blocks');
        return `Today h1; story “${story.title}” h1; ${h2.length} h2 sections`;
      } finally { await page.close(); }
    });

    await check('Back restores Today → Story → Source scroll and opener focus', async () => {
      const page = await newPage(browser);
      try {
        const story = await openFirstStory(page);
        const source = await openFirstSource(page);
        await backToParent(page);
        await page.waitForFunction(() => location.hash.startsWith('#edition/'));
        await page.waitForFunction(() => document.activeElement?.matches('[data-source]')).catch(async error => { throw new Error(`Source opener focus: ${await page.evaluate(() => document.activeElement?.outerHTML)}; ${error.message}`); });
        const sourceFocus = await page.locator('[data-source]').first().evaluate(element => element === document.activeElement).catch(() => false);
        const sourceScroll = await page.evaluate(() => window.scrollY);
        assert.ok(sourceFocus, 'Back from Source did not return focus to its opening source control');
        assert.ok(Math.abs(sourceScroll - source.openingScroll) <= 4, `Back from Source restored scroll ${sourceScroll}, expected ${source.openingScroll}`);
        await backToParent(page);
        await page.waitForFunction(() => location.hash === '#today');
        await page.waitForFunction(() => document.activeElement?.matches('.today-contents button, [data-story-row], .story-row')).catch(async error => { throw new Error(`Today opener focus: ${await page.evaluate(() => document.activeElement?.outerHTML)}; ${error.message}`); });
        const storyFocus = await firstStoryRow(page).evaluate(element => element === document.activeElement).catch(() => false);
        const todayScroll = await page.evaluate(() => window.scrollY);
        assert.ok(storyFocus, 'Back from Story did not return focus to its opening control on Today');
        assert.ok(Math.abs(todayScroll - story.openingScroll) <= 4, `Back to Today restored scroll ${todayScroll}, expected ${story.openingScroll}`);
        return 'Source opener and Today story-row opener regain focus and saved scroll';
      } finally { await page.close(); }
    });

    await check('Back restores Topics → Explanation scroll and opener focus', async () => {
      const page = await newPage(browser);
      try {
        const explanation = await openFirstExplanation(page);
        await backToParent(page);
        await page.waitForFunction(() => location.hash === '#topics');
        await page.waitForFunction(() => document.activeElement?.matches('[data-topic]'));
        const focus = await page.locator('[data-topic]').first().evaluate(element => element === document.activeElement).catch(() => false);
        const scroll = await page.evaluate(() => window.scrollY);
        assert.ok(focus, 'Back from Explanation did not focus the opening Topics control');
        assert.ok(Math.abs(scroll - explanation.openingScroll) <= 4, `Back to Topics restored scroll ${scroll}, expected ${explanation.openingScroll}`);
        return 'Explanation opener regains focus and saved Topics scroll';
      } finally { await page.close(); }
    });

    await check('Browser history traverses Story, Source and Explanation routes', async () => {
      const page = await newPage(browser);
      try {
        const story = await openFirstStory(page);
        const source = await openFirstSource(page);
        await page.goBack();
        await page.waitForFunction(hash => location.hash === hash, story.hash);
        await page.goBack();
        await page.waitForFunction(() => location.hash === '#today');
        const explanation = await openFirstExplanation(page);
        await page.goBack();
        await page.waitForFunction(() => location.hash === '#topics');
        return `History returned from ${source.hash} to Story/Today and from ${explanation.hash} to Topics`;
      } finally { await page.close(); }
    });

    await check('Direct URL loads for Story, Source, Explanation and Library routes', async () => {
      assert.ok(routes.story && routes.source && routes.explanation, 'Route discovery failed; cannot test direct URLs');
      const descriptors = [
        ['story', routes.story.hash], ['source', routes.source.hash], ['explanation', routes.explanation.hash], ['library', '#library'],
      ];
      const loaded = [];
      for (const [name, hash] of descriptors) {
        const page = await newPage(browser);
        try {
          await page.goto(`${baseURL}/${hash}`, { waitUntil: 'networkidle' });
          const heading = await verifyPageHeading(page);
          assert.equal(new URL(page.url()).hash, hash, `${name} route did not retain its direct URL`);
          loaded.push(`${name}: ${heading}`);
        } finally { await page.close(); }
      }
      return loaded.join('; ');
    });

    await check('Text tabs remain available on root and pushed screens', async () => {
      const page = await newPage(browser);
      try {
        for (const root of ['today', 'topics', 'library']) {
          await gotoRoot(page, root);
          const names = await activeNavNames(page);
          assert.ok(names.includes('Today') && names.includes('On demand') && names.includes('Topics') && names.includes('Earlier'), `${root} root tabs missing: ${names.join(', ')}`);
        }
        for (const [name, hash] of [['story', routes.story?.hash], ['source', routes.source?.hash], ['explanation', routes.explanation?.hash]]) {
          if (!hash) throw new Error(`${name} route unavailable`);
          await page.goto(`${baseURL}/${hash}`, { waitUntil: 'networkidle' });
          assert.deepEqual(await activeNavNames(page), ['Today','On demand','Earlier','Topics'], `V3 text tabs remain available on ${name}`);
        }
        return 'Today, Earlier and Topics stay reachable from content pages';
      } finally { await page.close(); }
    });

    await check('Today is readable inline with one persistent listening control', async () => {
      const page = await newPage(browser);
      try {
        await gotoRoot(page);
        assert.equal(await page.locator('.today-listen-pill').count(), 1);
        assert.equal(await page.locator('.today-mode-read,.story-row-play').count(), 0);
        const storyCount = await page.locator('.today-story-row').count();
        assert.equal(await page.locator('.inline-story .edition-section').count(), storyCount, 'Every story is readable inline');
        assert.ok(await page.locator('.inline-story p').count() > storyCount);
        await page.locator('.story-row-link').first().click();
        await page.waitForFunction(() => location.hash.startsWith('#edition/'));
        const progress = await page.locator('.story-progress').innerText();
        assert.match(progress, /^Story 1 of \d+$/, 'Story progress reflects the edition sections, even when profile matching hides a row');
        const storyListen = page.locator('[data-listen-control="story"]');
        assert.equal(await storyListen.count(), 1, 'Story should have one Listen-from-here control');
        assert.match(await storyListen.innerText(), /Listen from here\s*·\s*about \d+ min/);
        assert.match(await page.locator('.story-context-line').innerText(), / · \d{1,2} [A-Z][a-z]{2}$/);
        assert.equal(await page.locator('.reader-story-header .story-source-status').count(), 0, 'Citation notes appear below the headline');
        assert.equal(await page.locator('.prototype-voice-label').count(), 0, 'Prototype voice wording must not appear');
        return `${storyCount} inline stories, one persistent Listen control, and ${progress} linked reader verified`;
      } finally { await page.close(); }
    });

    await check('Pending editions and explanations retain ordinary listening controls', async () => {
      const page = await newPage(browser);
      try {
        await gotoRoot(page);
        const pack = await page.evaluate(async () => {
          const [editions, dives] = await Promise.all([
            fetch('/content/editions.json').then(response => response.json()),
            fetch('/content/deep-dives.json').then(response => response.json()),
          ]);
          return { editions: editions.editions, dives: dives.deep_dives };
        });
        const pending = [...pack.editions, ...pack.dives].filter(item => item.audio?.state !== 'recorded').map(item => item.id);
        assert.ok(pending.length, 'Fixture pack has no pending audio item to verify');
        const checked = [];
        const surfaces = [['Today', '#today'], ['Story', routes.story?.hash], ['Topics', '#topics'], ['Explanation', routes.explanation?.hash]];
        for (const [surface, hash] of surfaces) {
          if (!hash) throw new Error(`${surface} route unavailable for listening check`);
          await page.goto(`${baseURL}/${hash}`, { waitUntil: 'networkidle' });
          const actions = page.locator('button[data-audio-action]:visible, button[data-listen-item]:visible, button.today-listen-pill:visible, button.story-listen:visible');
          if (await actions.count()) {
            const label = await actions.first().evaluate(button => button.getAttribute('aria-label') || button.innerText);
            assert.ok(label.trim(), `${surface} listening control has a visible or accessible action name`);
            checked.push(surface);
          }
        }
        assert.ok(checked.includes('Today') && checked.includes('Story') && checked.includes('Explanation'), `Expected listening controls on content surfaces: ${checked.join(', ')}`);
        const pageCopy = await page.locator('body').innerText();
        assert.equal(/\bprototype\b/i.test(pageCopy), false, 'No prototype wording appears');
        return `${pending.length} pending edition items retain listening controls on ${checked.join(', ')}`;
      } finally { await page.close(); }
    });

    await check('Four destinations retain readable text labels at large text', async () => {
      const page = await newPage(browser);
      try {
        await gotoRoot(page, 'topics');
        const tabs = await page.evaluate(() => {
          document.documentElement.style.fontSize = '200%';
          return [...document.querySelectorAll('.main-nav a')].map(anchor => {
            const icon = anchor.querySelector('svg'); const label = anchor.querySelector('.main-nav-label');
            const box = label.getBoundingClientRect();
            return { name: label.textContent.trim(), icon: Boolean(icon), size: icon ? parseFloat(getComputedStyle(icon).width) : 0, fontSize: parseFloat(getComputedStyle(label).fontSize), visible: box.width > 0 && box.height > 0 };
          });
        });
        assert.deepEqual(tabs.map(tab => tab.name), ['Today', 'On demand', 'Earlier', 'Topics']);
        assert.ok(tabs.every(tab => tab.icon && tab.size === 24), 'V3 navigation retains 24px icons alongside readable labels');
        assert.ok(tabs.every(tab => tab.fontSize >= 16 && tab.visible), JSON.stringify(tabs));
        assert.equal(await page.getByRole('link', { name: 'Settings' }).count(), 1, 'Settings is available in Topics');
        await gotoRoot(page, 'today');
        assert.equal(await page.getByRole('link', { name: 'Settings' }).count(), 0, 'Settings is tucked into Topics');
        return 'Three labelled destinations remain readable; Settings lives in Topics';
      } finally { await page.close(); }
    });

    await check('Topics, explanation and weekly pages offer Listen with narrator Settings', async () => {
      const page = await newPage(browser);
      try {
        await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
        assert.equal(await page.locator('.topic-listen-button').count(), 0, 'Topics keeps explanation audio inside Learn more');
        await page.locator('.topic-dive-link').first().click();
        assert.match(await page.locator('.deep-dive-listen').innerText(), /Listen · about \d+ min/);
        await page.goto(`${baseURL}/#weekly`, { waitUntil: 'networkidle' });
        assert.match(await page.locator('.weekly-listen').innerText(), /Listen\s*·\s*about \d+ min/);
        assert.equal(await page.locator('.prototype-voice-label').count(), 0, 'Weekly page does not expose a prototype-voice label');
        await page.goto(`${baseURL}/#preferences`, { waitUntil: 'networkidle' });
        await page.locator('.narrator-sample').first().waitFor();
        assert.equal(await page.locator('.narrator-sample').count(), 6, 'Settings lists six voice audition samples');
        assert.equal(await page.getByText('About the AI voice').count(), 1, 'Settings explains the synthetic narrator');
        return 'Topics, explanation and weekly Listen controls are present; Settings offers six narrator samples';
      } finally { await page.close(); }
    });

    await check('Today keeps a calm date line and story headers stay uncluttered', async () => {
      assert.ok(routes.story && routes.source && routes.explanation, 'Route discovery failed; cannot check Sample tags');
      const sample = await newPage(browser);
      try {
        await sample.route('**/api/edition**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: false }) }));
        await gotoRoot(sample);
        assert.match(await sample.locator('.today-date').innerText(), /^[A-Z][a-z]+ \d+ [A-Z][a-z]+$/, 'V3 date line contains only the date');
        assert.equal(await sample.locator('.today-title').innerText(), 'Your daily briefing', 'Today uses a fixed daily title instead of a long linked greeting');
        assert.equal(await sample.locator('.today-greeting').count(), 0, 'Today has no greeting paragraph wall');
        assert.ok(await sample.locator('.today-top-stories li').count() > 0, 'Top stories appear as numbered rows');
        await sample.locator('.story-row-link').first().click();
        await sample.locator('body.is-reader .edition-section').waitFor();
        assert.equal(await visibleSampleTag(sample), false, 'Story page keeps the header quiet without a Sample tag');
      } finally { await sample.close(); }
      return 'Date-only Today header and plain story header verified';
    });

    await check('Visible interactive targets are at least 44 px tall', async () => {
      const routesToMeasure = [['Today', '#today'], ['Story', routes.story?.hash], ['Source', routes.source?.hash], ['Explanation', routes.explanation?.hash], ['Library', '#library']];
      const short = [];
      for (const [name, hash] of routesToMeasure) {
        if (!hash) { short.push(`${name}: route unavailable`); continue; }
        const page = await newPage(browser);
        try {
          await page.goto(`${baseURL}/${hash}`, { waitUntil: 'networkidle' });
          const items = await page.locator('button, a[href], input, select, textarea, summary, [role="button"], [role="link"]').evaluateAll(elements => elements.filter(element => {
            if (element.disabled) return false;
            const style = getComputedStyle(element);
            return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          }).map(element => {
            const target = getComputedStyle(element, '::after');
            return { label: (element.innerText || element.getAttribute('aria-label') || element.tagName).trim().replace(/\s+/g, ' ').slice(0, 48), height: element.getBoundingClientRect().height, citationTarget: element.matches('.cite') && target.position === 'absolute' && parseFloat(target.width) >= 44 && parseFloat(target.height) >= 44 };
          }));
          for (const item of items.filter(item => item.height < 43.5 && !item.citationTarget)) short.push(`${name}: “${item.label}” ${item.height.toFixed(1)}px`);
        } finally { await page.close(); }
      }
      assert.deepEqual(short, [], short.slice(0, 8).join('; '));
      return 'Visible buttons, links, inputs and summaries meet 44 px';
    });

    for (const scale of ['large-text', '200-percent-zoom']) {
      await check(`${scale === 'large-text' ? 'Large-text' : '200% zoom'} reflows headers, Back labels and chapter names at 390 px`, async () => {
        assert.ok(routes.story, 'Story route unavailable for reflow check');
        const storyPage = await newPage(browser);
        let chapterTestPage;
        try {
          const style = scale === 'large-text'
            ? 'html{font-size:200%!important} h1{font-size:2em!important;line-height:1.2!important} .reader-back,.page-back,[data-back]{font-size:2em!important;white-space:normal!important} .player-chapter-row strong{font-size:2em!important;line-height:1.25!important} .player-sheet [data-player-chapters-view] h2{font-size:2em!important}'
            : '';
          if (style) await installPreviewStyles(storyPage, style);
          if (scale === '200-percent-zoom') await storyPage.setViewportSize({ width: 195, height: 844 });
          await storyPage.goto(`${baseURL}/${routes.story.hash}`, { waitUntil: 'networkidle' });
          const appliedScale = await storyPage.evaluate(() => ({
            fontSize: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
            zoom: Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1,
            innerWidth,
          }));
          if (scale === 'large-text') assert.ok(appliedScale.fontSize >= 32, `Large-text stylesheet did not apply: ${appliedScale.fontSize}px root text`);
          else assert.ok(appliedScale.innerWidth <= 195, `200% zoom layout width was not reduced: ${appliedScale.innerWidth}px`);
          const storyClips = await clipReport(storyPage);
          const back = backControl(storyPage);
          assert.ok(await back.count(), 'Story has no Back label to inspect');
          const backClips = await back.evaluate(element => {
            const style = getComputedStyle(element);
            return (element.scrollWidth > element.clientWidth + 2 && /(hidden|clip)/.test(style.overflowX)) || element.getBoundingClientRect().right > innerWidth + 1;
          });
          assert.deepEqual(storyClips.clipped, [], `Story header/Back clipping: ${storyClips.clipped.join(' | ')}`);
          assert.equal(backClips, false, 'Story Back label clips or runs outside the 390 px viewport');

          chapterTestPage = await chapterPage(browser, style, scale === '200-percent-zoom' ? { width: 195, height: 844 } : undefined);
          const chapterClips = await clipReport(chapterTestPage);
          const labels = await chapterTestPage.locator('.player-chapter-row strong').evaluateAll(elements => elements.map(element => ({
            text: element.innerText.trim(), width: element.clientWidth, scrollWidth: element.scrollWidth,
            height: element.clientHeight, scrollHeight: element.scrollHeight,
          })));
          assert.ok(labels.length >= 2, `Expected one chapter per story, found ${labels.length}`);
          assert.deepEqual(chapterClips.clipped, [], `Chapter label clipping: ${chapterClips.clipped.join(' | ')}`);
          assert.ok(labels.every(label => label.width > 0 && label.height > 0), 'Chapter names are not laid out');
          return `Story header/Back and ${labels.length} fictional story chapter names reflow at 390 px`;
        } finally {
          await storyPage.close();
          if (chapterTestPage) await chapterTestPage.close();
        }
      });
    }
  } finally { await browser.close(); }

  console.log(`Design 008 acceptance · ${baseURL}`);
  for (const result of results) console.log(`${result.status} | ${result.name}${result.detail ? ` | ${result.detail}` : ''}`);
  const failed = results.filter(result => result.status === 'FAIL').length;
  console.log(`Result: ${results.length - failed}/${results.length} checks passed`);
  if (failed) process.exitCode = 1;
}

run().catch(error => {
  console.error(`Acceptance harness error: ${error.stack || error}`);
  process.exitCode = 2;
});
