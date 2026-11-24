require('../test_guard.cjs').enforceTestEnvironment();

// Fictional content only. Web Speech and Media Session are stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const auditionManifest = require('./fixtures/audition_manifest.json');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: auditionManifest }));
    let savedVoice = null;
    await page.route('**/api/voice', route => {
      if (route.request().method() === 'POST') {
        savedVoice = route.request().postDataJSON().voice_name;
        return route.fulfill({ json: { saved: true } });
      }
      return route.fulfill({ json: savedVoice ? { letter: 'A' } : {} });
    });
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      const state = { spoken: [], voices: [], paused: false, speaking: false, actions: {}, media: {} };
      const recordings = [];
      class Utterance { constructor(text) { this.text = text; this.rate = 1; this.voice = null; } }
      class Metadata { constructor(value) { Object.assign(this, value); } }
      class Recording extends EventTarget {
        constructor(src) { super(); this.src = src; this.currentTime = 0; this.duration = 60; this.playbackRate = 1; this.paused = true; recordings.push(this); }
        play() { this.paused = false; this.dispatchEvent(new Event('canplay')); this.dispatchEvent(new Event('playing')); return Promise.resolve(); }
        pause() { this.paused = true; }
      }
      Object.defineProperty(window, 'Audio', { value: Recording, configurable: true });
      Object.defineProperty(window, 'MediaMetadata', { value: Metadata, configurable: true });
      const listeners = new Set();
      const synth = {
        getVoices: () => state.voices,
        addEventListener: (name, callback) => name === 'voiceschanged' && listeners.add(callback),
        removeEventListener: (name, callback) => listeners.delete(callback),
        dispatchVoices: () => listeners.forEach(callback => callback()),
        speak(utterance) { state.spoken.push(utterance); state.speaking = true; queueMicrotask(() => utterance.onstart?.()); },
        pause() { state.paused = true; }, resume() { state.paused = false; state.speaking = true; },
        cancel() { state.paused = false; state.speaking = false; },
      };
      Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: Utterance, configurable: true });
      Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: {
        set metadata(value) { state.media.metadata = value; }, set playbackState(value) { state.media.playbackState = value; },
        setPositionState(value) { state.media.position = value || null; },
        setActionHandler(name, callback) { state.actions[name] = callback; },
      } });
      window.__voiceTest = { state, synth, recordings };
    });
    const setup = await page.evaluate(async () => {
      const { createPlayer, preferredEnglishVoice, cleanSpeechText, splitSpeechText, narrationUnits } = await import('/src/player.js');
      localStorage.setItem('briefing:playback-speed', '1.5');
      const root = document.createElement('div'); root.id = 'voice-player-root'; document.body.append(root);
      const select = document.createElement('select'), speed = document.createElement('select'); document.body.append(select, speed);
      for (const value of [0.75, 1, 1.25, 1.5, 2]) { const option = document.createElement('option'); option.value = String(value); speed.append(option); }
      const player = createPlayer(root, () => {}, () => null);
      const initialSpeed = player.speed;
      window.__voicePlayer = player;
      player.configureVoiceSettings(select, speed, null);
      const state = window.__voiceTest.state;
      state.voices = [
        { name: 'English Novelty Robot', lang: 'en-US', voiceURI: 'novelty' },
        { name: 'English Natural Premium', lang: 'en-US', voiceURI: 'natural', default: true },
        { name: 'French Google', lang: 'fr-FR', voiceURI: 'fr' },
        { name: 'English Standard', lang: 'en-US', voiceURI: 'standard' },
      ];
      window.__voiceTest.synth.dispatchVoices();
      const best = preferredEnglishVoice(state.voices)?.voiceURI;
      speed.value = '1.25'; speed.dispatchEvent(new Event('change'));
      const restoredRoot = document.createElement('div'); document.body.append(restoredRoot);
      const restoredPlayer = createPlayer(restoredRoot, () => {}, () => null);
      player.listen({ id: 'fictional-edition', revision_id: 'r1', kind: 'daily', title: 'Fictional daily edition', local_date: '2026-11-16', narrationTopics: ['Fictional banking'], sections: [
        { id: 'story-1', heading: 'The fictional headline [1]', topic_ids: ['banking'], body: 'The office announced a fictional route [1]. Read details at https://fictional.example/story.\n\nA second short sentence without a citation.' },
        { id: 'story-2', heading: 'A second fictional headline', body: 'The second story is entirely invented.' },
      ] });
      await new Promise(resolve => setTimeout(resolve, 30));
      return { best, speed: player.speed, initialSpeed, restoredSpeed: restoredPlayer.speed,
        prefSpeed: localStorage.getItem('briefing:playback-speed'), citationClean: cleanSpeechText('Claim [1]. Source: https://fictional.example/a'),
        chunks: splitSpeechText('One short sentence. A second short sentence.', 28),
        longChunks: splitSpeechText('This is a long fictional sentence with enough words to require safe utterance boundaries. '.repeat(8)),
        generatedSentence: splitSpeechText('The Federal Reserve Board has announced updated pricing for payment services that Federal Reserve Banks provide to banks and credit unions.'),
        naturalTransitions: cleanSpeechText('What happened: First fact. Why it matters: A short impact.'), activeVoice: player.voice?.voiceURI,
        fallbackUnits: narrationUnits({ id: 'fallback-fixture', sections: [{ id: 'fallback-story', heading: 'A fictional update', body:
          'A fictional service changed its schedule.\n\nLimits: one source only.\nCoverage limited today: no new development.\nCitations: [1].\n\nReaders have time to plan.' }] }).map(unit => unit.text),
        mediaMetadata: state.media.metadata, mediaPosition: state.media.position };
    });
    assert.equal(setup.best, 'natural', 'late voice list chooses the best English natural/premium voice and skips novelty voices');
    assert.equal(setup.activeVoice, 'natural', 'player automatically uses the selected voice');
    assert.equal(setup.speed, 1.25, 'speed control changes player speed');
    assert.equal(setup.prefSpeed, '1.25', 'speed is saved in local settings');
    assert.equal(setup.initialSpeed, 1.5, 'player loads a saved per-device speed');
    assert.equal(setup.restoredSpeed, 1.25, 'a new player restores the speed preference from local storage');
    assert.equal(setup.citationClean, 'Claim.');
    assert.equal(setup.naturalTransitions, 'First fact. Why it matters. A short impact.', 'script section labels become spoken transitions');
    assert.ok(setup.fallbackUnits.some(text => text === 'A fictional service changed its schedule.'), 'fallback story copy remains speakable');
    assert.ok(setup.fallbackUnits.some(text => text === 'Readers have time to plan.'), 'narration resumes after filtered editorial notes');
    assert.equal(setup.fallbackUnits.some(text => /Limits:|Coverage limited|Citations:/i.test(text)), false,
      'fallback Listen never reads Limits, coverage or citation notes');
    assert.equal(setup.chunks.length, 2, 'speech is split at sentence boundaries under the utterance size limit');
    assert.ok(setup.longChunks.every(chunk => chunk.length <= 180), 'long text is bounded to short utterances');
    assert.deepEqual(setup.generatedSentence, ['The Federal Reserve Board has announced updated pricing for payment services that Federal Reserve Banks provide to banks and credit unions.'], 'generated sentences stay intact when they fit a safe utterance');
    assert.equal(setup.mediaMetadata.title, 'Fictional daily edition', 'Media Session exposes the current edition title');
    assert.deepEqual(setup.mediaMetadata.artwork, [{ src: `${baseURL}/content/briefing-artwork.png`, sizes: '1024x1024', type: 'image/png' }], 'Media Session exposes absolute app artwork for supported lock screens');
    assert.ok(setup.mediaPosition.duration > 0 && setup.mediaPosition.position >= 0, 'Media Session publishes the current playhead and duration');
    assert.equal(setup.mediaPosition.playbackRate, 1.25, 'Media Session position follows the selected playback speed');

    await page.waitForFunction(() => window.__voiceTest.state.spoken.length >= 1);
    const outputs = [];
    for (let index = 0; index < 3; index += 1) {
      const priorCount = await page.evaluate(() => window.__voiceTest.state.spoken.length);
      const text = await page.evaluate(() => window.__voiceTest.state.spoken.at(-1).text);
      outputs.push(text);
      await page.evaluate(() => window.__voiceTest.state.spoken.at(-1).onend());
      await page.waitForFunction(count => window.__voiceTest.state.spoken.length > count, priorCount);
    }
    const speechState = await page.evaluate(() => ({
      outputs: [], media: window.__voiceTest.state.media, actions: Object.keys(window.__voiceTest.state.actions),
      currentText: window.__voiceTest.state.spoken.at(-1).text,
    }));
    outputs.push(speechState.currentText);
    await page.evaluate(() => {
      window.__voiceTest.state.spoken.at(-1).onerror({ error: 'interrupted' });
      window.__voiceTest.state.speaking = false;
    });
    assert.match(outputs[0], /November 16/, 'edition opening says the date like a host');
    assert.match(outputs[0], /Fictional banking/, 'edition opening introduces topics');
    assert.equal(outputs[1], 'The fictional headline.', 'story headline is spoken without citation markers');
    assert.equal(outputs[2], 'The office announced a fictional route.', 'URLs and citation markers are omitted from spoken text');
    assert.ok(speechState.actions.includes('play') && speechState.actions.includes('pause') && speechState.actions.includes('nexttrack')
      && speechState.actions.includes('previoustrack') && speechState.actions.includes('seekto'), 'Media Session play, pause, chapter skip, and seek handlers are registered');
    assert.ok(speechState.media.playbackState, 'Media Session playback state is maintained');
    await page.waitForFunction(() => window.__voicePlayer.status === 'playing', null, { timeout: 5000 });
    assert.ok(await page.evaluate(() => window.__voiceTest.state.spoken.length >= 5), 'speech continues after a transient interruption');
    await page.evaluate(() => window.__voiceTest.state.actions.pause());
    assert.equal(await page.evaluate(() => window.__voicePlayer.status), 'paused', 'Media Session pause pauses speech');
    await page.evaluate(() => window.__voiceTest.state.actions.play());
    await page.waitForFunction(() => window.__voicePlayer.status === 'playing');
    const beforeSkip = await page.evaluate(() => window.__voiceTest.state.spoken.length);
    await page.evaluate(() => window.__voiceTest.state.actions.nexttrack());
    await page.waitForFunction(count => window.__voiceTest.state.spoken.length > count, beforeSkip);
    assert.equal(await page.evaluate(() => window.__voiceTest.state.spoken.at(-1).text), 'Next story: A second fictional headline.', 'Media Session skip advances to and speaks the next story');
    const beforePrevious = await page.evaluate(() => window.__voiceTest.state.spoken.length);
    await page.evaluate(() => window.__voiceTest.state.actions.previoustrack());
    await page.waitForFunction(count => window.__voiceTest.state.spoken.length > count, beforePrevious);
    assert.equal(await page.evaluate(() => window.__voiceTest.state.spoken.at(-1).text), 'The fictional headline.', 'Media Session previous chapter returns to and speaks the first story');
    await page.evaluate(() => window.__voiceTest.state.actions.seekto({ seekTime: 1 }));
    await page.waitForFunction(() => window.__voicePlayer.status === 'playing');
    assert.equal(await page.evaluate(() => window.__voicePlayer.status), 'playing', 'Media Session seek keeps active speech playing');
    assert.ok(Math.abs(await page.evaluate(() => window.__voiceTest.state.media.position.position) - 1) < 0.25,
      'Media Session seekto moves the published playhead to the requested time');
    await page.locator('#voice-player-root .mini-player-open').click();
    assert.equal(await page.getByRole('button', { name: 'Minimize' }).count(), 1, 'full-player minimize control is an accessible icon button');
    assert.equal(await page.locator('.player-voice-label').innerText(), 'Voice: Sulafat', 'full player identifies the selected narrator sample');
    assert.equal(await page.locator('.mini-player .player-voice-label').count(), 0, 'mini player does not repeat the voice label');
    assert.equal(await page.locator('[data-player-speed]').count(), 5, 'full-player exposes all five speed choices');
    assert.equal(await page.locator('.player-speeds').evaluate(element => getComputedStyle(element).flexWrap), 'nowrap', 'full-player speed choices stay in one row');
    await page.getByRole('button', { name: 'Minimize' }).click();
    assert.equal(await page.locator('#voice-player-root .mini-player-open').evaluate(element => element.matches(':focus-visible')), false, 'mouse tap does not leave a focus ring on the mini-player title');
    await page.evaluate(() => { location.hash = '#preferences'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
    await page.getByRole('heading', { name: 'Choose your voice' }).waitFor();
    await page.locator('.narrator-sample').first().waitFor();
    assert.equal(await page.locator('.narrator-sample').count(), 6, 'Settings lists six audition samples');
    await page.locator('.narrator-sample').first().getByRole('button', { name: 'Use this voice' }).click();
    await page.getByRole('heading', { name: 'Narrator' }).waitFor();
    await page.getByRole('status').filter({ hasText: 'Sulafat selected.' }).waitFor();
    assert.equal(savedVoice, 'FixtureVoiceA', 'first-run selection saves the chosen audition voice');
    await page.locator('#voice-speed').selectOption('1.25');
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:playback-speed')), '1.25', 'Settings saves the selected listening speed');
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.evaluate(() => { location.hash = '#today'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
    const playToggle = page.locator('#voice-player-root .mini-player-toggle').first();
    const restingColors = await playToggle.evaluate(element => {
      const style = getComputedStyle(element); return [style.backgroundColor, style.color];
    });
    await playToggle.hover();
    await page.mouse.down();
    const pressedState = await playToggle.evaluate(element => {
      const style = getComputedStyle(element); return { active: element.matches(':active'), colors: [style.backgroundColor, style.color], opacity: style.opacity, transform: style.transform };
    });
    assert.equal(pressedState.active, true, 'the playback button receives a pressed state');
    assert.deepEqual(pressedState.colors, restingColors, 'press feedback never changes the dark play-circle colors');
    assert.equal(pressedState.opacity, '0.82', 'pressed feedback uses opacity without changing theme colors');
    assert.notEqual(pressedState.transform, 'none', 'pressed feedback uses a small transform');
    if (process.env.BRIEFING_SHOT_DIR) {
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, 'player-pressed-dark.png') });
    }
    await page.mouse.move(0, 0); await page.mouse.up();
    await page.emulateMedia({ colorScheme: 'light' });
    await playToggle.hover(); await page.mouse.down();
    if (process.env.BRIEFING_SHOT_DIR) await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, 'player-pressed-light.png') });
    await page.mouse.move(0, 0); await page.mouse.up();
    const darkContrast = await playToggle.evaluate(element => {
      const parse = value => value.match(/[\d.]+/g).slice(0, 3).map(Number).map(channel => channel / 255 <= 0.04045 ? channel / 3294.6 : ((channel / 255 + 0.055) / 1.055) ** 2.4);
      const [a, b] = [parse(getComputedStyle(element).backgroundColor), parse(getComputedStyle(element).color)];
      const lum = rgb => 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
      const [lighter, darker] = [lum(a), lum(b)].sort((x, y) => y - x); return (lighter + 0.05) / (darker + 0.05);
    });
    assert.ok(darkContrast >= 3, `dark row play circle contrast ${darkContrast.toFixed(2)}:1 meets 3:1`);
    console.log(`Dark row play circle contrast: ${darkContrast.toFixed(2)}:1 (minimum 3:1).`);
    const blockedStorageSpeed = await page.evaluate(async () => {
      const originalGet = Storage.prototype.getItem;
      const originalSet = Storage.prototype.setItem;
      Storage.prototype.getItem = function (key) { if (key === 'briefing:playback-speed') throw new Error('storage unavailable'); return originalGet.call(this, key); };
      const { createPlayer } = await import('/src/player.js');
      const root = document.createElement('div'); document.body.append(root);
      const player = createPlayer(root, () => {}, () => null);
      const fallbackSpeed = player.speed;
      Storage.prototype.setItem = function (key, value) { if (key === 'briefing:playback-speed') throw new Error('storage unavailable'); return originalSet.call(this, key, value); };
      const select = document.createElement('select');
      for (const value of [0.75, 1, 1.25, 1.5, 2]) { const option = document.createElement('option'); option.value = String(value); select.append(option); }
      player.configureVoiceSettings(null, select, null);
      select.value = '1.5';
      select.dispatchEvent(new Event('change'));
      Storage.prototype.getItem = originalGet; Storage.prototype.setItem = originalSet;
      return { fallbackSpeed, selectedSpeed: player.speed };
    });
    assert.deepEqual(blockedStorageSpeed, { fallbackSpeed: 1, selectedSpeed: 1.5 }, 'player works when per-device speed storage is unavailable');
    const backgroundRecording = await page.evaluate(async () => {
      const { createPlayer } = await import('/src/player.js');
      window.__voicePlayer.close();
      const root = document.createElement('div'); document.body.append(root);
      const player = createPlayer(root, () => {}, () => null);
      player.playRecording({ id: 'recorded-fixture', revision_id: 'r1', script_hash: 'fictional-recorded-script-r1', kind: 'episode',
        title: 'Fictional recorded briefing', audio: { revision_id: 'r1', script_hash: 'fictional-recorded-script-r1',
          url: '/content/fictional-audio.mp3', duration_seconds: 60 },
        chapters: [
          { id: 'chapter-one', title: 'First chapter', start_seconds: 0, end_seconds: 30 },
          { id: 'chapter-two', title: 'Second chapter', start_seconds: 30, end_seconds: 60 },
        ] });
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
      return { status: player.status, audioPaused: window.__voiceTest.recordings.at(-1).paused,
        mediaState: window.__voiceTest.state.media.playbackState, actions: Object.keys(window.__voiceTest.state.actions) };
    });
    assert.equal(backgroundRecording.status, 'playing', 'recorded audio keeps playing after its tab is backgrounded');
    assert.equal(backgroundRecording.audioPaused, false, 'backgrounding does not pause the media element');
    assert.equal(backgroundRecording.mediaState, 'playing', 'Media Session stays in the playing state in background');
    assert.ok(backgroundRecording.actions.includes('play') && backgroundRecording.actions.includes('pause'),
      'lock-screen Media Session play and pause controls remain registered');
    await page.evaluate(() => window.__voiceTest.state.actions.pause());
    assert.equal(await page.evaluate(() => window.__voiceTest.state.media.playbackState), 'paused',
      'background Media Session pause updates the recorded player state');
    await page.evaluate(() => window.__voiceTest.state.actions.play());
    assert.equal(await page.evaluate(() => window.__voiceTest.state.media.playbackState), 'playing',
      'background Media Session play resumes recorded audio');
    await page.evaluate(() => window.__voiceTest.state.actions.seekforward());
    assert.ok(Math.abs(await page.evaluate(() => window.__voiceTest.recordings.at(-1).currentTime) - 15) < 0.25,
      'Media Session forward seek defaults to fifteen seconds');
    await page.evaluate(() => window.__voiceTest.state.actions.seekbackward());
    assert.ok(await page.evaluate(() => window.__voiceTest.recordings.at(-1).currentTime) < 0.25,
      'Media Session backward seek defaults to fifteen seconds and clamps at zero');
    await page.evaluate(() => window.__voiceTest.state.actions.nexttrack());
    assert.ok(Math.abs(await page.evaluate(() => window.__voiceTest.recordings.at(-1).currentTime) - 30) < 0.25,
      'Media Session next chapter moves to the next recorded chapter');
    await page.evaluate(() => window.__voiceTest.state.actions.previoustrack());
    assert.ok(await page.evaluate(() => window.__voiceTest.recordings.at(-1).currentTime) < 0.25,
      'Media Session previous chapter moves to the previous recorded chapter');
    await page.close();

    // A real browser smoke test: report availability and request speech only when the runtime exposes it.
    const smoke = await browser.newPage();
    await smoke.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    const real = await smoke.evaluate(() => ({ available: Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance), voices: window.speechSynthesis?.getVoices?.().length || 0 }));
    if (real.available) {
      const result = await smoke.evaluate(() => new Promise(resolve => {
        const utterance = new SpeechSynthesisUtterance('Briefing speech smoke check.');
        let done = false; const finish = status => { if (done) return; done = true; window.speechSynthesis.cancel(); resolve(status); };
        utterance.onstart = () => finish('started'); utterance.onerror = event => finish(`error:${event.error}`);
        window.speechSynthesis.speak(utterance); setTimeout(() => finish('timeout'), 4000);
      }));
      console.log(`Real browser speech smoke: available=${real.available}, voices=${real.voices}, result=${result}`);
    } else console.log('Real browser speech smoke: SpeechSynthesis unavailable in this headless runtime; stubbed browser coverage passed.');
    await smoke.close();
    console.log('player voice: narrator label, playback speed, speech cleanup/chunking, interruptions and Media Session passed.');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
