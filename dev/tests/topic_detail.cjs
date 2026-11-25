require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8938';
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/topic-detail.json'), 'utf8'));
const shotDir = process.env.BRIEFING_SHOT_DIR || '/tmp/mica-topic-source-correction-visual';
fs.mkdirSync(shotDir, { recursive: true });
const edition = structuredClone(fixture.edition);
const profile = structuredClone(fixture.profile);
const profileKey = JSON.stringify({ words: profile.words, minutes: profile.minutes,
  topics: profile.topics.map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude,
    depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })), muted: profile.muted });

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    let generateCalls = 0;
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    await page.addInitScript(({ value, key }) => {
      localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(value));
      localStorage.setItem('briefing:v1:built-profile-key', key);
    }, { value: profile, key: profileKey });
    await page.route('**/api/profile', route => route.fulfill({ json: { profile } }));
    await page.route('**/api/edition?profile=active&history=0', route => route.fulfill({ json: {
      generated: true, edition, sources: fixture.sources,
    } }));
    await page.route('**/api/edition?profile=active&history=1', route => route.fulfill({ json: { history: fixture.history, sources: fixture.history.flatMap(bundle => bundle.sources) } }));
    await page.route('**/api/generate', route => { generateCalls++; return route.fulfill({ status: 503, json: { error: 'fixture_generation_unavailable' } }); });

    await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
    const paymentsRow = page.locator('.topic-row').filter({ has: page.getByRole('heading', { name: 'Payments', exact: true }) });
    await paymentsRow.getByRole('link', { name: 'Recent stories' }).click();
    await page.waitForURL('**/#topic-detail/payments');
    await page.getByRole('heading', { name: 'Payments', exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.topic-detail-story').length === 4);
    assert.equal(await page.locator('.topic-detail-story').count(), 4, 'current, withdrawn and earlier fictional stories are shown');
    assert.match(await page.locator('.topic-detail-date').first().innerText(), /Published Nov 14, 2026/);
    assert.match(await page.locator('.topic-detail-date').last().innerText(), /Published Nov 10, 2026/);
    const correctedStory = page.locator('.topic-detail-story').filter({ has: page.getByRole('link', { name: 'The payments rule sets a shared deadline' }) });
    assert.equal(await correctedStory.locator('.topic-detail-corrected').innerText(), 'Corrected Nov 20, 2026', 'Topic shows the current correction date separately');
    const withdrawnStory = page.locator('.topic-detail-story').filter({ hasText: 'Story withdrawn' });
    assert.equal(await withdrawnStory.count(), 1, 'withdrawn story gets a plain status title');
    assert.doesNotMatch(await withdrawnStory.innerText(), /Superseded headline|withdrawn text claimed/,
      'withdrawn headline and claim text stay hidden');
    assert.equal(await withdrawnStory.locator('.topic-detail-sources button').count(), 0,
      'withdrawn source links are unavailable');
    assert.match(await page.locator('.topic-detail-why').first().innerText(), /^Why it matters: A shared date gives banks/);
    assert.equal(await page.locator('.topic-detail-scope').innerText(), 'How money moves between people, businesses and banks', 'The topic scope appears below its title');
    assert.equal(await page.locator('.topic-detail-exclusions').count(), 0, 'Topic detail avoids negative exclusion copy');
    const updatesSwitch = page.getByRole('switch', { name: 'Daily updates' });
    assert.equal(await updatesSwitch.getAttribute('aria-checked'), 'true');
    const switchSize = await updatesSwitch.evaluate(element => ({ width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }));
    assert.ok(switchSize.width >= 48 && switchSize.height >= 48, 'Daily updates switch has a 48px touch target');
    assert.equal(await page.getByRole('link', { name: 'Brief me on this' }).count(), 1);
    assert.equal(await page.locator('.topic-detail-ask-arrow').getAttribute('aria-hidden'), 'true', 'CTA arrow is decorative to assistive technology');
    assert.equal(await page.locator('.topic-detail-story h3 a').first().evaluate(link => getComputedStyle(link).textDecorationLine), 'underline', 'recent story titles read as links');
    assert.equal(await page.locator('.topic-detail-why strong').first().innerText(), 'Why it matters:', 'story context has a clear label');
    assert.equal(await page.getByRole('link', { name: 'Edit topic preferences' }).count(), 1);
    const targetSizes = await page.locator('.topic-detail-page a, .topic-detail-page button').evaluateAll(items => items.map(item => ({
      name: item.innerText, height: item.getBoundingClientRect().height,
    })));
    assert.ok(targetSizes.every(item => item.height >= 44), `Every topic detail action has a 44px target: ${JSON.stringify(targetSizes)}`);
    assert.ok(await page.locator('.topic-detail-story h3 a').first().evaluate(item => getComputedStyle(item).overflowWrap === 'anywhere'), 'Story links can wrap without truncation');
    await page.screenshot({ path: path.join(shotDir, 'topic-correction-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(shotDir, 'topic-correction-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    await withdrawnStory.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shotDir, 'topic-withdrawn-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(shotDir, 'topic-withdrawn-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: '/tmp/mica-topic-detail-light.png' });
    await page.emulateMedia({ colorScheme: 'dark' });
    assert.equal(await page.locator('.topic-detail-ask').evaluate(link => getComputedStyle(link).backgroundColor), 'rgb(27, 28, 32)', 'CTA uses a calm dark surface in dark mode');
    await page.screenshot({ path: '/tmp/mica-topic-detail-dark.png' });
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.topic-detail-page h1, .topic-detail-page h2, .topic-detail-page h3, .topic-detail-page p, .topic-detail-page a, .topic-detail-page button, .topic-detail-page strong').evaluateAll(items => {
      for (const item of items) item.style.setProperty('font-size', `${Math.round(parseFloat(getComputedStyle(item).fontSize) * 1.25)}px`, 'important');
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Topic detail fits at 390px with larger text');
    await page.screenshot({ path: '/tmp/mica-topic-detail-large-light.png' });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: '/tmp/mica-topic-detail-large-dark.png' });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.topic-detail-page *').evaluateAll(items => items.forEach(item => item.style.removeProperty('font-size')));

    let topicFocusCount = 0;
    for (let index = 0; index < 48; index += 1) {
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => {
        const item = document.activeElement;
        const style = getComputedStyle(item);
        return { within: Boolean(item.closest('.topic-detail-page')), tag: item.tagName, ring: item.matches(':focus-visible') && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2 };
      });
      if (focus.tag === 'BODY') break;
      if (focus.within) { assert.equal(focus.ring, true, 'Keyboard focus is visible on every topic detail control'); topicFocusCount += 1; }
    }
    assert.ok(topicFocusCount >= targetSizes.length, 'Keyboard navigation reaches every topic detail action');

    const topicSourceAction = correctedStory.getByRole('button', { name: 'Open source: Federal Reserve, Payment systems overview' });
    await topicSourceAction.focus();
    await page.keyboard.press('Enter');
    await page.waitForURL('**/#source/fed-payments');
    assert.match(await page.locator('#content').innerText(), /Payment systems overview/);
    await page.screenshot({ path: path.join(shotDir, 'topic-source-light.png') });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(shotDir, 'topic-source-dark.png') });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.reader-back').click();
    await page.waitForURL('**/#topic-detail/payments');
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') ===
      'Open source: Federal Reserve, Payment systems overview', null, { timeout: 2000 });
    assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'BUTTON',
      'closing a source opened with Enter returns keyboard focus to the same Topic citation');

    await page.getByRole('link', { name: 'The payments rule sets a shared deadline' }).click();
    await page.waitForURL('**/#edition/daily/topic-detail-fixture/payments-one');
    assert.match(await page.locator('#content').innerText(), /The payments rule sets a shared deadline/);
    await page.goBack();
    await page.waitForURL('**/#topic-detail/payments');
    await page.getByRole('link', { name: 'Brief me on this' }).click();
    await page.waitForURL('**/#ask-episode');
    assert.equal(await page.locator('#episode-request-prompt').inputValue(), 'Tell me about Payments', 'episode request starts with this topic');
    assert.equal(await page.locator('.episode-request-page h1').innerText(), 'Make an episode', 'topic briefing opens the On demand page');
    assert.equal(await page.locator('[data-nav="on-demand"]').getAttribute('aria-current'), 'page', 'On demand is selected after leaving topic detail');
    assert.equal(await page.locator('.episode-request-chip[aria-pressed="true"]').innerText(), '5 min', 'topic briefing keeps the normal default length');
    await page.screenshot({ path: '/tmp/mica-topic-on-demand-light.png' });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: '/tmp/mica-topic-on-demand-dark.png' });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goBack();
    await page.waitForURL('**/#topic-detail/payments');
    await page.getByRole('link', { name: 'Edit topic preferences' }).click();
    await page.waitForURL('**/#interests');

    await page.goBack();
    await page.waitForURL('**/#topic-detail/payments');
    const pauseBuild = page.waitForRequest(request => request.url().endsWith('/api/generate'));
    await updatesSwitch.click();
    await pauseBuild;
    await page.waitForFunction(() => {
      const current = JSON.parse(localStorage.getItem('briefing:v1:interest-profile') || '{}');
      return current.muted?.some(item => item.label === 'Payments');
    });
    assert.ok(generateCalls > 0, 'pause action requests a fresh daily edition for the updated profile');

    await page.goto(`${baseURL}/#topic-detail/banking`, { waitUntil: 'networkidle' });
    const followBuild = page.waitForRequest(request => request.url().endsWith('/api/generate'));
    const bankingUpdates = page.getByRole('switch', { name: 'Daily updates' });
    assert.equal(await bankingUpdates.getAttribute('aria-checked'), 'false', 'Unfollowed topics show daily updates off');
    await bankingUpdates.click();
    await followBuild;
    await page.waitForFunction(() => {
      const current = JSON.parse(localStorage.getItem('briefing:v1:interest-profile') || '{}');
      return current.topics?.some(item => item.id === 'banking');
    });
    assert.ok(generateCalls > 1, 'following a topic requests a fresh daily edition');
    console.log(`Topic detail: correction, withdrawal, citation and keyboard-return checks passed. Screenshots: ${shotDir}`);
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
