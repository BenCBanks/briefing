require('../test_guard.cjs').enforceTestEnvironment();

// Generated edition rendering checks use a synthetic API response; no external request or real voice is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile } = require('./helpers/profile.cjs');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const shots = process.env.BRIEFING_SHOT_DIR;

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
  if (shots) {
    fs.mkdirSync(shots, { recursive: true });
    await page.screenshot({ path: path.join(shots, `${name}-large-text-light.png`) });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.screenshot({ path: path.join(shots, `${name}-large-text-dark.png`) });
    await page.emulateMedia({ colorScheme: 'light' });
    if (name === 'today') {
      assert.equal(await page.locator('.today-completion').isVisible(), false, 'caught-up ending stays hidden until the daily audio completes');
      await page.evaluate(() => { document.querySelector('.today-completion').hidden = false; });
      await page.locator('.today-completion').scrollIntoViewIfNeeded();
      const clearance = await page.evaluate(() => ({
        endingBottom: document.querySelector('.today-completion').getBoundingClientRect().bottom,
        tabTop: document.querySelector('.main-nav').getBoundingClientRect().top,
      }));
      assert.ok(clearance.endingBottom <= clearance.tabTop + 1, `Caught-up ending clears the tab bar: ${JSON.stringify(clearance)}`);
      await page.screenshot({ path: path.join(shots, 'today-ending-large-text-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(shots, 'today-ending-large-text-dark.png') });
      await page.emulateMedia({ colorScheme: 'light' });
    }
  }
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
}

async function installSpeechStub(page) {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('generated-story-signals-seeded')) {
      localStorage.removeItem('briefing:v1:signals');
      sessionStorage.setItem('generated-story-signals-seeded', '1');
    }
    window.__spoken = [];
    class StubUtterance { constructor(text) { this.text = text; this.rate = 1; } }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: StubUtterance, configurable: true });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      paused: false, speaking: false,
      speak(utterance) { window.__spoken.push(utterance.text); window.__lastUtterance = utterance; setTimeout(() => utterance.onstart?.(), 0); },
      pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.paused = false; this.speaking = false; },
    } });
  });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    page.on('pageerror', error => console.error('Page error:', error.message));
    page.on('console', message => { if (message.type() === 'error') console.error('Console error:', message.text()); });
    const profile = { version: 1, words: 'Follow payments and AI in finance.', minutes: 12, background: null, muted: [], topics: [
      { id: 'payments', label: 'Payments', scope: 'Payment systems', include: ['payments'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' },
      { id: 'ai', label: 'AI in finance', scope: 'AI in financial services', include: ['AI in finance'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'AI in finance' },
      { id: 'digital', label: 'Digital payments', scope: 'Digital payment systems', include: ['digital payments'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'digital payments' },
    ] };
    await installSpeechStub(page);
    await page.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
    await page.route('**/api/edition**', async route => {
      const base = await route.fetch({ url: `${baseURL}/content/editions.json` });
      const editions = await base.json();
      const edition = structuredClone(editions.editions.find(item => item.kind === 'daily'));
      edition.coverage.push({ topic_id: 'ai', label: 'AI in finance', state: 'limited', reason: 'No candidate passed the quality checks.' });
      const firstCoveredTopic = edition.coverage.find(row => !['quiet', 'limited'].includes(row.state));
      const section = edition.sections.find(item => item.topic_ids?.includes(firstCoveredTopic.topic_id));
      section.heading = 'Medicare Administrative Contractors Announce a Nationwide Moratorium on Enrollment of New Hospice Providers';
      section.body = 'wHaT hApPeNeD: The Federal Reserve Board changed its payment service prices for next year. [1]\n\nWHY IT MATTERS: Banks can plan for the new fees. [1]\n\nThings To Consider: The schedule takes effect in January. [1]\n\nLimits: Only one source was available.';
      section.summary = 'The change sets next year’s fees for a key payment service.';
      section.lead_paragraphs = ['Payment service pricing is changing for next year. [1]', 'The update affects participating institutions.'];
      section.why_it_matters = 'The schedule gives institutions time to plan. [1]';
      section.read_takeaway = 'Payment service fees change next year.';
      section.source_ids = ['generated-test-source'];
      section.source_status = 'OFFICIAL · PRIMARY AUTHORITY';
      section.source_status_detail = 'official source';
      section.kicker = 'Update:';
      const nextSection = edition.sections.find(item => item.id !== section.id);
      nextSection.source_ids = ['generated-test-source'];
      nextSection.source_status = 'single-source';
      nextSection.body = 'Page copy should disappear when the approved spoken script is available. [1]';
      nextSection.audio_script = {
        orientation: 'First up.',
        paragraphs: [
          { section: 'what_happened', text: 'The spoken fixture says the fictional office changed its schedule.' },
          { section: 'why_it_matters', text: 'That schedule gives fictional riders time to plan.' },
        ],
        source_attribution: 'Reporting from Federal Reserve.',
        text: 'First up.\n\nThe spoken fixture says the fictional office changed its schedule.\n\nWhy it matters.\n\nThat schedule gives fictional riders time to plan.\n\nReporting from Federal Reserve.',
      };
      const linkSection = edition.sections.find(item => item.id === 'daily-digital-001');
      linkSection.kind = 'link';
      linkSection.heading = 'Digital payment service update';
      linkSection.url = 'https://www.federalreserve.gov/example-link';
      linkSection.source_ids = ['generated-test-source'];
      linkSection.published_at = '2026-11-16T12:00:00Z';
      delete linkSection.body;
      edition.id = 'generated-test-edition';
      edition.local_date = '2026-11-17';
      edition.revision_id = 'generated-test-r2';
      edition.generated = true;
      edition.title = 'Your briefing · Tuesday 17 November';
      edition.summary = 'A generated briefing about changes to important public services.';
      edition.availability_note = 'Supply note: Payments: 1 verified story available.';
      edition.thin_day = true;
      edition.quick_hits = [{ title: 'Weekend settlement window announced', outlet: 'Federal Reserve',
        sentences: ['Payment transfers will use a revised settlement window.', 'The notice applies to participating institutions.'],
        source_id: 'generated-test-source', url: 'https://www.federalreserve.gov/quick-hit' }];
      edition.also_today = { label: 'Also today · 3 more', items: [
        { title:'Payment schedule guide published', outlet:'Federal Reserve', url:'https://www.federalreserve.gov/quick-one' },
        { title:'Regional lenders expand weekend access', outlet:'Cedar News', url:'https://cedar.example.test/quick-two' },
        { title:'Transfer network posts service update', outlet:'Harbor Journal', url:'https://harbor.example.test/quick-three' },
      ] };
      const older = structuredClone(edition);
      older.id = 'generated-test-yesterday';
      older.revision_id = 'generated-yesterday-r1';
      older.local_date = '2026-11-16';
      older.title = 'Your briefing · Monday 16 November';
      older.sections = [structuredClone(section)];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generated: true, edition, sources: [{
        id: 'generated-test-source', publisher: 'Federal Reserve', title: 'Payment service prices', published_at: '2026-11-17',
        url: 'https://www.federalreserve.gov/example', kind: 'RSS', note: 'Fixture',
      }], history: [{ edition: older, sources: [{
        id: 'generated-test-source', publisher: 'Federal Reserve', title: 'Payment service prices', published_at: '2026-11-16',
        url: 'https://www.federalreserve.gov/example', kind: 'RSS', note: 'Fixture',
      }] }] }) });
    });
    await seedInterestProfile(page, profile);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    assert.equal(await page.locator('.today-availability-note').count(), 0,
      'Today never exposes internal topic supply diagnostics');
    assert.equal(await page.getByText(/Supply note:|verified story available|Worth knowing references exhausted/i).count(), 0,
      'Today does not show supply diagnostics or apology copy');
    if (shots) {
      fs.mkdirSync(shots, { recursive: true });
      await page.emulateMedia({ colorScheme: 'light' });
      await page.screenshot({ path: path.join(shots, 'today-supply-gap-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(shots, 'today-supply-gap-dark.png') });
      await page.emulateMedia({ colorScheme: 'light' });
    }
    const row = page.locator('.today-story-row').first();
    const rowText = await row.locator('.today-chapter-title').innerText();
    assert.equal(/\[1\]|OFFICIAL|SOURCE-BACKED|Corrected/.test(rowText), false, 'Today omits citation syntax, confidence labels and unlinked correction tags');
    assert.equal(await row.locator('.today-chapter-title').count(), 1, 'The story title appears once as its chapter heading');
    assert.equal(await row.locator('.today-chapter-kicker').innerText(), '1 · UPDATE: · PAYMENTS', 'Follow-up kicker stays in the chapter overline');
    assert.match(await page.locator('.today-date').innerText(), /^Tuesday 17 November$/);
    assert.ok(await page.locator('.today-chapter-title').count() >= 1);
    assert.equal(await page.locator('.today-thin-day,.today-link-row').count(), 0, 'No banner or raw peripheral items');
    assert.equal(await page.locator('.today-quick-hits li').count(), 3, 'The server-filtered remainder renders as three compact quick hits');
    assert.deepEqual(await page.locator('.today-quick-hits li a span').allTextContents(), ['Federal Reserve','Cedar News','Harbor Journal']);
    assert.doesNotMatch(await page.locator('.today-quick-hits').innerText(), /Weekend settlement window announced|revised settlement window/, 'Raw quick hits and summaries stay hidden');
    assert.equal(await page.locator('.today-completion').isVisible(), false, 'Today hides the ending until playback is complete');
    assert.equal(await page.locator('.today-completion strong').textContent(), "You're caught up for today.");
    assert.equal(await page.locator('.today-completion span').textContent(), 'Next briefing tomorrow morning');
    assert.equal(await page.locator('.today-quiet-line').count(), 0,
      'Today does not expose a topic gap note');
    await captureLargeText(page, 'today');
    assert.doesNotMatch(await page.locator('.today-page').innerText(), /Nothing happened today|No stories today|could not be loaded|coverage was limited/i,
      'Today ends with calm, plain copy instead of negative or source-limit language');
    assert.equal(await page.locator('.today-transcript').first().evaluate(details => details.open), false, 'Thin editions keep transcripts folded until requested');

    await page.locator('.today-earlier-briefings-link').click();
    await page.locator('.library-briefing-list').waitFor();
    assert.equal(await page.locator('.library-briefing-link').count(), 2, 'Earlier dates are kept in the Library briefing list');
    assert.match(await page.locator('.library-briefing-link').first().innerText(), /Tuesday 17 November/, 'Briefing dates are newest first');
    await page.locator('.library-briefing-link').nth(1).click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.reader-story-header h1').innerText(), 'Medicare Administrative Contractors Announce a Nationwide Moratorium on Enrollment of New Hospice Providers', 'Earlier briefing dates open their own story');
    assert.equal(await page.locator('.reader-back').innerText(), '‹ Library', 'Library briefing returns to Library');
    await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
    await page.locator('.today-story-row').first().waitFor();

    assert.equal(await row.locator('.story-interest-feedback').count(), 0, 'Today uses edition feedback only');
    await row.locator('.today-transcript').evaluate(details => { details.open = true; });
    await row.locator('.today-full-story-link').click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.reader-story-header h1').innerText(), 'Medicare Administrative Contractors Announce a Nationwide Moratorium on Enrollment of New Hospice Providers', 'The Story page retains the full headline');
    if (shots) {
      fs.mkdirSync(shots, { recursive: true });
      await page.emulateMedia({ colorScheme: 'light' });
      await page.screenshot({ path: path.join(shots, 'story-long-headline-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(shots, 'story-long-headline-dark.png') });
      await page.emulateMedia({ colorScheme: 'light' });
      await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
      await page.screenshot({ path: path.join(shots, 'story-long-headline-large-text-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(shots, 'story-long-headline-large-text-dark.png') });
      await page.emulateMedia({ colorScheme: 'light' });
      await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
    }
    assert.match(await page.locator('.story-source-line').innerText(), /Federal Reserve/);
    assert.equal(await page.locator('.reader-story-header .story-context-line').innerText(), 'Update: · Payments · 17 Nov', 'Follow-up kicker shares the muted topic/date line');
    assert.equal(await page.locator('.story-why-line').innerText(), 'Why you\'re seeing this: it matches “payments” in your interest brief.', 'Story explains the match using the reader’s original source phrase');
    const feedback = page.locator('.story-interest-feedback');
    assert.equal(await feedback.isVisible(), true, 'Feedback appears at the end of the rendered story');
    assert.equal(await feedback.getAttribute('aria-label'), 'More or less of this story', 'Story feedback is grouped with a clear accessible name');
    assert.ok((await feedback.locator('button').evaluateAll(buttons => buttons.every(button => button.getBoundingClientRect().height >= 44))), 'Both feedback choices retain accessible touch targets');
    await page.setViewportSize({ width: 195, height: 422 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Feedback reflows at 200 percent zoom');
    await page.setViewportSize({ width: 390, height: 844 });
    const storySectionId = await page.locator('.edition-section').getAttribute('id');
    await feedback.getByRole('button', { name: 'More like this' }).click();
    assert.equal(await feedback.getAttribute('open'), null, 'Saving feedback closes the choices');
    assert.ok(await feedback.getByRole('button', {name:'More like this'}).evaluate(el => el === document.activeElement), 'Feedback retains focus');
    let interestSignals = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')));
    assert.deepEqual(interestSignals.filter(item => ['more', 'less'].includes(item.type) && item.storyId === storySectionId).map(item => item.type), ['more'],
      'More stores exactly one active preference for this story');
    assert.equal(await feedback.getByRole('button', { name: 'More like this', includeHidden: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await feedback.getByRole('button', { name: 'Less like this', includeHidden: true }).getAttribute('aria-pressed'), 'false');
    assert.deepEqual({ type: interestSignals.at(-1).type, storyId: interestSignals.at(-1).storyId, topicId: interestSignals.at(-1).topicId },
      { type: 'more', storyId: storySectionId, topicId: 'payments' }, 'More records a one-tap signal for this story and topic');
    assert.equal(await page.locator('.why-story-dialog').count(), 0, 'Direct feedback does not open a nested sheet');
    await feedback.getByRole('button', { name: 'Less like this' }).click();
    interestSignals = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')));
    assert.deepEqual(interestSignals.filter(item => ['more', 'less'].includes(item.type) && item.storyId === storySectionId).map(item => item.type), ['more', 'less'], 'Switching to Less records a newer explicit signal that supersedes More');
    assert.equal(await feedback.getByRole('button', { name: 'Less like this', includeHidden: true }).getAttribute('aria-pressed'), 'true');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('body.is-reader .edition-section').waitFor();
    const persistedFeedback = page.locator('.story-interest-feedback');
    assert.equal(await persistedFeedback.getByRole('button', { name: 'Less like this', includeHidden: true }).getAttribute('aria-pressed'), 'true', 'The selected preference survives a reload');
    await persistedFeedback.getByRole('button', { name: 'More like this' }).click();
    interestSignals = await page.evaluate(() => JSON.parse(localStorage.getItem('briefing:v1:signals')));
    assert.equal(interestSignals.filter(item => ['more', 'less'].includes(item.type) && item.storyId === storySectionId).at(-1).type, 'more', 'More can replace Less with one tap');
    assert.equal(await persistedFeedback.getByRole('button', { name: 'More like this', includeHidden: true }).getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('.story-lead p').first().innerText(), /^Payment service pricing is changing for next year\./);
    assert.equal(await page.locator('.story-lead .cite').count(), 0, 'Single-source leads use the named source below');
    assert.equal(await page.locator('.story-lead p').count(), 1, 'V3 joins the lede into one short paragraph');
    assert.equal(await page.locator('.story-takeaway').innerText(), 'Read: Payment service fees change next year.', 'Story page shows the supplied one-sentence Read takeaway');
    assert.match(await page.locator('.story-source-line a').innerText(), /Federal Reserve/);
    assert.equal(await page.locator('.story-limits').count(), 0, 'Legacy negative source notes are omitted');
    assert.equal(await page.locator('.story-sources').getAttribute('aria-label'), 'Citations');
    assert.equal(await page.locator('.reader-story-header .story-source-status,.edition-summary-dek,.section-topic,.generated-label').count(), 0, 'Story header has no source authority, dek, generated label or duplicate topic tag');
    const renderedParagraphs = await page.locator('.edition-section p').allInnerTexts();
    assert.ok(renderedParagraphs.some(text=>text.startsWith('So what.')));
    assert.ok(renderedParagraphs.some(text=>text.startsWith('Worth watching:')));
    assert.ok(renderedParagraphs.every(text => !/^(?:What happened|Why it matters|Things to consider):/i.test(text)), 'Rendered body strips each matching heading label, regardless of casing');
    assert.equal(await page.locator('.edition-section .cite').count(), 0, 'A single source is named once instead of repeated numeric chips');
    assert.equal(await page.locator('.story-sources .citation-number').count(), 0, 'A single source is not numbered');
    const citation = page.locator('.story-sources [data-source]').first();
    assert.ok(await page.locator('.story-sources li').count() > 0, 'The numbered Sources list remains present');
    assert.match(await page.locator('.story-sources li').first().innerText(), /Federal Reserve/);
    assert.match(await page.locator('.story-progress').innerText(), /Story 1 of 3/, 'Story progress excludes link-only rows');

    const previousSpeechCount = await page.evaluate(() => window.__spoken.length);
    await page.locator('.story-listen').click();
    await page.waitForFunction(count => window.__spoken.length > count, previousSpeechCount);
    assert.ok(await page.evaluate(() => window.__spoken.length > 0));
    assert.equal(await page.evaluate(() => window.__spoken.some(text => /\[\s*\d/.test(text))), false, 'Narration strips citation markers');
    assert.equal(await page.evaluate(() => window.__spoken.some(text => /^(?:What happened|Why it matters|Things to consider):/i.test(text))), false, 'Narration matches the visible, de-labeled story text');
    await citation.click();
    await page.locator('.source-actions').waitFor();
    assert.equal(await page.locator('.source-published').innerText(), 'Document date · 17 Nov', 'Source dates use a plain day and month');
    assert.match(page.url(), /#source\/generated-test-source$/);
    assert.match(await page.locator('.reader-back').innerText(), /Story 1$/, 'Source Back names its story parent');
    assert.equal(await page.locator('.source-sample-tag').count(), 0, 'A real source is never labeled Sample · fictional');
    await page.locator('.reader-back').click();
    await page.locator('body.is-reader .edition-section').waitFor();

    const missingStandardFields = await page.evaluate(async () => {
      const { renderEdition } = await import('/src/edition/edition.js');
      const host = document.createElement('div');
      renderEdition(host, { id: 'legacy-story', kind: 'daily', revision_id: 'legacy-r1', local_date: '2026-11-17',
        sections: [{ id: 'legacy-section', heading: 'Older story shape', topic_ids: [], source_ids: [],
          body: 'What happened: A useful update occurred.\n\nWhy it matters: Readers can use the update.\n\nLimits: One outlet reported this.' }] }, [], { listeningAvailable: false });
      return { lead: host.querySelector('.story-lead')?.textContent, takeaway: host.querySelector('.story-takeaway'), why: host.querySelector('.story-so-what')?.textContent,
        limits: host.querySelector('.story-limits')?.textContent };
    });
    assert.deepEqual(missingStandardFields, { lead: 'A useful update occurred.', takeaway: null, why: 'So what. Readers can use the update.', limits: undefined }, 'Older story records render their available body without legacy source notes without empty standard placeholders');
    const next = page.locator('.next-story');
    assert.equal(await next.locator('small').evaluate(element => getComputedStyle(element).whiteSpace), 'nowrap', 'Next story label cannot wrap into a narrow stack');
    assert.equal(await next.evaluate(element => getComputedStyle(element).flexShrink), '0', 'Next story control does not shrink');
    await next.click();
    await page.locator('body.is-reader .edition-section').waitFor();
    assert.equal(await page.locator('.edition-section.audio-transcript h2').count(), 0, 'No labelled blocks');
    const scriptText = await page.locator('.edition-section.audio-transcript').innerText();
    assert.match(scriptText, /The spoken fixture says the fictional office changed its schedule/);
    assert.match(scriptText, /That schedule gives fictional riders time to plan/);
    const scriptSpeechCount = await page.evaluate(() => window.__spoken.length);
    await page.locator('.story-listen').click();
    await page.waitForFunction(count => window.__spoken.length > count, scriptSpeechCount);
    assert.equal(await page.evaluate(() => window.__spoken.at(-1)), 'First up.', 'Listening starts with the first paragraph of the supplied script');
    await page.waitForFunction(() => document.querySelector('.mini-player-toggle')?.getAttribute('aria-label')?.startsWith('Pause'));
    assert.equal(await page.evaluate(() => window.__spoken.includes('The spoken fixture says the fictional office changed its schedule.')), false,
      'The following paragraph is not spoken until the first utterance ends');
    await page.evaluate(() => window.__lastUtterance.onend?.());
    await page.waitForFunction(() => window.__spoken.includes('The spoken fixture says the fictional office changed its schedule.'));
    assert.equal(await page.evaluate(() => window.__spoken.some(text => text.includes('Page copy should disappear'))), false,
      'Speech reads the audio script and never falls back to page copy');
    await page.locator('.mini-player-open').click();
    await page.getByRole('button', { name: 'Read along' }).click();
    await page.locator('.player-dialog').waitFor({ state: 'detached' });
    assert.match(await page.locator('.edition-section.audio-transcript').innerText(), /The spoken fixture says the fictional office changed its schedule/, 'Read along returns to the story');
    console.log('Generated story rendering: row cleanup, section labels, source status, citation navigation and narration passed.');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
