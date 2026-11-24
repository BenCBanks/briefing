require('../test_guard.cjs').enforceTestEnvironment();

// Fictional content only. Speech synthesis is stubbed so no audio or voice provider runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const captureDir = process.env.BRIEFING_CAPTURE_DIR;

async function captureLargeText(page, name) {
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  const result = await page.evaluate(() => {
    const controls = [...document.querySelectorAll('main button, main a[href], main summary, .player-dialog button, .player-dialog a[href], .mini-player button')]
      .filter(element => element.getClientRects().length && !element.disabled)
      .map(element => ({ label: (element.innerText || element.getAttribute('aria-label') || '').trim(),
        width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }));
    const clipped = [...document.querySelectorAll('main :is(h1,h2,h3,p,a,button,span,strong,small),.mini-player :is(span,strong,small),.player-dialog :is(h1,h2,h3,p,button,span,strong,small)')]
      .filter(element => element.textContent.trim() && element.getClientRects().length)
      .filter(element => {
        const style = getComputedStyle(element);
        return style.textOverflow === 'ellipsis' ||
          (['hidden', 'clip'].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1) ||
          (['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1);
      }).map(element => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')} clips text`);
    const readingLines = [...document.querySelectorAll('main .edition-section>p:not(.section-kicker),main .story-lead p')]
      .filter(element => element.getClientRects().length)
      .map(element => ({ fontSize: parseFloat(getComputedStyle(element).fontSize), lineHeight: parseFloat(getComputedStyle(element).lineHeight) }));
    return { root: parseFloat(getComputedStyle(document.documentElement).fontSize),
      width: document.documentElement.scrollWidth, viewport: innerWidth, controls, clipped, readingLines };
  });
  assert.ok(result.root >= 32, `${name}: 200% text scale applied`);
  assert.ok(result.width <= result.viewport, `${name}: no horizontal overflow (${result.width}/${result.viewport})`);
  assert.deepEqual(result.clipped, [], `${name}: text wraps: ${result.clipped.join('; ')}`);
  assert.ok(result.readingLines.every(line => line.lineHeight >= line.fontSize * 1.35),
    `${name}: reading line spacing scales with large type: ${JSON.stringify(result.readingLines)}`);
  const small = result.controls.filter(control => control.width < 44 || control.height < 44);
  assert.deepEqual(small, [], `${name}: visible controls meet 44px targets: ${JSON.stringify(small)}`);
  if (captureDir) {
    fs.mkdirSync(captureDir, { recursive: true });
    await page.screenshot({ path: path.join(captureDir, `${name}-large-text-light.png`) });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(captureDir, `${name}-large-text-dark.png`) });
    await page.emulateMedia({ colorScheme: 'light' });
  }
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
}

async function installSpeechStub(page) {
  await page.addInitScript(() => {
    const state = { spoken: [], paused: false, speaking: false, cancelCount: 0 };
    const voices = [
      { name: 'Natural English', lang: 'en-US', voiceURI: 'natural-en', default: true },
      { name: 'English Standard', lang: 'en-GB', voiceURI: 'standard-en', default: false },
    ];
    class StubUtterance {
      constructor(text) { this.text = text; this.rate = 1; }
    }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: StubUtterance, configurable: true });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      getVoices() { return voices; },
      get paused() { return state.paused; }, get speaking() { return state.speaking; },
      speak(utterance) { state.spoken.push(utterance); state.speaking = true; setTimeout(() => utterance.onstart?.(), 0); },
      pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; },
      cancel() { state.cancelCount += 1; state.paused = false; state.speaking = false; },
    } });
    window.__speech = state;
  });
}

async function run() {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    let page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.on('pageerror', error => console.error('Player page error:', error.message));
    page.on('requestfailed', request => console.error('Player request failed:', request.url(), request.failure()?.errorText));
    await installSpeechStub(page);
    await page.route('**/api/profile', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    // Keep this player-specific flow on the checked-in four-story sample;
    // active-profile editions are covered by the generated-edition suites.
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-listen-pill').waitFor({ timeout: 10000 }).catch(async error => {
      console.error('Player Today did not render:', (await page.locator('body').innerText()).slice(0, 1200)); throw error;
    });
    assert.equal(await page.locator('.today-listen-pill').count(), 1, 'Today has the equal Listen control');
    assert.equal(await page.locator('.prototype-voice-label').count(), 0, 'No prototype-voice wording is exposed');
    const play = page.locator('.today-listen-pill');
    assert.equal(await play.count(), 1, 'pending recording has a working Listen control');
    const editionId = await play.getAttribute('data-edition-id');
    const firstStoryId = (await page.locator('.today-story-row').first().getAttribute('id')).replace(/^story-/u, '');
    await page.evaluate(({ id, sectionId }) => { location.hash = `#edition/daily/${encodeURIComponent(id)}/${encodeURIComponent(sectionId)}`; }, { id: editionId, sectionId: firstStoryId });
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.story-listen-label').innerText(), 'Listen from here', 'story action is clearly labelled Listen from here');
    const storyParagraphs = page.locator('.edition-section p');
    assert.ok(await storyParagraphs.count() > 1, 'fixture story includes a later paragraph to resume from');
    await storyParagraphs.last().evaluate(paragraph => paragraph.scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.waitForFunction(id => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress[id]?.paragraphIndex > 0, editionId);
    const expectedIndex = await page.evaluate(id => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress[id].paragraphIndex, editionId);
    const expectedParagraph = await page.evaluate(async ({id,index}) => {const {narrationUnits}=await import('/src/player.js'); const {editions}=await (await fetch('/content/editions.json')).json(); const item=editions.find(x=>x.id===id && !x.superseded_by); return narrationUnits(item).filter(x=>!x.intro&&!x.transition&&x.chapterIndex===0&&x.paragraphIndex===index).map(x=>x.text).join(' ');},{id:editionId,index:expectedIndex});
    await page.locator('.story-listen').click();
    await page.locator('.spoken-paragraph').waitFor();
    await page.locator('.mini-player').waitFor();
    await captureLargeText(page, 'mini-player');
    const readingPlace = await page.evaluate(id => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress[id], editionId);
    assert.equal(readingPlace.mode, 'listen', 'Listening writes the shared story position');
    assert.equal(readingPlace.paragraphIndex, expectedIndex, 'The highlighted story paragraph is exact');
    const readerCount = (await page.locator('.story-progress').innerText()).match(/of (\d+)/)?.[1];
    assert.ok(readerCount, 'reader shows its current story count');
    assert.match(await page.locator('.mini-player-open small').innerText(), new RegExp(`Story \\d+ of ${readerCount}`), 'mini-player uses the same visible story count as the reader');
    const speechParts = await page.evaluate(async text => {
      const { cleanSpeechText, splitSpeechText } = await import('/src/player.js');
      return splitSpeechText(cleanSpeechText(text));
    }, expectedParagraph);
    await page.waitForFunction(() => window.__speech.spoken.length > 0);
    await page.evaluate(() => window.__speech.spoken.at(-1).onend?.()); // finish the spoken section heading
    await page.waitForFunction(parts => window.__speech.spoken.some(item => parts.includes(item.text)), speechParts);
    await page.waitForTimeout(350); await page.evaluate(() => window.scrollBy(0, 450));
    await page.waitForFunction(() => { const chip = document.querySelector('.back-to-audio'); return chip && !chip.hidden; });
    assert.ok(await page.evaluate(() => document.querySelector('.back-to-audio').getBoundingClientRect().bottom < document.querySelector('.mini-player').getBoundingClientRect().top),
      'Back to audio chip sits above the pinned mini-player');
    await page.close();
    page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.stack));
    await installSpeechStub(page);
    await page.route('**/api/profile', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-listen-pill').waitFor({ timeout: 10000 }).catch(async error => {
      console.error('Player Today did not render:', (await page.locator('body').innerText()).slice(0, 1200)); throw error;
    });
    await page.locator('.today-page').waitFor();
    await page.locator('.today-listen-pill').click();
    await page.waitForFunction(() => window.__speech.spoken.length > 0);
    const firstText = await page.locator('.edition-section').count();
    assert.equal(firstText, 4, 'Today player retains all inline stories');
    const spoken = await page.evaluate(() => window.__speech.spoken[0].text);
    assert.match(spoken, /Here's your briefing for/, 'edition narration opens with a spoken date');
    assert.match(spoken, /Today we're following/, 'edition narration introduces its topic mix');
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    await page.waitForFunction(() => document.querySelector('.today-listen-pill .mode-label')?.textContent === 'Playing · tap to pause');
    await page.locator('.mini-player-toggle').click();
    await page.waitForFunction(() => !document.querySelector('.mini-player'));
    assert.match(await page.locator('.today-listen-pill').innerText(), /Resume/);
    assert.ok(await page.evaluate(id => JSON.parse(localStorage.getItem('briefing:v1:state')).progress[id]?.seconds > 0, editionId), 'pause saves a local listening position');
    assert.equal((await page.evaluate(id => JSON.parse(localStorage.getItem('briefing:v1:state')).readingProgress[id].mode, editionId)), 'listen', 'pausing preserves listen mode in the shared position');
    await page.locator('.today-listen-pill').click(); await page.locator('.mini-player-open').click();
    await page.locator('[data-player-toggle]').click();
    assert.equal(await page.locator('.player-voice-label').innerText(), 'Voice: Sulafat');
    assert.equal(await page.locator('[data-player-toggle] svg').count(), 1, 'full player uses an SVG glyph');
    assert.equal(await page.getByRole('heading', { name: 'Now playing' }).count(), 1, 'full player has a Now playing queue heading');
    assert.equal(await page.getByRole('heading', { name: 'Playing next' }).count(), 1, 'full player has a Playing next queue heading');
    assert.match(await page.locator('[data-player-now-title]').innerText(), /./, 'active edition chapter is named in the queue');
    assert.match(await page.locator('[data-player-now-length]').innerText(), /^\d+ min left$/, 'active chapter reports remaining listening time');
    assert.ok(await page.locator('.player-queue-row').count() > 0, 'queue lists the edition stories that follow the current chapter');
    assert.match(await page.locator('.player-queue-row').first().innerText(), /about \d+ min/, 'next stories show length estimates');
    assert.equal(await page.locator('.player-voice-label').innerText(), 'Voice: Sulafat', 'full player identifies the active narrator sample');
    assert.equal(await page.locator('[data-player-voice]').count(), 0, 'full player does not expose a separate browser voice selector');
    const playerSeek = page.locator('[data-player-seek]');
    await playerSeek.evaluate(input => { input.value = '30'; input.dispatchEvent(new Event('change', { bubbles: true })); });
    assert.equal(await page.locator('[data-player-elapsed]').innerText(), '0:30', 'seek control sets a readable listening position');
    await page.locator('[data-player-skip="-15"]').click();
    assert.equal(await page.locator('[data-player-elapsed]').innerText(), '0:15', 'Back 15 seconds moves the same edition position back');
    if (captureDir) { fs.mkdirSync(captureDir, { recursive: true }); await page.screenshot({ path: path.join(captureDir, 'listen-queue-phone.png') }); }
    await captureLargeText(page, 'player');
    const queuedTitle = await page.locator('.player-queue-row').first().locator('strong').innerText();
    assert.notEqual(await page.locator('[data-player-now-title]').innerText(), queuedTitle, 'queue row is a distinct upcoming chapter');
    await page.locator('.player-queue-row').first().click();
    await page.waitForFunction(title => document.querySelector('[data-player-now-title]')?.textContent === title, queuedTitle);
    await page.waitForTimeout(100);
    const queuePlayback = await page.evaluate(() => ({ spoken: window.__speech.spoken.length, canceled: window.__speech.cancelCount,
      status: document.querySelector('[data-player-toggle]')?.getAttribute('aria-label'), phase: document.querySelector('[data-player-phase]')?.textContent,
      active: document.querySelector('[data-player-now-title]')?.textContent }));
    queuePlayback.pageErrors = pageErrors;
    assert.deepEqual(pageErrors, [], 'queue controls do not throw while updating chapter state');
    assert.ok(queuePlayback.spoken > 1, `choosing a queued story begins that chapter in the same edition (${JSON.stringify(queuePlayback)})`);
    await page.getByRole('button', { name: 'Chapters' }).click();
    assert.ok(await page.locator('[data-player-chapter-list] button').count() > 1, 'edition exposes one chapter per story');
    await page.locator('[data-player-back]').click();
    await page.locator('[data-player-speed="2"]').click();
    await page.locator('[data-player-toggle]').click();
    await page.waitForFunction(() => window.__speech.spoken.some(item => item.rate === 2));
    await page.evaluate(id => window.dispatchEvent(new CustomEvent('briefing:material-correction', { detail: { id } })), editionId);
    await page.waitForFunction(() => window.__speech.cancelCount > 0 && !document.querySelector('.mini-player-toggle:not(:disabled)'));
    assert.equal(await page.locator('.today-listen-pill').isDisabled(), true, 'material correction stops synthesis and disables Listen');
    assert.match(await page.locator('.today-listen-pill').innerText(), /Audio paused/);
    const state = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:state')));
    assert.equal(state.progress[editionId], undefined, 'material correction clears the saved position');
    await page.evaluate(async () => {
      window.__speech.spoken.length = 0;
      const [{ createPlayer }, signals] = await Promise.all([import('/src/player.js'), import('/src/interests/signals.js')]);
      signals.clear();
      const root = document.createElement('div'); root.id = 'replay-player-root'; document.body.append(root);
      window.__replayTest = createPlayer(root, () => {}, () => null, () => {}, () => ({ available: false }), () => 'Read along',
        () => signals.record('replay', 'voice-story', 'payments'));
      window.__replayTest.play({ id: 'voice-item', title: 'A fictional short item', revision_id: 'fictional-r1', kind: 'daily',
        sections: [{ id: 'voice-story', heading: 'A short fictional headline', body: 'A brief sample sentence.' }] });
    });
    await page.waitForFunction(() => window.__speech.spoken.length === 1);
    await page.evaluate(() => window.__speech.spoken.at(-1).onend?.());
    await page.waitForFunction(() => window.__speech.spoken.length === 2);
    await page.evaluate(() => window.__speech.spoken.at(-1).onend?.());
    await page.waitForFunction(() => window.__replayTest.status === 'complete');
    assert.equal(await page.locator('#replay-player-root .mini-player').count(), 0, 'Finished playback hides the player');
    await page.evaluate(() => window.__replayTest.play(window.__replayTest.item));
    await page.waitForFunction(() => window.__speech.spoken.length === 3);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')).at(-1).type), 'replay', 'replay event is emitted when a finished item restarts');
    await page.close();
    console.log('listening: exact paragraphs, player disclosure, pause/resume, chapters, speed, saved position and correction stop passed.');
  } finally { await browser.close(); }
}

async function runRevisionFixture() {
  const { isCurrentRecordingEvent, recordingSession } = await import('../../src/player.js');
  const oldRevision = {
    id: 'fictional-daily', revision_id: 'fictional-r1', script_hash: 'fictional-script-r1',
    audio: { revision_id: 'fictional-r1', script_hash: 'fictional-script-r1',
      url: '/audio/fictional-r1.wav', voice_name: 'Kore' },
    chapters: [
      { id: 'opening', title: 'Opening', start_seconds: 0, end_seconds: 42 },
      { id: 'markets', title: 'Markets', start_seconds: 42, end_seconds: 130 },
    ],
  };
  const correctedRevision = {
    ...oldRevision,
    revision_id: 'fictional-r2',
    script_hash: 'fictional-script-r2',
    audio: { revision_id: 'fictional-r2', script_hash: 'fictional-script-r2',
      url: '/audio/fictional-r2.wav', voice_name: 'Kore' },
    chapters: [
      { id: 'opening', title: 'Opening', start_seconds: 0, end_seconds: 29 },
      { id: 'markets', title: 'Markets', start_seconds: 29, end_seconds: 101 },
    ],
  };
  const oldSession = recordingSession(oldRevision, null, 'markets');
  assert.equal(oldSession.chapterIndex, 1);
  assert.equal(oldSession.startSeconds, 42);
  const revisedSession = recordingSession(correctedRevision, oldSession.item, 'markets');
  assert.equal(revisedSession.reusable, false, 'a changed revision replaces the old recording');
  assert.equal(revisedSession.item.revision_id, 'fictional-r2');
  assert.equal(revisedSession.item.audio.url, '/audio/fictional-r2.wav');
  assert.equal(revisedSession.chapterIndex, 1);
  assert.equal(revisedSession.startSeconds, 29, 'chapter seek uses the corrected revision offset');
  assert.equal(revisedSession.item.sections[1].start_seconds, 29);
  assert.equal(revisedSession.item.sections[1].end_seconds, 101);
  const unchangedSession = recordingSession(correctedRevision, revisedSession.item, 'markets');
  assert.equal(unchangedSession.reusable, true, 'an identical revision can keep its recording');
  assert.equal(unchangedSession.startSeconds, 29);
  const previousScriptSameRevision = { ...correctedRevision, script_hash: 'fictional-prior-script-r2',
    audio: { ...correctedRevision.audio, script_hash: 'fictional-prior-script-r2' } };
  assert.equal(recordingSession(correctedRevision, previousScriptSameRevision, 'markets').reusable,
    false, 'a changed script hash cannot resume a recording with the same revision, voice, and URL');
  const staleManifest = { ...correctedRevision,
    audio: { ...correctedRevision.audio, script_hash: 'fictional-script-r1' } };
  assert.equal(recordingSession(staleManifest, revisedSession.item, 'markets').reusable,
    false, 'audio metadata with an obsolete script hash cannot be reused for corrected text');
  assert.equal(recordingSession(staleManifest, revisedSession.item, 'markets').playable,
    false, 'audio metadata with an obsolete script hash cannot start playback');
  const staleRevisionManifest = { ...correctedRevision,
    audio: { ...correctedRevision.audio, revision_id: 'fictional-r1' } };
  assert.equal(recordingSession(staleRevisionManifest, revisedSession.item, 'markets').reusable,
    false, 'audio metadata from the prior revision cannot be reused for corrected text');
  assert.equal(recordingSession(staleRevisionManifest, revisedSession.item, 'markets').playable,
    false, 'audio metadata from the prior revision cannot start playback');
  const noVoiceRecording = { ...correctedRevision,
    audio: { revision_id: 'fictional-r2', script_hash: 'fictional-script-r2',
      url: '/audio/fictional-r2-human.wav' } };
  assert.equal(recordingSession(noVoiceRecording, noVoiceRecording, 'markets').reusable,
    true, 'a valid human recording without voice metadata can reuse its current asset');
  const rebuiltAudio = { ...correctedRevision,
    audio: { ...correctedRevision.audio, url: '/audio/fictional-r2-rebuilt.wav' } };
  assert.equal(recordingSession(rebuiltAudio, revisedSession.item, 'markets').reusable,
    false, 'a changed asset URL replaces audio on the same revision');
  const changedVoice = { ...correctedRevision,
    audio: { ...correctedRevision.audio, voice_name: 'Orus' } };
  assert.equal(recordingSession(changedVoice, revisedSession.item, 'markets').reusable,
    false, 'a changed narrator replaces audio on the same revision');
  const missingId = { ...correctedRevision, id: '' };
  assert.equal(recordingSession(missingId, missingId, 'markets').reusable, false,
    'an item without an identity cannot reuse a recording');
  const oldAudioElement = {};
  const revisedAudioElement = {};
  assert.equal(isCurrentRecordingEvent(revisedAudioElement, oldAudioElement, true), false,
    'events from the previous recording cannot update the revised session');
  assert.equal(isCurrentRecordingEvent(revisedAudioElement, revisedAudioElement, true), true);
  assert.equal(isCurrentRecordingEvent(revisedAudioElement, revisedAudioElement, false), false);
  // Exercise the actual player entry point, not only recordingSession's selector.
  const { createPlayer } = await import('../../src/player.js');
  const descriptors = new Map(['window', 'document', 'navigator', 'localStorage', 'Audio', 'setInterval',
    'HTMLElement', 'requestAnimationFrame']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const opened = [];
  // Minimal dialog plumbing only; visual layout and real device playback are separate checks.
  class FixtureElement {
    constructor() { this.dataset = {}; this.nodes = new Map(); this.listeners = new Map(); }
    querySelector(selector) {
      if (!this.nodes.has(selector)) this.nodes.set(selector, new FixtureElement());
      return this.nodes.get(selector);
    }
    querySelectorAll() { return []; }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    setAttribute() {} append() {} replaceChildren() {} remove() {} focus() {}
    showModal() { this.open = true; }
    close() { this.open = false; this.listeners.get('close')?.(); }
  }
  const announcement = { textContent: '', setAttribute() {} };
  const root = { querySelector: selector => selector === '.player-announcement' ? announcement : null,
    append() {} };
  try {
    const globals = {
      window: { addEventListener() {} },
      document: { addEventListener() {}, hidden: false, createElement: () => new FixtureElement(),
        querySelector: () => null }, navigator: {},
      HTMLElement: FixtureElement, requestAnimationFrame: callback => callback(),
      localStorage: { getItem: () => null, setItem() {} }, setInterval: () => 0,
      Audio: class { constructor(url) { this.url = url; this.currentTime = 0; this.plays = 0; opened.push(this); }
        addEventListener() {} play() { this.plays += 1; return Promise.resolve(); } pause() {} },
    };
    for (const [key, value] of Object.entries(globals))
      Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    const player = createPlayer(root, () => {}, () => null);
    const current = { ...correctedRevision, kind: 'episode', audio_status: 'recorded',
      audio: { ...correctedRevision.audio, status: 'recorded' } };
    const resolved = { severity: 'material', previous_revision_id: 'fictional-r0',
      revision_id: 'fictional-r1', audio_status: 'recorded' };
    player.play({ ...current, corrections: [resolved] });
    assert.equal(player.item, null, 'recording exemption must not relax the speech selection guard');
    for (const item of [
      { ...current, corrections: [{ ...resolved, revision_id: current.revision_id, audio_status: 'regenerating' }] },
      { ...current, corrections: [{ ...resolved, previous_revision_id: current.revision_id,
        revision_id: 'fictional-r3', audio_status: 'regenerating' }] },
      { ...current, corrections: [{ ...resolved, previous_revision_id: current.revision_id,
        revision_id: 'fictional-r3', audio_status: 'recorded' }] },
      { ...current, corrections: [{ severity: 'material', audio_status: 'regenerating' }] },
      { ...current, corrections: [{ severity: 'material', audio_status: 'recorded' }] },
      { ...current, corrections: [{ ...resolved, previous_revision_id: undefined }] },
      { ...current, corrections: [{ ...resolved, previous_revision_id: resolved.revision_id }] },
      { ...current, corrections: [resolved], audio: { ...current.audio, revision_id: 'fictional-r1' } },
      { ...current, corrections: [resolved], audio: { ...current.audio, script_hash: 'fictional-old-hash' } },
      { ...current, corrections: [resolved], audio: { ...current.audio, status: 'review_pending' } },
      { ...current, corrections: [resolved], audio_status: 'regenerating' },
      { ...current, corrections: [resolved], audio: { ...current.audio, state: 'superseded' } },
      { ...current, corrections: [resolved], audio: { ...current.audio, superseded: true } },
    ]) {
      const before = opened.length;
      player.playRecording(item);
      assert.equal(opened.length, before, 'unresolved or stale correction must not open audio');
    }
    for (const correction of [resolved, { ...resolved, revision_id: current.revision_id }]) {
      for (const section_ids of [undefined, ['opening']]) {
        player.close();
        const before = opened.length;
        const item = { ...current, corrections: [{ ...correction, section_ids }] };
        player.playRecording(item);
        assert.equal(opened.length, before + 1,
          'resolved correction must allow newly approved exact-current recording');
        assert.equal(player.item.audio.url, current.audio.url);
        assert.equal(opened.at(-1).plays, 1, 'current recording reaches play');
        player.playRecording(item);
        assert.equal(opened.length, before + 1, 'same current recording is reused');
        assert.equal(opened.at(-1).plays, 2, 'resolved recording remains playable through toggle guard');
      }
    }
    player.close();
  } finally {
    for (const [key, descriptor] of descriptors)
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
  }
  console.log('player revision fixture: current audio URL and revised chapter offsets passed.');
}

if (process.env.BRIEFING_PLAYER_FIXTURE_ONLY === '1') {
  runRevisionFixture().catch(error => { console.error(error); process.exitCode = 1; });
} else {
  run().catch(error => { console.error(error); process.exitCode = 1; });
}
