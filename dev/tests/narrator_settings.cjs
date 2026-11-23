require('../test_guard.cjs').enforceTestEnvironment();

// Fixture-only narrator picker coverage. No provider calls or live state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const manifest = require('./fixtures/audition_manifest.json');
const scheduleFixture = require('./fixtures/settings-next-run.json');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const captureDir = process.env.BRIEFING_CAPTURE_DIR || '/tmp/briefing-shots/narrator-test';

async function captureLargeText(page) {
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  const result = await page.evaluate(() => {
    const controls = [...document.querySelectorAll('main button, main a[href], main summary, main input, main select, main textarea')]
      .filter(element => element.getClientRects().length && !element.disabled)
      .map(element => ({ label: (element.innerText || element.getAttribute('aria-label') || '').trim(),
        width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }));
    const clipped = [...document.querySelectorAll('main :is(h1,h2,h3,p,a,button,label,summary,span,strong,small)')]
      .filter(element => element.textContent.trim() && element.getClientRects().length)
      .filter(element => {
        const style = getComputedStyle(element);
        return style.textOverflow === 'ellipsis' ||
          (['hidden', 'clip'].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1) ||
          (['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1);
      }).map(element => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')} clips text`);
    return { root: parseFloat(getComputedStyle(document.documentElement).fontSize),
      width: document.documentElement.scrollWidth, viewport: innerWidth, controls, clipped };
  });
  assert.ok(result.root >= 32, 'Settings applies the 200% text scale');
  assert.ok(result.width <= result.viewport, `Settings has no horizontal overflow (${result.width}/${result.viewport})`);
  assert.deepEqual(result.clipped, [], `Settings text wraps: ${result.clipped.join('; ')}`);
  const small = result.controls.filter(control => control.width < 44 || control.height < 44);
  assert.deepEqual(small, [], `Settings controls meet 44px targets: ${JSON.stringify(small)}`);
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
}

async function contrast(page, selector) {
  return page.locator(selector).first().evaluate(element => {
    const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(channel => {
      const c = Number(channel) / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
    });
    const luminance = color => { const [r,g,b] = rgb(color); return .2126*r + .7152*g + .0722*b; };
    const [a,b] = [luminance(getComputedStyle(element).backgroundColor), luminance(getComputedStyle(element).color)].sort((x,y) => y-x);
    return (a + .05) / (b + .05);
  });
}

async function assertFocusRing(page, selector, label) {
  const focus = await page.locator(selector).first().evaluate(element => {
    element.focus({ focusVisible: true });
    const style = getComputedStyle(element);
    return { visible: element.matches(':focus-visible'), outline: style.outlineStyle, width: parseFloat(style.outlineWidth) };
  });
  assert.ok(focus.visible && focus.outline !== 'none' && focus.width >= 2,
    `${label} has a visible keyboard focus ring: ${JSON.stringify(focus)}`);
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    const posts = [];
    const capWrites = [];
    page.on('request', request => {
      if (request.method() !== 'GET' && /(?:cap|spend|budget)/i.test(new URL(request.url()).pathname)) capWrites.push(request.url());
    });
    await page.addInitScript(({ dailyTime, now }) => {
      localStorage.setItem('briefing:v1:daily-time', dailyTime);
      const NativeDate = Date;
      const fixedNow = NativeDate.parse(now);
      class FixedDate extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [fixedNow])); }
        static now() { return fixedNow; }
      }
      window.Date = FixedDate;
    }, { dailyTime: scheduleFixture.daily_time, now: scheduleFixture.now_utc });
    await page.route('**/api/profile', route => route.request().method() === 'GET'
      ? route.fulfill({ status: 404, json: { error: 'fixture profile absent' } })
      : route.fulfill({ json: { saved: true } }));
    await page.route('**/api/status', route => route.fulfill({ json: { last_run_at: '2026-11-18T11:00:00Z' } }));
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: manifest }));
    await page.route('**/audition-audio/*.wav', route => route.fulfill({ status: 200, contentType: 'audio/wav', body: Buffer.alloc(44) }));
    let savedVoice = 'A';
    await page.route('**/api/voice', async route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: { letter: savedVoice } });
      const body = route.request().postDataJSON(); posts.push(body);
      if (body.voice_name === 'FixtureVoiceD') return route.fulfill({ status: 503, json: { error: 'fixture failure' } });
      savedVoice = body.voice_name.replace('FixtureVoice', '');
      return route.fulfill({ json: { letter: savedVoice } });
    });
    await page.goto(`${baseURL}/#preferences`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Choose your voice' }).waitFor();
    assert.equal(await page.locator('.settings-voice-row .settings-row-value').innerText(), 'Choose a voice', 'first run clearly prompts for a voice');
    assert.equal(await page.locator('.settings-daily-row .settings-row-value').innerText(), scheduleFixture.daily_time_label, 'Settings shows the saved ET briefing time');
    assert.equal(await page.locator('.settings-next-run-value').innerText(), scheduleFixture.next_run_label, 'next run advances to the next ET calendar day after the scheduled time');
    assert.deepEqual(await page.locator('.settings-quick-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height >= 48)), [true, true, true, true], 'settings rows retain comfortable height');
    for (const row of ['.settings-daily-row', '.settings-next-run-row']) {
      assert.equal(await page.locator(row).getAttribute('aria-readonly'), 'true', 'schedule rows are read only');
      assert.equal(await page.locator(row).locator('input,button,select').count(), 0, 'schedule rows expose no editable controls');
    }
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:v1:daily-time')), scheduleFixture.daily_time, 'showing the schedule does not change the saved time');
    const spending = page.locator('.settings-spending-row');
    assert.equal(await spending.getAttribute('aria-readonly'), 'true');
    assert.equal(await spending.getByText('Read only', { exact: true }).count(), 1);
    assert.equal(await spending.locator('input,button,select,a[href]').count(), 0, 'spending and budget settings cannot be edited from this row');
    fs.mkdirSync(captureDir, { recursive: true });
    await assertFocusRing(page, '.settings-voice-row', 'Voice setting');
    await assertFocusRing(page, '.settings-about-row summary', 'About disclosure');
    await page.locator('.settings-about-row summary').click();
    assert.match(await page.locator('.settings-about-row').innerText(), /daily five-minute briefing.*ready by 6 am ET/i);
    await page.getByRole('button', { name: /Voice Choose a voice/ }).click();
    assert.ok(await page.locator('#settings-voice').isVisible(), 'the Voice row brings its controls into view');
    await page.locator('.preferences-voice h2').evaluate(element => element.scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.screenshot({ path: path.join(captureDir, 'voice-first-run-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(captureDir, 'voice-first-run-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    assert.deepEqual(await page.locator('.narrator-sample>span').allTextContents(), ['Sample A', 'Sample B', 'Sample C', 'Sample D', 'Sample E', 'Sample F']);
    assert.equal(await page.locator('.narrator-sample button').count(), 12, 'each sample has Play and Use this voice actions');
    const sampleRows = await page.locator('.narrator-sample').evaluateAll(rows => rows.map(row => {
      const label = row.querySelector('span').getBoundingClientRect();
      const buttons = [...row.querySelectorAll('button')].map(button => button.getBoundingClientRect());
      return { rowHeight: row.getBoundingClientRect().height, labelRight: label.right, firstButtonLeft: buttons[0].left,
        buttonsInline: Math.abs(buttons[0].top - buttons[1].top) < 2, targets: buttons.every(button => button.height >= 44) };
    }));
    assert.ok(sampleRows.every(row => row.rowHeight <= 64 && row.labelRight <= row.firstButtonLeft && row.buttonsInline && row.targets), '390px narrator samples fit one compact row with 44px actions');
    assert.equal(await page.getByText('About the AI voice').count(), 1, 'AI voice disclosure is present');
    assert.equal(await page.locator('.narrator-use[aria-pressed=true]').count(), 0, 'the default is not presented as a saved choice');
    assert.equal(await page.locator('.narrator-sample:nth-child(1) .narrator-use').isDisabled(), false, 'the default sample can be explicitly saved');
    await page.getByRole('button', { name: 'Play sample B' }).click();
    assert.match(await page.locator('audio[aria-label="Narrator sample audio"]').getAttribute('src'), /^\/audition-audio\/sample-b\.wav$/);
    await page.locator('.narrator-sample:nth-child(3) .narrator-use').click();
    await page.waitForFunction(() => localStorage.getItem('briefing:narrator-sample') === 'C');
    await page.locator('.preferences-voice h2').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(captureDir, 'voice-selected-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(captureDir, 'voice-selected-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    assert.equal(await page.locator('.settings-voice-row .settings-row-value').innerText(), 'Sample C', 'the summary row reflects the selected voice');
    assert.equal(await page.getByRole('heading', { name: 'Narrator' }).count(), 1, 'the first-run prompt ends after choosing a voice');
    assert.deepEqual(posts[0], { voice_name: 'FixtureVoiceC' }, 'selection POST sends the manifest voice name');
    assert.equal(await page.locator('.narrator-use[aria-pressed=true]').innerText(), 'Selected');
    await page.evaluate(() => localStorage.removeItem('briefing:narrator-sample'));
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.narrator-use[aria-pressed=true]').waitFor();
    assert.equal(await page.locator('.narrator-use[aria-pressed=true]').innerText(), 'Selected', 'saved voice loads from the API after reload');
    assert.equal(await page.locator('.settings-voice-row .settings-row-value').innerText(), 'Sample C');
    await assertFocusRing(page, '.narrator-sample-actions button', 'Narrator sample action');
    assert.deepEqual(capWrites, [], 'Settings sends no requests that change spending or budget caps');
    assert.ok(await contrast(page, '.narrator-sample-actions button') >= 3, 'light action contrast meets control minimum');
    await page.locator('.settings-about-row').evaluate(details => { details.open = false; });
    await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
    await captureLargeText(page);
    await page.setViewportSize({ width: 195, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'settings rows do not overflow at a 200% zoom equivalent');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: 'dark' });
    assert.ok(await contrast(page, '.narrator-sample-actions button') >= 3, 'dark action contrast meets control minimum');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.narrator-sample:nth-child(4) .narrator-use').click();
    await page.getByText('Voice could not be saved. Try again.').waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('briefing:narrator-sample')), 'C', 'failed save preserves the prior selection');
    console.log(`narrator settings: six samples, audio URL, success/failure saves, disclosure, selected state and contrast passed; screenshots: ${captureDir}`);
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
