require('../test_guard.cjs').enforceTestEnvironment();

// End-to-end regression for the old generated/*.json + words-only profile layout.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, builtProfileKey, seedInterestProfile } = require('./helpers/profile.cjs');

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.join(__dirname, 'fixtures/legacy-state');

function fixtureDigest(directory) {
  const files = [];
  const walk = current => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const file = path.join(current, entry.name);
      if (entry.isDirectory()) walk(file);
      else files.push([path.relative(directory, file), crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]);
    }
  };
  walk(directory);
  return JSON.stringify(files.sort(([left], [right]) => left.localeCompare(right)));
}

async function freePort() {
  const server = http.createServer();
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitForServer(child, baseURL) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Preview server exited early (${child.exitCode}).`);
    try {
      const response = await fetch(`${baseURL}/`);
      if (response.ok) return;
    } catch { /* Wait for the local listener. */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Preview server did not become ready within 10 seconds.');
}

async function stopServer(child) {
  if (!child || child.exitCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([
    new Promise(resolve => child.once('exit', resolve)),
    new Promise(resolve => setTimeout(resolve, 3000)),
  ]);
  if (child.exitCode === null) child.kill('SIGKILL');
}

async function run() {
  const legacyProfile = JSON.parse(fs.readFileSync(path.join(FIXTURE, 'profile.json'), 'utf8'));
  assert.deepEqual(Object.keys(legacyProfile), ['words'], 'Fixture retains the legacy words-only profile shape');
  for (const name of ['current.json', 'editions.json', 'editions-history.json', 'sources.json']) {
    assert.ok(fs.existsSync(path.join(FIXTURE, 'generated', name)), `Fixture includes generated/${name}`);
  }
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'briefing-legacy-live-state-'));
  const stateDir = path.join(tempRoot, 'state');
  const homeDir = path.join(tempRoot, 'home');
  fs.mkdirSync(homeDir, { recursive: true });
  fs.cpSync(FIXTURE, stateDir, { recursive: true });
  const beforeFixture = fixtureDigest(FIXTURE);
  const port = await freePort();
  const baseURL = `http://127.0.0.1:${port}`;
  let server;
  let browser;
  try {
    server = spawn('python3', ['dev/preview_server.py', '127.0.0.1', String(port)], {
      cwd: ROOT,
      env: { ...process.env, HOME: homeDir, BRIEFING_STATE_DIR: stateDir },
      stdio: 'ignore',
    });
    await waitForServer(server, baseURL);

    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.setDefaultTimeout(10_000);
    const requests = [];
    const pageErrors = [];
    page.on('request', request => requests.push(request.url()));
    page.on('pageerror', error => pageErrors.push(error.message));
    const activeProfile = { ...DEFAULT_PROFILE, words: legacyProfile.words,
      topics: DEFAULT_PROFILE.topics.map(topic => ({ ...topic, sourcePhrase: legacyProfile.words })) };
    await seedInterestProfile(page, activeProfile);

    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('.today-page').waitFor();
    assert.ok(requests.some(value => {
      const url = new URL(value);
      return url.pathname === '/api/edition' && url.searchParams.get('profile') === 'active';
    }), 'Today requests the active-profile edition endpoint used by the phone client');
    assert.equal(await page.locator('.today-page h1').innerText(), 'Your daily briefing');
    assert.equal(await page.locator('.today-contents > ol > li').count(), 2, 'Both legacy generated stories appear on Today');
    assert.deepEqual(await page.locator('.today-contents .story-row-copy strong').allTextContents(), [
      'Confirm the payment before it moves', 'Make settlement status easier to follow',
    ]);
    assert.doesNotMatch(await page.locator('main').innerText(), /did not load/i, 'Today has no profile or edition load error');

    await page.locator('.today-contents li .story-row-link').first().click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.match(await page.locator('.edition-section').innerText(), /fictional checkout now shows the amount and payment route/i,
      'Opening a legacy story renders its body, not only the headline');
    await page.locator('.reader-back').click();
    await page.locator('.today-page').waitFor();

    await page.locator('.today-earlier-briefings-link').click();
    await page.locator('.library-briefing-list').waitFor();
    assert.ok(await page.locator('.library-briefing-link').count() >= 2, 'Library lists current and historical generated briefings');
    assert.match(await page.locator('.library-briefing-list').innerText(), /Tuesday 17 November/);
    assert.match(await page.locator('.library-briefing-list').innerText(), /Monday 16 November/);
    await page.locator('.library-briefing-link').nth(1).click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.edition h1').innerText(), 'Keep a useful receipt', 'An earlier briefing opens from Library');
    assert.match(await page.locator('.edition-section').innerText(), /fictional receipt records the payment method/i);
    assert.deepEqual(pageErrors, [], 'No client runtime errors occurred during the legacy-state flow');

    assert.equal(fixtureDigest(FIXTURE), beforeFixture, 'The checked-in fictional fixture was not modified by the server');
    assert.ok(fs.existsSync(path.join(stateDir, 'generated', 'default', 'current.json')),
      'The preview route migrated a copy into the isolated state directory');
    console.log('Legacy live-state flow: active API request, Today stories and bodies, Earlier briefings, Library history, and copied-state isolation passed.');
  } finally {
    await browser?.close();
    await stopServer(server);
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
