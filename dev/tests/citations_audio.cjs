require('../test_guard.cjs').enforceTestEnvironment();

// Citation metadata remains inspectable without entering any narration path.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.goto(baseURL);
    const result = await page.evaluate(async () => {
      const { audioScriptText, audioScriptParagraphs } = await import('/src/audio-script.js');
      const { cleanSpeechText, narrationUnits } = await import('/src/player.js');
      const { renderEdition } = await import('/src/edition/edition.js');
      const body = 'What happened: The timetable changed. [1, 2]\n\nWhy it matters: Riders can plan.\n\nLimits: Secret metadata sentence.\n\nCitations:\n\n1. Other secret metadata.';
      const sections = [
        { body },
        { audio_script: body },
        { audio_script: { text: body } },
        { audio_script: { paragraphs: [
          { section: 'what_happened', text: 'The timetable changed.' },
          { section: 'limits', text: 'Secret metadata sentence.' },
          { section: 'citations', text: 'Other secret metadata.' },
          { section: 'why_it_matters', text: 'Riders can plan.' },
        ], source_attribution: 'Reporting from Fictional Transit.' } },
        { audio_script: ['The timetable changed.', 'Limits: Secret metadata sentence.', 'Riders can plan.'] },
      ];
      const speech = sections.map((section, index) => narrationUnits({ sections: [{ id: `story-${index}`, heading: 'Timetable', ...section }] }).map(unit => unit.text).join(' '));
      const host = document.createElement('div');
      document.body.append(host);
      renderEdition(host, { id: 'fictional', revision_id: 'r1', sections: [{ id: 'story', body, heading: 'Timetable', topic_ids: ['custom-transit'], source_ids: ['fictional-source', 'fictional-source-two'], read_takeaway: 'Check the timetable. [1]' }] }, [], {
        sources: [{ id: 'fictional-source', publisher: 'Fictional Transit', title: 'Timetable' }, { id: 'fictional-source-two', publisher: 'Fictional Council' }], listeningAvailable: false,
        profile: { topics: [{ id: 'custom-transit', label: 'Local transport' }] },
      });
      return { speech, direct: cleanSpeechText('A useful sentence. Limits: Secret metadata sentence.'),
        scripts: sections.slice(1).map(section => audioScriptParagraphs(section).join(' ')),
        storySpeech: narrationUnits({ sections: [{ id: 'story', heading: 'Timetable', body, read_takeaway: 'Check the timetable. [1]' }] }).map(unit => unit.text).join(' '),
        text: host.textContent, context: host.querySelector('.story-context-line').textContent,
        citationHeading: host.querySelector('.story-sources').getAttribute('aria-label'),
        citationNumbers: [...host.querySelectorAll('.citation-number')].map(el=>el.textContent),
        sourceCount: host.querySelectorAll('.story-sources li').length,
        attribution: audioScriptText(sections[3]),
      };
    });
    for (const text of [...result.speech, ...result.scripts, result.direct, result.text]) {
      assert.doesNotMatch(text, /Limits|Secret metadata|Other secret metadata/i);
    }
    for (const text of result.speech) {
      assert.doesNotMatch(text, /Citations|\[\s*\d/i);
      assert.match(text, /timetable changed/);
      assert.match(text, /Riders can plan/);
    }
    assert.equal(result.direct, 'A useful sentence.');
    assert.match(result.attribution, /Source: Fictional Transit\.$/);
    assert.equal(result.citationHeading, 'Citations');
    assert.deepEqual(result.citationNumbers, ['1.', '2.']);
    assert.equal(result.sourceCount, 2);
    assert.match(result.text, /So what\./);
    assert.match(result.text, /Read: Check the timetable\./, 'The supplied takeaway is visible on the story page');
    assert.doesNotMatch(result.storySpeech, /Read:|Check the timetable/, 'The editorial takeaway does not enter the narrated script');
    assert.match(result.context, /^Local transport/);
    console.log('Citations stay readable; legacy and generated narration exclude source-note text, and custom topics retain context.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
