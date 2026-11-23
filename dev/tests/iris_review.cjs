require('../test_guard.cjs').enforceTestEnvironment();

// Fictional regression evidence for Iris's first four build-order items.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL;
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(baseURL);
    const result = await page.evaluate(async () => {
      const { renderTopics } = await import('/src/topics/topics.js');
      const host = document.createElement('div'); host.className = 'topics-page'; document.querySelector('main').replaceChildren(host);
      const words = 'Follow payments, AI in finance, and health insurance in North Carolina. Skip crypto hype.';
      const topics = ['payments', 'AI in finance', 'health insurance in North Carolina'].map((phrase, i) => ({
        id: String(i), label: phrase, sourcePhrase: phrase, scope: 'Invented scope in North Carolina', include: [phrase], exclude: ['crypto hype'],
      }));
      renderTopics(host, topics, [], topics.map(t => t.id), [], { words, savedWords: words,
        interestBrief: { geography: 'North Carolina', exclusions: ['crypto hype'] } });
      return { descriptions: [...host.querySelectorAll('.topic-description')].map(p => p.textContent),
        exclusionCopy: host.querySelector('.topics-skipping')?.textContent || '',
        targets: [...host.querySelectorAll('.topic-pause')].map(b => ({ w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height })) };
    });
    assert.deepEqual(result.descriptions, [], 'Names are never repeated as descriptions');
    assert.equal(result.exclusionCopy, '', 'Topic rows avoid negative exclusion copy');
    assert.ok(result.targets.every(b => b.w >= 44 && b.h >= 44));
    const story = await page.evaluate(async () => {
      const { renderEdition } = await import('/src/edition/edition.js');
      const host = document.querySelector('main');
      const section = { id: 'story', heading: 'A fictional schedule changes', topic_ids: ['transport'],
        body: 'What happened: The timetable changed. [1] Riders have more options. [1]\n\nWhy it matters: Connections improve. [1]',
        source_ids: ['one'], read_takeaway: 'The timetable changed. [1]' };
      const actions = { sources: [{ id: 'one', publisher: 'Fictional Transit' }, { id: 'two', publisher: 'Fictional Council' }], interestFeedback() {} };
      renderEdition(host, { id: 'day', sections: [section] }, [], actions);
      const single = { read: host.querySelector('.story-takeaway')?.textContent.trim(), markers: host.querySelectorAll('.cite').length,
        feedback: host.querySelector('.story-interest-feedback').textContent.trim(),
        expanded: host.querySelector('.story-more-toggle').getAttribute('aria-expanded') };
      section.source_ids.push('two'); section.body += ' Another reference. [2] [1]';
      renderEdition(host, { id: 'day', sections: [section] }, [], actions);
      const markers = [...host.querySelectorAll('.citation-number')].map(c => c.textContent);
      actions.sources[0].url = 'https://fictional.example/timetable';
      renderEdition(host, { id: 'day', sections: [section] }, [], actions);
      section.audio_script = 'The timetable changed. [1] Riders have more options. [1]';
      renderEdition(host, { id: 'day', sections: [section] }, [], actions);
      return { single, markers, scriptMarkers: host.querySelectorAll('.audio-transcript .cite').length,
        read: host.querySelector('.story-source-line').textContent, url: host.querySelector('.story-source-line a').href };
    });
    assert.deepEqual(story.single, { read: 'Read: The timetable changed.', markers: 0, feedback: 'More like thisLess like this', expanded: 'true' });
    assert.deepEqual(story.markers, ['1.', '2.']);
    assert.equal(story.scriptMarkers, 0, 'Scripted paragraphs also collapse repeated citations');
    assert.match(story.read, /Fictional Transit/);
    assert.equal(story.url, 'https://fictional.example/timetable');
    const today = await page.evaluate(async () => {
      const { renderToday } = await import('/src/edition/edition.js');
      const sections = Array.from({ length: 3 }, (_, i) => ({ id: `story-${i}`, topic_ids: [`topic-${i}`],
        heading: `Fictional agency ${i} publishes the full timetable for a new route connecting the city centre with every surrounding district`,
        body: 'A stale short summary.', audio_script: { paragraphs: ['Fictional detail. '.repeat(160)] }, source_ids: [] }));
      const host = document.querySelector('main');
      renderToday(host, { id: 'day', kind: 'daily', local_date: '2026-11-17', sections,
        coverage: sections.map((s, i) => ({ topic_id: `topic-${i}`, state: 'covered' })),
        quick_hits: [{ title: 'Fictional service notice', outlet: 'Fictional Transit', url: 'https://fictional.example/notice', summary: 'A full summary. '.repeat(20) }] }, [], {});
      const quick = host.querySelector('.today-quick-hits');
      return { greeting: host.querySelector('h1').textContent, top: host.querySelectorAll('[data-jump-story]').length,
        meta: host.querySelector('.today-run-meta').textContent,
        thin: host.querySelector('.today-thin-day'), quick,
        headlines: [...host.querySelectorAll('.story-row-copy strong')].every(p => getComputedStyle(p).webkitLineClamp === 'none' && p.scrollHeight <= p.clientHeight + 1),
        summary: null };
    });
    assert.equal(today.greeting, 'Your daily briefing');
    assert.equal(today.top, 3); assert.match(today.meta, /^3 stories · about \d+ min$/);
    assert.equal(today.thin, null); assert.equal(today.quick, null); assert.ok(today.headlines);
    console.log('Topic descriptions preserve request scope, avoid negative copy, and keep switches touch-sized.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
