require('../test_guard.cjs').enforceTestEnvironment();

// Issue 44 accessibility regression audit. Uses fictional bundled content only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const shots = process.env.BRIEFING_SHOT_DIR || path.join(__dirname, '../../screenshots/v3/a11y');
const episodes = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes.json'), 'utf8')).episodes;
const firstEpisode = episodes[0];
// This fictional fixture has a playable recording for the episode revision.
firstEpisode.revision_id = 'fixture-payment-rules-r1';
firstEpisode.script_hash = 'fixture-payment-rules-script-r1';
firstEpisode.audio_status = 'recorded';
firstEpisode.audio = {
  ...firstEpisode.audio,
  status: 'recorded',
  revision_id: firstEpisode.revision_id,
  script_hash: firstEpisode.script_hash,
};
const sourceFixture = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/sources.json'), 'utf8')).sources;
const editionsFixture = JSON.parse(fs.readFileSync(path.join(__dirname, '../../content/editions.json'), 'utf8'));
const auditionFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/audition_manifest.json'), 'utf8'));
const correctionsFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/corrections.json'), 'utf8'));
const phoneReflowRoutes = ['today', 'ask-episode', 'library', 'topics', 'preferences'];
const correctionEditionRevisions = Object.fromEntries(['r1', 'r2'].map(revision => [revision,
  JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/correction', revision, 'editions.json'), 'utf8')).editions.find(item => item.kind === 'daily')]));
const viewports = [
  { name: 'phone', width: 390, height: 844 },
  { name: 'desktop', width: 1280, height: 800 },
];

function contrast(foreground, background) {
  const rgb = value => {
    const match = value.match(/^#([\da-f]{3}|[\da-f]{6})$/i);
    assert.ok(match, `Expected a resolved hex color, got ${value}`);
    let hex = match[1]; if (hex.length === 3) hex = [...hex].map(char => char + char).join('');
    const channels = [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16) / 255);
    return channels.map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  };
  const luminance = color => { const [r, g, b] = rgb(color); return .2126 * r + .7152 * g + .0722 * b; };
  const [a, b] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (a + .05) / (b + .05);
}

function hexColor(value) {
  const parts = value.match(/[\d.]+/g).map(Number);
  return `#${parts.slice(0, 3).map(channel => Math.round(channel).toString(16).padStart(2, '0')).join('')}`;
}

async function assertThemeTokenContrast(page, theme, route) {
  const pairs = await page.evaluate(() => {
    const probe = document.createElement('div'); document.body.append(probe);
    const resolve = (foreground, background, minimum) => {
      probe.style.cssText = `color:${foreground};background:${background}`;
      const style = getComputedStyle(probe);
      return { foreground: style.color, background: style.backgroundColor, minimum };
    };
    const result = {
      body: resolve('var(--ink)', 'var(--paper)', 4.5),
      muted: resolve('var(--muted)', 'var(--paper)', 4.5),
      accent: resolve('var(--accent)', 'var(--paper)', 4.5),
      disabled: resolve('var(--muted)', 'var(--soft)', 4.5),
      warning: resolve('var(--warn)', 'var(--warnbg)', 4.5),
      primaryAction: resolve('var(--onaccent)', 'var(--accent)', 4.5),
      listenAction: resolve('#fff', 'var(--accent-fill)', 4.5),
      focusRing: resolve('var(--focus)', 'var(--paper)', 3),
      controlBoundary: resolve('var(--control-line)', 'var(--paper)', 3),
    };
    probe.remove(); return result;
  });
  for (const [label, pair] of Object.entries(pairs)) {
    const ratio = contrast(hexColor(pair.foreground), hexColor(pair.background));
    assert.ok(ratio >= pair.minimum,
      `${route}/${theme} ${label} contrast must be at least ${pair.minimum}:1 (got ${ratio.toFixed(2)}:1, ${pair.foreground} on ${pair.background})`);
  }
  assert.notEqual(pairs.warning.background, pairs.body.background,
    `${route}/${theme} warning surface stays distinct from the page`);
}

async function visit(browser, viewport, route, theme = 'light', zoom = false) {
  const size = zoom ? { width: Math.floor(viewport.width / 2), height: Math.floor(viewport.height / 2) } : viewport;
  const page = await browser.newPage({ viewport: { width: size.width, height: size.height }, timezoneId: 'America/New_York', colorScheme: theme });
  await page.setDefaultTimeout(9000);
  await page.addInitScript(() => {
    class StubUtterance { constructor(text) { this.text = text; this.rate = 1; } }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: StubUtterance, configurable: true });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      paused: false, speaking: false, getVoices: () => [],
      speak(utterance) { this.speaking = true; setTimeout(() => utterance.onstart?.(), 0); },
      pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.paused = false; this.speaking = false; },
    } });
  });
  await page.route('**/api/profile', request => request.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/generate**', request => request.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/edition**', request => request.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/corrections', request => request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(correctionsFixture) }));
  await page.route('**/api/revisions/**', request => {
    const parts = new URL(request.request().url()).pathname.split('/');
    const targetType = parts.at(-3); const targetId = parts.at(-2); const revision = parts.at(-1);
    const content = targetType === 'edition'
      ? { edition: correctionEditionRevisions[revision] || null }
      : { episode: { ...firstEpisode, id: targetId, title: firstEpisode.title } };
    return request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(content) });
  });
  await page.route('**/api/episodes', request => request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [], left_today: 3 }) }));
  await page.route('**/audition-audio/manifest.json', request => request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(auditionFixture) }));
  await page.route('**/content/editions.json', async request => {
    const response = await request.fetch();
    const data = structuredClone(editionsFixture);
    const firstStory = data.editions.find(item => item.kind === 'daily')?.sections?.[0];
    const secondSource = sourceFixture.find(source => source.id !== firstStory?.source_ids?.[0]);
    if (firstStory && secondSource) {
      firstStory.source_ids = [firstStory.source_ids[0], secondSource.id];
      firstStory.body = `${firstStory.body} A second fictional source supports this detail [2].`;
    }
    await request.fulfill({ response, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.route('**/dev/tests/fixtures/episodes.json', request => request.fulfill({ contentType: 'application/json', body: JSON.stringify({ episodes }) }));
  const suffix = route.startsWith('episode/') || route === 'episodes' ? '?episodes=1' : '';
  await page.goto(`${baseURL}/${suffix}#${route}`, { waitUntil: 'domcontentloaded' });
  if (route === 'today') await page.locator('main h1').waitFor();
  else if (route === 'topics') await page.locator('.topics-page h1').waitFor();
  else if (route === 'library') await page.locator('.library-page h1, main > h1').first().waitFor();
  else if (route === 'ask-episode') await page.locator('.episode-request-page h1').waitFor();
  else if (route === 'episodes') await page.locator('.episode-deck-page').waitFor();
  else if (route === 'preferences') await page.locator('main h1').filter({ hasText: 'Settings' }).waitFor();
  else if (route === 'corrections') await page.locator('main h1').filter({ hasText: 'Corrections' }).waitFor();
  else if (route.startsWith('episode/')) await page.locator('.episode-page h1').waitFor();
  return page;
}

async function assertStructure(page, route) {
  assert.equal(await page.locator('main').count(), 1, `${route}: one main landmark`);
  assert.equal(await page.locator('main h1').count(), 1, `${route}: page has one level-one heading`);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `${route}: no horizontal overflow`);
  const activeTabs = await page.locator('[data-nav][aria-current="page"]').count();
  if (['today', 'topics', 'library'].includes(route)) assert.equal(activeTabs, 1, `${route}: exactly one current tab`);
  if (route === 'preferences') {
    assert.equal(activeTabs, 1, `${route}: Settings has one current destination`);
    assert.equal(await page.locator('[aria-current="page"][data-nav="topics"]').count(), 1,
      `${route}: Settings is grouped under Topics`);
  }
  if (route === 'corrections') assert.equal(activeTabs, 1, `${route}: its Today root stays marked current`);
  if (route === 'topics' && await page.locator('.topics-settings').count()) assert.ok(await page.locator('.topics-settings').getAttribute('aria-label'));
  await page.keyboard.press('Tab');
  if (['today', 'topics', 'library'].includes(route)) assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('skip')), true, `${route}: keyboard reaches skip link first`);
  else assert.notEqual(await page.evaluate(() => document.activeElement?.tagName), 'BODY', `${route}: keyboard enters the page controls`);
  const focusStyle = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  assert.notEqual(focusStyle, 'none', `${route}: skip link has a focus style`);
  const controls = await page.locator('main button:visible, main a[href]:visible, main summary:visible, main input:visible, main textarea:visible, main select:visible, .main-nav a:visible').evaluateAll(elements => elements.map(el => {
    const rect = el.getBoundingClientRect();
    const target = el.matches('.cite') ? getComputedStyle(el, '::after') : null;
    return { inlineText: false, name: el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.tagName,
      width: target ? parseFloat(target.width) : rect.width, height: target ? parseFloat(target.height) : rect.height };
  }));
  // Links within the greeting sentence use the inline-text target exception.
  const small = controls.filter(item => !item.inlineText && (item.width < 44 || item.height < 44));
  assert.deepEqual(small, [], `${route}: interactive targets are at least 44 by 44: ${JSON.stringify(small.slice(0, 4))}`);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const clearance = await page.evaluate(() => {
    const main = document.querySelector('main');
    if (main.querySelector('[aria-modal="true"]')) return { lastBottom: 0, barTop: innerHeight };
    const last = [...main.querySelectorAll('a[href],button,summary,input,textarea,select')].filter(el => el.getClientRects().length).at(-1);
    const fixed = [...document.querySelectorAll('.main-nav,.mini-player')].filter(el => getComputedStyle(el).position === 'fixed' && el.getBoundingClientRect().height > 0 && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().right > main.getBoundingClientRect().left && el.getBoundingClientRect().left < main.getBoundingClientRect().right);
    return { lastBottom: last?.getBoundingClientRect().bottom || 0, lastName: last?.getAttribute('aria-label') || last?.innerText || last?.tagName,
      barTop: fixed.length ? Math.min(...fixed.map(el => el.getBoundingClientRect().top)) : innerHeight,
      fixedBars: fixed.map(el => ({ name: el.className, rect: { top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom, height: el.getBoundingClientRect().height } })),
      viewportHeight: innerHeight, mainPaddingBottom: getComputedStyle(main).paddingBottom, scrollY };
  });
  assert.ok(clearance.lastBottom <= clearance.barTop + 1, `${route}: last control clears fixed bars (${JSON.stringify(clearance)})`);
  if (route === 'episodes') assert.equal(await page.locator('.episode-deck-position[aria-live="polite"]').count(), 1, 'Deck position changes are announced');
  if (route === 'today') assert.equal(await page.locator('.today-thin-day').count(), 0, 'Thin-day count belongs in the greeting');
}

async function assertNoReflowClipping(page, route) {
  const issues = await page.evaluate(() => {
    const pageWidth = document.documentElement.scrollWidth;
    const textElements = [...document.querySelectorAll('main :is(h1,h2,h3,p,a,button,label,span,strong,small,summary),.main-nav :is(a,span)')]
      .filter(element => element.textContent.trim() && element.getClientRects().length);
    const clipped = pageWidth > innerWidth + 1 ? [`document overflows horizontally (${pageWidth} > ${innerWidth})`] : [];
    for (const element of textElements) {
      const rect = element.getBoundingClientRect();
      if (rect.left < -1 || rect.right > innerWidth + 1) {
        clipped.push(`${element.tagName.toLowerCase()}.${[...element.classList].join('.')} extends beyond viewport (${Math.round(rect.left)}..${Math.round(rect.right)} of ${innerWidth})`);
        continue;
      }
      const style = getComputedStyle(element);
      const lineClamp = Number.parseInt(style.getPropertyValue('-webkit-line-clamp'), 10);
      const clipsOwnText = ['hidden', 'clip'].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1 ||
        ['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1 ||
        style.textOverflow === 'ellipsis' || (Number.isFinite(lineClamp) && lineClamp > 0 && element.scrollHeight > element.clientHeight + 1);
      if (clipsOwnText) clipped.push(`${element.tagName.toLowerCase()}.${[...element.classList].join('.')} clips text (${element.scrollWidth}x${element.scrollHeight} in ${element.clientWidth}x${element.clientHeight})`);
    }
    return clipped;
  });
  assert.deepEqual(issues, [], `${route}: 200% zoom has no clipped text or controls: ${issues.slice(0, 8).join('; ')}`);
}

async function save(page, name) {
  fs.mkdirSync(shots, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => { if (!document.querySelector('[aria-modal="true"]')) document.activeElement?.blur(); });
  await page.screenshot({ animations: 'disabled', caret: 'hide', path: path.join(shots, `${name}.png`) });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const routes = ['today', 'edition/daily/daily-sample-001/daily-payments-001', 'library', 'topics', 'ask-episode', `episode/${firstEpisode.id}`, 'episodes', 'preferences', 'corrections'];
    for (const viewport of viewports) {
      for (const theme of ['light', 'dark']) {
        for (const route of routes) {
          const page = await visit(browser, viewport, route, theme);
          await assertThemeTokenContrast(page, theme, route);
          await assertStructure(page, route);
          if (route === 'corrections') assert.equal(await page.locator('.correction-entry').count(), 2, 'Corrections renders dated fictional entries');
          await save(page, `${route.replaceAll('/', '-')}-${viewport.name}-${theme}`);
          if (route === 'episodes') {
            assert.ok(await page.locator('.episode-deck-page[aria-modal="true"][aria-labelledby]').count(), 'Deck is a named modal dialog');
            assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('episode-deck-shuffle')), true, 'Tab from the dialog entry reaches the first control');
            await page.keyboard.press('Shift+Tab');
            assert.equal(await page.evaluate(() => document.activeElement === document.querySelector('.episode-deck-controls button:last-child')), true, 'Shift+Tab wraps inside the dialog');
            await page.keyboard.press('Escape');
            await page.waitForFunction(() => location.hash === '#today');
            await page.waitForFunction(() => document.querySelector('[data-nav="today"]')?.getAttribute('aria-current') === 'page');
            await page.waitForFunction(() => document.activeElement?.matches('[data-nav="today"]'));
          }
          if (route.startsWith('episode/')) {
            assert.match(await page.locator('.episode-citation summary').first().getAttribute('aria-label'), /^Source 1, /);
            assert.equal(await page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).isDisabled(), false, 'Recorded fixture audio offers a named Listen action');
            const noAudioEpisode = episodes.find(episode => !episode.audio_recorded);
            await page.goto(`${baseURL}/?episodes=1#episode/${noAudioEpisode.id}`, { waitUntil: 'domcontentloaded' });
            await page.locator('.episode-page h1').waitFor();
            const disabled = await page.locator('.episode-listen:disabled').first().evaluate(el => ({ color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor, opacity: getComputedStyle(el).opacity }));
            assert.equal(disabled.opacity, '1', 'Unavailable Listen controls are visibly disabled without fading text');
            assert.ok(contrast(hexColor(disabled.color), hexColor(disabled.background)) >= 4.5, 'Disabled Listen label retains 4.5:1 text contrast');
            await page.emulateMedia({ reducedMotion: 'reduce' });
            await page.goto(`${baseURL}/?episodes=1#episodes`, { waitUntil: 'domcontentloaded' });
            await page.locator('.episode-deck-track').waitFor();
            assert.equal(await page.locator('.episode-deck-track').evaluate(el => getComputedStyle(el).scrollBehavior), 'auto');
          }
          if (route.startsWith('edition/')) {
            const citation = page.locator('.story-sources [data-source]').first();
            assert.match(await citation.innerText(), /.+/, 'Expanded sources name their outlet');
          }
          await page.close();

          const zoomed = await visit(browser, viewport, route, theme, true);
          await assertStructure(zoomed, route);
          if (viewport.name === 'phone' && phoneReflowRoutes.includes(route)) {
            await assertNoReflowClipping(zoomed, route);
            await save(zoomed, `${route.replaceAll('/', '-')}-phone-200-${theme}`);
          }
          await zoomed.close();
        }
      }
    }

    const player = await visit(browser, viewports[0], 'today', 'light');
    await player.locator('.today-listen-pill').click();
    await player.locator('.mini-player').waitFor();
    await player.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const playerClearance = await player.evaluate(() => ({
      lastBottom: [...document.querySelectorAll('main a[href],main button,main summary,main input,main textarea,main select')].filter(el => el.getClientRects().length).at(-1)?.getBoundingClientRect().bottom || 0,
      navTop: document.querySelector('.main-nav').getBoundingClientRect().top,
      playerTop: document.querySelector('.mini-player').getBoundingClientRect().top,
      navBottom: document.querySelector('.main-nav').getBoundingClientRect().bottom,
      playerBottom: document.querySelector('.mini-player').getBoundingClientRect().bottom,
    }));
    assert.ok(playerClearance.playerBottom <= playerClearance.navTop + 1, `player clears the tab bar: ${JSON.stringify(playerClearance)}`);
    assert.ok(playerClearance.lastBottom <= playerClearance.playerTop + 1, `page content clears the player at scroll end: ${JSON.stringify(playerClearance)}`);
    await save(player, 'player-phone-light');
    await player.emulateMedia({ colorScheme: 'dark' }); await save(player, 'player-phone-dark');
    await player.close();

    const story = await visit(browser, viewports[0], 'today');
    await story.locator('.today-transcript').first().evaluate(details => { details.open = true; });
    await story.locator('.today-full-story-link').first().click();
    await story.locator('.edition-section').first().waitFor();
    const trigger = story.locator('.why-story-trigger').first();
    await trigger.scrollIntoViewIfNeeded(); await trigger.click();
    const dialog = story.locator('.why-story-dialog');
    await dialog.waitFor();
    assert.equal(await dialog.getAttribute('aria-labelledby'), 'why-story-heading');
    await story.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });
    assert.equal(await story.evaluate(() => document.activeElement?.classList.contains('why-story-trigger')), true, 'Why sheet Escape returns focus to its opener');
    await story.close();
    console.log(`Accessibility audit passed at phone, desktop and 200% reflow widths in light/dark themes. Screenshots: ${shots}`);
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
