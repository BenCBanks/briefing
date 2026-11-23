require('../test_guard.cjs').enforceTestEnvironment();

// Client transcript export helpers use only the fictional caption fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const net = require('node:net');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episode-captions.json'), 'utf8'));

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitForServer(url, child) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`preview server exited with ${child.exitCode}`);
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('preview server did not start');
}

async function runFixtureOnly() {
  const exporter = await import(pathToFileURL(path.join(root, 'src/episodes/export.js')));
  const previous = structuredClone(fixture);
  previous.revision_id = 'fictional-episode-r1';
  previous.chapters[0].sentences[0].text = 'Earlier fictional wording from before the correction.';
  const current = structuredClone(fixture);
  current.revision_id = 'fictional-episode-r2';
  current.asked = 'PRIVATE REQUEST: include my confidential project name Cedar Lantern.';
  current.chapters[0].sentences[0].text = 'The corrected fictional report says Alder Bay will begin the parcel trial in June.';
  current.sources[0].title = 'A fictional municipal procurement notice with a deliberately long title that must remain complete in transcript exports';

  const copied = [];
  const copyResult = await exporter.copyEpisodeTranscript(current, { writeText: async text => copied.push(text) });
  const link = { clicked: false, removed: false, click() { this.clicked = true; }, remove() { this.removed = true; } };
  let downloadedBlob;
  const revoked = [];
  class FixtureBlob {
    constructor(parts, options) { this.text = parts.join(''); this.type = options.type; }
  }
  const downloadResult = exporter.downloadEpisodeTranscript(current, {
    documentRef: { createElement: tag => { assert.equal(tag, 'a'); return link; }, body: { append: node => assert.equal(node, link) } },
    urlApi: {
      createObjectURL: blob => { downloadedBlob = blob; return 'blob:fictional-episode'; },
      revokeObjectURL: url => revoked.push(url),
    },
    BlobConstructor: FixtureBlob,
  });

  for (const transcript of [copyResult, downloadResult, downloadedBlob.text]) {
    assert.match(transcript, /The corrected fictional report says Alder Bay will begin the parcel trial in June\. \[1\]/);
    assert.doesNotMatch(transcript, /Earlier fictional wording from before the correction\./);
    assert.match(transcript, /\[1\] Alder Bay Civic Desk — A fictional municipal procurement notice with a deliberately long title that must remain complete in transcript exports/);
    assert.match(transcript, /Residents can collect packages during one trip downtown\. \[1, 2\]/);
    assert.doesNotMatch(transcript, /PRIVATE REQUEST|Cedar Lantern/);
  }
  assert.deepEqual(copied, [copyResult]);
  assert.equal(downloadedBlob.type, 'text/plain;charset=utf-8');
  assert.equal(link.download, 'episode-transcript.txt');
  assert.equal(link.clicked, true);
  assert.equal(link.removed, true);
  assert.deepEqual(revoked, ['blob:fictional-episode']);
  assert.notEqual(exporter.buildPlainTranscript(previous), copyResult, 'the export reflects the supplied current revision');

  const failingLink = {
    removed: false,
    click() { throw new Error('fixture click failed'); },
    remove() { this.removed = true; },
  };
  const failedRevoked = [];
  let failedBlob;
  assert.throws(() => exporter.downloadEpisodeTranscript(current, {
    documentRef: { createElement: tag => { assert.equal(tag, 'a'); return failingLink; }, body: { append: node => assert.equal(node, failingLink) } },
    urlApi: {
      createObjectURL: blob => { failedBlob = blob; return 'blob:fictional-failure'; },
      revokeObjectURL: url => failedRevoked.push(url),
    },
    BlobConstructor: FixtureBlob,
  }), /fixture click failed/);
  assert.equal(failingLink.removed, true, 'a failed click still removes the temporary link');
  assert.deepEqual(failedRevoked, ['blob:fictional-failure'], 'a failed click still revokes the object URL');
  assert.equal(failedBlob.text, copyResult, 'the failed download was built from the same copied transcript');
  assert.deepEqual(copied, [copyResult], 'download failure does not alter copied transcript text');
  assert.match(copyResult, /Residents can collect packages during one trip downtown\. \[1, 2\]/,
    'the copied transcript retains its source citations');
  console.log('Episode export fixture: current corrected copy and download retain citations/titles and omit the private prompt.');
}

async function run() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'episode-export-'));
  const port = await freePort();
  const baseURL = `http://127.0.0.1:${port}`;
  const child = spawn('python3', ['dev/preview_server.py', '127.0.0.1', String(port)], {
    cwd: root,
    env: { ...process.env, BRIEFING_STATE_DIR: path.join(temp, 'state') },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let serverErrors = '';
  child.stderr.on('data', chunk => { serverErrors += chunk.toString(); });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    await waitForServer(baseURL, child);
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
    const result = await page.evaluate(async episode => {
      const exporter = await import('/src/episodes/export.js');
      const transcript = exporter.buildPlainTranscript(episode);
      const vtt = exporter.buildWebVtt(episode);
      const copied = [];
      const clipboard = { writeText: async text => copied.push(text) };
      const copyResult = await exporter.copyEpisodeTranscript(episode, clipboard);
      const control = exporter.createTranscriptCopyControl(episode, { documentRef: document, clipboard });
      document.body.append(control);
      control.querySelector('button').click();
      await new Promise(resolve => setTimeout(resolve, 0));
      return { transcript, vtt, copied, status: control.querySelector('[role="status"]').textContent };
    }, fixture);

    assert.match(result.transcript, /^A fictional library delivery trial/m);
    assert.match(result.transcript, /Alder Bay will test shared parcel lockers this spring\. \[1\]/);
    assert.match(result.transcript, /Residents can collect packages during one trip downtown\. \[1, 2\]/);
    assert.match(result.transcript, /\[2\] North Harbor Ledger — Fictional resident survey/);
    assert.doesNotMatch(result.transcript, /https?:\/\//);
    assert.match(result.vtt, /00:00:05\.000 --> 00:00:06\.500/);
    assert.equal(result.copied.length, 2);
    assert.equal(result.copied[0], result.transcript);
    assert.equal(result.status, 'Transcript copied.');

    const bad = structuredClone(fixture);
    bad.chapters[1].sentences[0].end_seconds = -1;
    const invalid = await page.evaluate(async episode => {
      const exporter = await import('/src/episodes/export.js');
      try { exporter.buildWebVtt(episode); return false; } catch { return true; }
    }, bad);
    assert.equal(invalid, true, 'negative caption timestamps are rejected');
    await browser.close();
    console.log('Episode transcript export: citations, source list, chapter-offset VTT, clipboard helper and bad-time rejection pass.');
  } finally {
    await browser.close().catch(() => {});
    child.kill('SIGTERM');
    if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
    fs.rmSync(temp, { recursive: true, force: true });
    if (serverErrors) process.stderr.write(serverErrors);
  }
}

const fixtureOnly = process.argv.includes('--fixture-only');
(fixtureOnly ? runFixtureOnly() : run()).catch(error => { console.error(error); process.exitCode = 1; });
