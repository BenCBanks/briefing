require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

(async function run() {
const { renderWeeklyRecap } = await import('../../src/edition/edition.js');
const { buildPlainTranscript } = await import('../../src/episodes/export.js');

class FixtureNode {
  constructor() { this.html = ''; }
  set innerHTML(value) { this.html = String(value); }
  get innerHTML() { return this.html; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}

const current = {
  id: 'weekly-current-r2',
  publisher: 'Fictional Weekly Desk',
  title: 'Current corrected weekly synthesis source with a complete title',
  published_at: '2026-11-20',
  updated_at: '2026-11-22',
  number: 1,
  url: 'https://fictional.example.test/weekly-current',
};
const withdrawn = {
  id: 'weekly-withdrawn-r1',
  publisher: 'Fictional Archive Desk',
  title: 'Superseded weekly closure claim',
  published_at: '2026-11-18',
  number: 2,
  status: 'withdrawn',
  url: 'https://fictional.example.test/weekly-old',
};
const weekly = {
  id: 'weekly-parity-r2', kind: 'weekly', week_start: '2026-11-16', week_end: '2026-11-22',
  revision_id: 'weekly-parity-r2', title: 'A current fictional weekly synthesis',
  key_developments: [
    { title: 'Current weekly development', section_id: 'current-story' },
    { title: 'Superseded weekly closure claim', section_id: 'withdrawn-story' },
    { title: 'Old wording from an earlier revision', original_section_id: 'withdrawn-story' },
  ],
  sections: [
    { id: 'current-story', heading: 'Current weekly development', body: 'The current development remains in force.', source_ids: [withdrawn.id, current.id] },
    { id: 'withdrawn-story', status: 'withdrawn', heading: 'Superseded weekly closure claim', body: 'The withdrawn claim said the service had ended.', source_ids: [withdrawn.id] },
  ],
};

const container = new FixtureNode();
renderWeeklyRecap(container, weekly, [current, withdrawn]);
assert.match(container.innerHTML, /<span>1\.<\/span><span><strong>Fictional Weekly Desk<\/strong>/u,
  'current citation is numbered 1 after withdrawn sources are removed');
assert.match(container.innerHTML, /Fictional Weekly Desk/u, 'weekly citation keeps the current publisher');
assert.match(container.innerHTML, /Current corrected weekly synthesis source with a complete title/u,
  'weekly citation keeps the complete current title');
assert.match(container.innerHTML, /20 Nov/u, 'weekly citation keeps the current publication date');
assert.match(container.innerHTML, /data-weekly-source="weekly-current-r2"/u, 'weekly citation opens the current source identity');
assert.doesNotMatch(container.innerHTML, /Superseded weekly closure claim|weekly-withdrawn-r1|weekly-old/u,
  'withdrawn weekly claim and source link stay hidden');
assert.equal(new URL(current.url).protocol, 'https:', 'the current source fixture uses HTTPS');

const transcript = buildPlainTranscript({
  title: weekly.title,
  sources: [withdrawn, current],
  chapters: [
    { id: 'current-story', title: 'Current weekly development', sentences: [{ text: 'The current development remains in force.', source_ids: [current.id] }] },
    { id: 'withdrawn-story', status: 'withdrawn', title: withdrawn.title, sentences: [{ text: withdrawn.title, source_ids: [withdrawn.id] }] },
  ],
});
assert.match(transcript, /Fictional Weekly Desk — Current corrected weekly synthesis source with a complete title \(Published Nov 20, 2026\)/u,
  'transcript export keeps the same current publisher/title/date identity');
assert.match(transcript, /The current development remains in force\. \[1\]/u,
  'transcript citation number agrees with the current weekly source number');
assert.doesNotMatch(transcript, /Superseded weekly closure claim|weekly-old/u,
  'transcript export excludes the withdrawn claim and old link');
console.log('Weekly source parity: current title/date/HTTPS identity matches transcript export and withdrawn history stays hidden.');
})().catch(error => { console.error(error); process.exitCode = 1; });
