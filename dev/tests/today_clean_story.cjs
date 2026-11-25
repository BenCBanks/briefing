require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.route('**/src/app.js', route => route.fulfill({ contentType: 'application/javascript', body: '' }));
    await page.goto(baseURL);
    const evidence = await page.evaluate(async () => {
      const { renderToday } = await import('/src/edition/edition.js');
      const headlines = [
        'Transit agency changes its weekend timetable',
        'A very long fictional headline about how a regional transit agency changes its weekend timetable across every route in three neighboring counties',
        'Council publishes a new service calendar',
      ];
      const sections = headlines.map((headline, index) => ({
        id: `fictional-${index}`, heading: headline, headline,
        topic_ids: [`topic-${index}`], source_ids: [`source-${index}`],
        lede: `${headline}. Riders can check the revised times before traveling. [1]`,
        so_what: 'That check can help avoid a missed connection on a busy weekend.',
        more: 'The timetable includes a separate note for holiday service.',
        body: `${headline}. [1] Riders can check the revised times before traveling. [1]`,
      }));
      const sources = sections.map((section, index) => ({
        id: `source-${index}`, publisher: `Fictional Publisher ${index + 1}`,
        title: 'Fictional timetable notice', published_at: '2026-11-17',
        url: `https://publisher-${index}.example.test/notice`,
      }));
      const host = document.querySelector('main');
      renderToday(host, {
        id: 'fictional-day', revision_id: 'fictional-r1', kind: 'daily', local_date: '2026-11-18',
        sections, coverage: sections.map((section, index) => ({ topic_id: `topic-${index}`, state: 'covered' })),
      }, [], { sources, followedTopicCount: 3, listeningAvailable: true });
      const defaultDisclosures = [...host.querySelectorAll('.today-transcript')].map(details => ({
        open: details.open, label: details.querySelector('summary')?.innerText.trim() || '',
        height: Math.round(details.querySelector('summary')?.getBoundingClientRect().height || 0),
      }));
      host.querySelectorAll('.today-transcript').forEach(details => { details.open = true; });
      const rows = [...host.querySelectorAll('.today-story-row')];
      const toggles = [...host.querySelectorAll('.story-more-toggle')];
      const toggleState = button => ({
        label: button.innerText.trim(), expanded: button.getAttribute('aria-expanded'),
        border: getComputedStyle(button).borderTopWidth,
        background: getComputedStyle(button).backgroundColor,
        chevron: button.querySelector('svg path')?.getAttribute('d') || '',
      });
      const closedToggle = toggleState(toggles[0]);
      toggles.forEach(button => button.click());
      return { defaultDisclosures, rows: rows.map(row => ({
        heading: row.querySelector('.inline-story-heading').textContent.trim(),
        summary: row.querySelector('.today-story-summary')?.innerText.trim() || '',
        summaryColor: row.querySelector('.today-story-summary') ? getComputedStyle(row.querySelector('.today-story-summary')).color : '',
        summarySize: row.querySelector('.today-story-summary') ? getComputedStyle(row.querySelector('.today-story-summary')).fontSize : '',
        storyText: row.querySelector('.inline-story').innerText,
        expandedTextColor: getComputedStyle(row.querySelector('.story-more p')).color,
        bodyTextColor: getComputedStyle(row.querySelector('.story-lead p')).color,
        markerCount: row.querySelectorAll('.inline-story .citation-number, .inline-story .cite, .inline-story [data-cite]').length,
        sourceLists: row.querySelectorAll('.inline-story-sources').length,
        sourceText: row.querySelector('.inline-story-sources')?.innerText || '',
        sourceLine: row.querySelector('.story-source-line > span')?.innerText.trim() || '',
        toggle: toggleState(row.querySelector('.story-more-toggle')),
        topHeadline: host.querySelectorAll('.today-chapter-title')[rows.indexOf(row)]?.innerText.trim() || '',
        topHeadlineFits: (() => { const title = host.querySelectorAll('.today-chapter-title')[rows.indexOf(row)]; return title.scrollHeight <= title.clientHeight + 1; })(),
        closedToggle: rows.indexOf(row) === 0 ? closedToggle : null,
      })) };
    });
    assert.equal(evidence.rows.length, 3, 'a three-story day renders all three stories');
    assert.equal(await page.locator('.today-date').innerText(), 'Wednesday 18 November',
      'Today labels the edition with its actual publication date');
    for (const disclosure of evidence.defaultDisclosures) {
      assert.equal(disclosure.open, false, 'the longer story stays folded beneath its summary');
      assert.equal(disclosure.label, 'Read story and sources', 'the folded story action says what opens');
      assert.ok(disclosure.height >= 44, 'the folded story action has a 44px target');
    }
    for (const [index, row] of evidence.rows.entries()) {
      assert.equal(row.summary, 'Riders can check the revised times before traveling. That check can help avoid a missed connection on a busy weekend.',
        `story ${index + 1} shows a concise what-happened and why-it-matters summary`);
      assert.equal(row.summary.match(/[.!?](?:\s|$)/g)?.length, 2, 'the visible summary uses two sentences');
      assert.equal(row.summarySize, '17px', 'the visible summary uses comfortable body text');
      assert.equal(row.markerCount, 0, `story ${index + 1} has no inline citation markers`);
      assert.equal(row.sourceLists, 1, `story ${index + 1} has one source list at the end`);
      assert.equal(row.sourceLine, '', `story ${index + 1} has no duplicate source attribution`);
      assert.match(row.sourceText, new RegExp(`Fictional Publisher ${index + 1} · 17 Nov`));
      assert.equal(row.expandedTextColor, row.bodyTextColor, `story ${index + 1} expanded prose uses normal body color`);
      assert.equal(row.toggle.border, '0px', 'the expanded More control has no border box');
      assert.equal(row.toggle.background, 'rgba(0, 0, 0, 0)', 'the expanded More control has a plain background');
      assert.match(row.toggle.chevron, /m7 14 5-5 5 5/u, 'the open control points up');
      assert.equal(row.topHeadline, row.heading, `story ${index + 1} Top stories shows the complete headline`);
      assert.equal(row.topHeadlineFits, true, `story ${index + 1} Top stories wraps without clipping`);
      if (index === 0) assert.match(row.closedToggle.chevron, /m7 10 5 5 5-5/u, 'the closed control points down');
      assert.equal((row.storyText.match(new RegExp(row.heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length, 0,
        `story ${index + 1} body does not repeat its headline`);
      assert.match(row.storyText, /Riders can check the revised times/u, `story ${index + 1} keeps body detail`);
    }
    if (process.env.BRIEFING_SHOT_DIR) {
      const fs = require('node:fs');
      const path = require('node:path');
      fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
      await page.locator('.today-transcript').evaluateAll(details => details.forEach(item => { item.open = false; }));
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-three-stories-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-three-stories-dark.png') });
      await page.emulateMedia({ colorScheme: 'light' });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-three-stories-desktop-light.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator('.today-story-row').nth(1).scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-long-headline-light.png') });
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, 'today-long-headline-dark.png') });
    }
    console.log('Today story text: three-story day, long headline, clean prose, and single dated source list passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
