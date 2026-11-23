require('../test_guard.cjs').enforceTestEnvironment();

// Fixture-only preview safety and page performance check. Measures the Python
// preview process only; Chromium is a separate test client and not under its cap.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const net = require('node:net');
const { performance } = require('node:perf_hooks');
let chromium = null;
try { ({ chromium } = require('playwright')); } catch { /* API/RSS checks do not require a browser install. */ }
const { DEFAULT_PROFILE, builtProfileKey } = require('./helpers/profile.cjs');

const ROOT = path.resolve(__dirname, '../..');
const RSS_CAP_KIB = 96 * 1024;
const TABLE = [];

function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function profileId(profile) {
  return crypto.createHash('sha256').update(stable(profile)).digest('hex').slice(0, 24);
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, `${JSON.stringify(value)}\n`, { mode: 0o600 });
}

function seedState(stateDir) {
  const profile = structuredClone(DEFAULT_PROFILE);
  const id = profileId(profile);
  writeJson(path.join(stateDir, 'profile.json'), profile);
  writeJson(path.join(stateDir, 'status.json'), {
    last_run_at: new Date().toISOString(), ok: true, items_fetched: 5,
    stories_written: 1, feeds_disabled: [], reason: null,
  });
  const edition = structuredClone(require('./fixtures/legacy-state/generated/editions.json').editions[0]);
  const sources = structuredClone(require('./fixtures/legacy-state/generated/sources.json').sources);
  edition.id = 'fixture-edition-perf';
  edition.revision_id = 'fixture-revision-perf';
  edition.profile_id = id;
  edition.local_date = '2026-11-18';
  edition.generated_at = new Date().toISOString();
  const storyTemplate = edition.sections[0];
  edition.sections = Array.from({ length: 24 }, (_, index) => ({ ...storyTemplate,
    id: `fixture-story-${index + 1}`, heading: `Fictional payment development ${index + 1}`,
    body: `A fictional payment network describes a new settlement status for story ${index + 1}. ` +
      'The update explains the operational context and the effect on payment providers. '.repeat(4),
    topic_ids: ['payments'] }));
  const generatedDir = path.join(stateDir, 'generated', 'profiles', id);
  writeJson(path.join(generatedDir, 'current.json'), {
    editions: { editions: [edition] }, sources: { sources },
  });
  const jobDir = path.join(stateDir, 'episode-jobs', 'profiles', id);
  const audioEpisode = structuredClone(require('./fixtures/episode-audio.json'));
  audioEpisode.profile_id = id;
  for (let index = 0; index < 40; index++) {
    const jobId = index === 0 ? audioEpisode.id : index.toString(16).padStart(32, 'a');
    const episode = index === 0 ? audioEpisode : {
      id: jobId, title: `Fictional payment service ${index + 1}`, topic: `Fictional payment service ${index + 1}`,
      chapters: [{ id: `fixture-chapter-${index + 1}`, title: 'Status', sentences: [
        { text: 'A fictional payment service shows a pending status.', source_numbers: [1] },
      ] }], sources: [{ id: `fixture-episode-source-${index + 1}`, outlet: 'Fictional Desk',
        title: `Fictional payment service update ${index + 1}`, url: 'https://example.invalid/payments' }],
    };
    writeJson(path.join(jobDir, `${jobId}.json`), {
      job_id: jobId, profile_id: id, topic: `Fictional payment service ${index + 1}`,
      length: 5, style: 'explainer', status: 'done', stage: 'done',
      audio_status: index === 0 ? 'recorded' : 'disabled', created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(), finished_at: new Date().toISOString(),
      counts: { chapters: episode.chapters?.length || 1, sentences: 2, sources: 1, audio_seconds: 0 },
      episode,
    });
  }
  const history = Array.from({ length: 90 }, (_, index) => ({
    editions: { editions: [{ id: `fixture-history-${index + 1}`,
      revision_id: `fixture-history-r${index + 1}`, kind: 'daily', generated: true,
      local_date: new Date(Date.UTC(2026, 10, 18 - index)).toISOString().slice(0, 10),
      sections: [{ id: `fixture-history-story-${index + 1}`, source_ids: ['fixture-source-perf'] }] }] },
    sources: { sources: [{ id: 'fixture-source-perf', outlet: 'Fictional Desk',
      title: `Fictional archived payment update ${index + 1}`,
      url: 'https://example.invalid/archive' }] },
  }));
  writeJson(path.join(generatedDir, 'editions-history.json'), history);
  return { profile, audioEpisode, historyCount: history.length,
    historyBytes: fs.statSync(path.join(generatedDir, 'editions-history.json')).size };
}

function seedFakeAudio(stateDir, manifestPath) {
  const voiceName = 'fixture-audio-voice';
  const script = [
    'import json, sys',
    'from pathlib import Path',
    'from decimal import Decimal',
    'from dev.ingest import episodes_audio',
    'from dev.ingest.tts.base import TtsResult',
    'from dev.ingest.tts.gemini import DEFAULT_MODEL',
    'from dev.tests.episodes_audio_test import FakeProvider, read_fixture, wav_bytes',
    'from dev.ingest.paths import profile_identifier',
    'class PerfProvider(FakeProvider):',
    '    def synthesize(self, text, voice_name):',
    '        return TtsResult(wav_bytes(frames=220500, sample_rate=44100), "audio/wav", 5.0,',
    '                         len(text), Decimal("0.0001"), DEFAULT_MODEL, voice_name)',
    'episode = read_fixture()',
    'profile = json.loads(Path(sys.argv[2]).read_text())',
    'profile_id = profile_identifier(profile)',
    'episode["profile_id"] = profile_id',
    'episodes_audio.DEFAULT_AUDITION_MANIFEST = Path(sys.argv[3])',
    'episodes_audio._manifest_voice_ids = lambda manifest_path=None: frozenset({"fixture-audio-voice"})',
    'episodes_audio.render_episode_audio(episode, profile_id, provider=PerfProvider(),',
    `    voice_name=${JSON.stringify(voiceName)}, state_dir=Path(sys.argv[1]))`,
  ].join('\n');
  writeJson(manifestPath, { samples: ['A', 'B', 'C', 'D', 'E', 'F'].map((label, index) => ({
    label, voice: index === 0 ? voiceName : `fixture-voice-${label}`,
  })) });
  writeJson(path.join(stateDir, 'voice.json'), { voice_name: voiceName });
  const result = spawnSync('python3', ['-c', script, stateDir, path.join(stateDir, 'profile.json'), manifestPath], {
    cwd: ROOT, encoding: 'utf8', env: { ...process.env, BRIEFING_STATE_DIR: stateDir },
  });
  assert.equal(result.status, 0, `Fixture audio render failed: ${result.stderr || result.stdout}`);
}

function installCoherentCatalog(page) {
  const read = name => JSON.parse(fs.readFileSync(path.join(ROOT, 'content', `${name}.json`), 'utf8'));
  const topic = read('topics').topics.find(row => row.id === 'payments');
  const dive = read('deep-dives').deep_dives.find(row => row.topic_id === 'payments');
  const editions = read('editions').editions.map(edition => ({ ...edition,
    coverage: edition.coverage.filter(row => row.topic_id === 'payments'),
    sections: edition.sections.filter(section => (section.topic_ids || []).includes('payments')),
  }));
  const fixtures = { topics: { topics: [topic] }, 'deep-dives': { deep_dives: [dive] }, editions: { editions } };
  return page.route('**/content/*.json', async route => {
    const name = path.basename(new URL(route.request().url()).pathname, '.json');
    if (!Object.hasOwn(fixtures, name)) return route.continue();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtures[name]) });
  });
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

function readRssKib(pid, field = 'VmHWM') {
  const status = fs.readFileSync(`/proc/${pid}/status`, 'utf8');
  const match = status.match(new RegExp(`^${field}:\\s+(\\d+)\\s+kB$`, 'm')) ||
    (field === 'VmHWM' ? status.match(/^VmRSS:\s+(\d+)\s+kB$/m) : null);
  if (!match) throw new Error('Could not read preview process RSS from /proc');
  return Number(match[1]);
}

async function sampleApi(base, path, expected, repetitions = 30) {
  const samples = [];
  let lastPayload;
  for (let index = 0; index < repetitions; index++) {
    const started = performance.now();
    const response = await fetch(`${base}${path}`);
    const elapsed = performance.now() - started;
    assert.equal(response.status, 200, `${path} returned ${response.status}`);
    lastPayload = await response.json();
    assert.equal(expected(lastPayload), true, `${path} fixture shape did not match`);
    samples.push(elapsed);
  }
  samples.sort((left, right) => left - right);
  return { p95: samples[Math.ceil(samples.length * 0.95) - 1], payload: lastPayload };
}

async function waitForServer(base, child) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Preview server exited with ${child.exitCode}`);
    try {
      const response = await fetch(`${base}/api/status`);
      if (response.ok) return;
    } catch { /* wait until the local socket is ready */ }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Preview server did not start within 10 seconds');
}

async function pagePaint(page, url, selector, label, thresholdMs) {
  const started = performance.now();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  try {
    await page.locator(selector).first().waitFor({ state: 'visible', timeout: 10000 });
  } catch (error) {
    throw new Error(`${label} did not appear; page text: ${(await page.locator('body').innerText()).slice(0, 500)}`, { cause: error });
  }
  const elapsed = performance.now() - started;
  TABLE.push({ check: label, observed: `${elapsed.toFixed(1)} ms`, threshold: `< ${thresholdMs} ms`, pass: elapsed < thresholdMs });
  assert.ok(elapsed < thresholdMs, `${label} was ${elapsed.toFixed(1)} ms (limit ${thresholdMs} ms)`);
  return elapsed;
}

function printTable() {
  const headers = ['Check', 'Observed', 'Limit', 'Result'];
  const rows = TABLE.map(row => [row.check, row.observed, row.threshold,
    row.status || (row.pass ? 'PASS' : 'FAIL')]);
  const widths = headers.map((header, index) => Math.max(header.length, ...rows.map(row => row[index].length)));
  const border = `+${widths.map(width => '-'.repeat(width + 2)).join('+')}+`;
  const line = values => `| ${values.map((value, index) => value.padEnd(widths[index])).join(' | ')} |`;
  console.log(border); console.log(line(headers)); console.log(border);
  for (const row of rows) console.log(line(row));
  console.log(border);
}

async function run() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'briefing-service-limits-'));
  const stateDir = path.join(temp, 'state');
  const testHome = path.join(temp, 'home');
  const ledgerDir = path.join(temp, 'usage-ledger');
  fs.mkdirSync(testHome, { recursive: true });
  const seeded = seedState(stateDir);
  const voiceManifest = path.join(testHome, '.local', 'share', 'briefing', 'audio-auditions', 'manifest.json');
  seedFakeAudio(stateDir, voiceManifest);
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const startupStarted = performance.now();
  const child = spawn('python3', [path.join(ROOT, 'dev/preview_server.py'), '127.0.0.1', String(port)], {
    cwd: ROOT, stdio: 'ignore', env: { ...process.env, HOME: testHome,
      BRIEFING_STATE_DIR: stateDir, BRIEFING_TEST_LEDGER_DIR: ledgerDir,
      BRIEFING_EPISODES: 'fake', BRIEFING_TESTING: '1' },
  });
  let browser;
  try {
    await waitForServer(base, child);
    const startupMs = performance.now() - startupStarted;
    const idleRss = readRssKib(child.pid, 'VmRSS');
    TABLE.push({ check: 'Preview startup to ready', observed: `${startupMs.toFixed(1)} ms`, threshold: '< 5000 ms', pass: startupMs < 5000 });
    assert.ok(startupMs < 5000, `Preview startup took ${startupMs.toFixed(1)} ms`);
    TABLE.push({ check: 'Preview idle RSS', observed: `${(idleRss / 1024).toFixed(1)} MiB`, threshold: '< 96 MiB', pass: idleRss < RSS_CAP_KIB });
    assert.ok(idleRss < RSS_CAP_KIB, `Preview idle RSS ${(idleRss / 1024).toFixed(1)} MiB exceeds 96 MiB`);
    TABLE.push({ check: 'Fixture edition history', observed: `${seeded.historyCount} editions · ${(seeded.historyBytes / 1024).toFixed(1)} KiB`, threshold: '90 editions', pass: seeded.historyCount === 90 });
    assert.equal(seeded.historyCount, 90);
    const todaySample = await sampleApi(base, '/api/edition?profile=active&history=0',
      payload => payload.edition?.id === 'fixture-edition-perf');
    const today = todaySample.payload;
    assert.equal(today.edition?.id, 'fixture-edition-perf');
    assert.equal(today.history, undefined);
    const todayBytes = Buffer.byteLength(JSON.stringify(today));
    assert.ok(todayBytes > 12_000, `Today fixture should exercise a large payload; got ${todayBytes} bytes`);
    TABLE.push({ check: 'Today edition payload', observed: `${(todayBytes / 1024).toFixed(1)} KiB`, threshold: '> 12 KiB', pass: true });
    TABLE.push({ check: 'Today API p95 (30 requests)', observed: `${todaySample.p95.toFixed(1)} ms`, threshold: '< 1000 ms', pass: todaySample.p95 < 1000 });
    assert.ok(todaySample.p95 < 1000, `Today edition p95 was ${todaySample.p95.toFixed(1)} ms`);
    const episodesSample = await sampleApi(base, '/api/episodes',
      payload => payload.jobs?.length === 40);
    const episodes = episodesSample.payload;
    assert.equal(episodes.jobs?.length, 40);
    const episodesBytes = Buffer.byteLength(JSON.stringify(episodes));
    assert.ok(episodesBytes > 8_000, `Episode list fixture should exercise a larger payload; got ${episodesBytes} bytes`);
    TABLE.push({ check: 'Episode list payload', observed: `${(episodesBytes / 1024).toFixed(1)} KiB`, threshold: '> 8 KiB', pass: true });
    TABLE.push({ check: 'Episodes API p95 (30 requests)', observed: `${episodesSample.p95.toFixed(1)} ms`, threshold: '< 1000 ms', pass: episodesSample.p95 < 1000 });
    assert.ok(episodesSample.p95 < 1000, `Episode list p95 was ${episodesSample.p95.toFixed(1)} ms`);
    const statusSample = await sampleApi(base, '/api/status',
      payload => payload.ok === true && typeof payload.last_run_at === 'string');
    TABLE.push({ check: 'Status API p95 (30 requests)', observed: `${statusSample.p95.toFixed(1)} ms`, threshold: '< 1000 ms', pass: statusSample.p95 < 1000 });
    assert.ok(statusSample.p95 < 1000, `Status p95 was ${statusSample.p95.toFixed(1)} ms`);
    const rangeStarted = performance.now();
    const audioRange = await fetch(`${base}/api/episodes/${seeded.audioEpisode.id}/audio`, {
      headers: { Range: 'bytes=0-65535' },
    });
    const rangeMs = performance.now() - rangeStarted;
    const rangeBytes = await audioRange.arrayBuffer();
    assert.equal(audioRange.status, 206);
    assert.match(audioRange.headers.get('content-range') || '', /^bytes 0-65535\/\d+$/);
    assert.equal(rangeBytes.byteLength, 64 * 1024);
    TABLE.push({ check: '64 KiB audio range', observed: `${rangeMs.toFixed(1)} ms`, threshold: '< 500 ms', pass: rangeMs < 500 });
    assert.ok(rangeMs < 500, `Audio range response was ${rangeMs.toFixed(1)} ms`);
    const afterApiRss = readRssKib(child.pid);
    TABLE.push({ check: 'Preview server high-water RSS', observed: `${(afterApiRss / 1024).toFixed(1)} MiB`, threshold: '< 96 MiB', pass: afterApiRss < RSS_CAP_KIB });
    assert.ok(afterApiRss < RSS_CAP_KIB, `Preview process high-water RSS ${(afterApiRss / 1024).toFixed(1)} MiB exceeds 96 MiB`);

    try {
      if (!chromium) throw new Error('Playwright package unavailable');
      browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    } catch (error) {
      // Some CI/test hosts have the Playwright package and browser cache but lack
      // Chromium system libraries. Keep API/RSS checks actionable and report the
      // visual portion as skipped rather than disguising the environment defect.
      const message = String(error?.message || error);
      const detail = message.match(/error while loading shared libraries: ([^\s]+)/)?.[1];
      if (!detail && !/Playwright package unavailable/.test(message)) throw error;
      TABLE.push({ check: 'Browser paint checks', observed: detail ? `${detail} unavailable` : 'Playwright unavailable',
        threshold: 'Browser dependency installed', status: 'SKIP' });
    }
    if (browser) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
      await installCoherentCatalog(page);
      await page.addInitScript(({ profile, key }) => {
        localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
        localStorage.setItem('briefing:v1:built-profile-key', key);
      }, { profile: DEFAULT_PROFILE, key: builtProfileKey(DEFAULT_PROFILE) });
      await pagePaint(page, `${base}/#today`, '.today-story-row, [data-read-section]', 'Today first story paint', 1500);
      await page.locator('.today-page').waitFor({ state: 'visible' });
      await pagePaint(page, `${base}/#episodes`, '.episode-list-page .episode-job-card', 'Episode listing paint', 1000);
    }
    const finalRss = readRssKib(child.pid);
    TABLE.push({ check: 'Preview server after Today + episodes', observed: `${(finalRss / 1024).toFixed(1)} MiB`, threshold: '< 96 MiB', pass: finalRss < RSS_CAP_KIB });
    assert.ok(finalRss < RSS_CAP_KIB, `Preview process high-water RSS ${(finalRss / 1024).toFixed(1)} MiB exceeds 96 MiB`);
    printTable();
  } finally {
    await browser?.close().catch(() => {});
    child.kill('SIGTERM');
    await new Promise(resolve => {
      if (child.exitCode !== null) resolve();
      else { child.once('exit', resolve); setTimeout(resolve, 3000).unref(); }
    });
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
