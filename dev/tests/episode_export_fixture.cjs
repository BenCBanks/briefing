require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episode-captions.json'), 'utf8'));

async function run() {
  const exporter = await import(pathToFileURL(path.resolve(__dirname, '../../src/episodes/export.js')));
  const previous = structuredClone(fixture);
  previous.revision_id = 'fictional-episode-r1';
  previous.chapters[0].sentences[0].text = 'Earlier fictional wording from before the correction.';
  const previousTranscript = exporter.buildPlainTranscript(previous);
  assert.match(previousTranscript, /Earlier fictional wording from before the correction\. \[1\]/);

  const current = structuredClone(fixture);
  current.revision_id = 'fictional-episode-r2';
  current.asked = 'PRIVATE REQUEST: include my confidential project name Cedar Lantern.';
  current.withdrawn_text = 'Withdrawn fictional claim about a canceled parcel program.';
  const earlierSource = {
    id: 'source-one', title: 'Superseded fictional procurement notice',
    published_at: '2026-11-17T12:00:00Z', url: 'https://fictional.example.test/old-procurement',
  };
  current.corrections = [{
    severity: 'material', previous_revision_id: 'fictional-episode-r1',
    revision_id: 'fictional-episode-r2', chapter_id: 'what-changed',
    corrected_at: '2026-11-21T15:00:00Z',
    what_changed: 'Earlier wording said the town canceled the parcel trial.',
  }];
  current.sources.reverse();
  current.sources.find(source => source.id === 'source-one').number = 1;
  current.sources.find(source => source.id === 'source-two').number = 2;
  current.sources.find(source => source.id === 'source-one').published_at = '2026-11-18T12:00:00Z';
  current.sources.find(source => source.id === 'source-two').published_at = '2026-11-19T12:00:00Z';
  current.sources.find(source => source.id === 'source-one').url = 'https://fictional.example.test/current-procurement';
  current.sources.find(source => source.id === 'source-two').url = 'https://fictional.example.test/current-survey';
  current.chapters[0].sentences[0].text = 'The corrected fictional report says Alder Bay will begin the parcel trial in June.';
  current.chapters[0].sentences[0].source_numbers = [2];
  current.chapters[1].sentences[0].source_numbers = [1];
  current.sources.push({
    id: 'stale-source', number: 3, outlet: 'Fictional Archive Desk',
    title: 'Superseded source for a withdrawn fictional claim', status: 'superseded',
    url: 'https://fictional.example.test/stale-source',
  });
  current.chapters.push({
    id: 'current-follow-up', title: 'A final review date is set',
    sentences: [{
      text: 'Alder Bay will review resident feedback before deciding on another parcel trial.',
      source_ids: ['source-two', 'stale-source'], source_numbers: [2, 3],
    }],
  });
  current.chapters.push({
    id: 'withdrawn-story', title: 'Superseded headline about a canceled parcel program',
    status: 'withdrawn', source_ids: ['stale-source'],
    sentences: [{
      text: 'The withdrawn claim said the parcel program had been canceled.',
      source_ids: ['stale-source'], source_numbers: [3],
    }],
  });
  current.sources.find(source => source.id === 'source-one').title = 'A fictional municipal procurement notice with a deliberately long title that must remain complete in transcript exports';

  const transcript = exporter.buildPlainTranscript(current);
  assert.match(transcript, /Corrected Nov 21, 2026/);
  assert.match(transcript, /The corrected fictional report says Alder Bay will begin the parcel trial in June\. \[1\]/);
  assert.doesNotMatch(transcript, /Earlier fictional wording from before the correction\.|Earlier wording said the town canceled the parcel trial|Superseded fictional procurement notice/);
  const expectedSourceList = [
    '[1] Alder Bay Civic Desk — A fictional municipal procurement notice with a deliberately long title that must remain complete in transcript exports (Published Nov 18, 2026)',
    '[2] North Harbor Ledger — Fictional resident survey (Published Nov 19, 2026)',
  ].join('\n');
  assert.equal(transcript.split('\n\nSources\n')[1], expectedSourceList,
    'source labels follow explicit citation numbers after source order changes');
  const assertCurrentRevisionDatesAndClaims = (text, output) => {
    assert.match(text, /^Corrected Nov 21, 2026$/mu, `${output} keeps the current correction date readable`);
    assert.match(text, /^\[1\] Alder Bay Civic Desk — .+ \(Published Nov 18, 2026\)$/mu,
      `${output} keeps the current source publication date readable`);
    assert.doesNotMatch(text,
      /Earlier fictional wording from before the correction\.|Earlier wording said the town canceled the parcel trial|Superseded headline|The withdrawn claim said/,
      `${output} excludes stale claims at the current revision`);
  };
  assertCurrentRevisionDatesAndClaims(transcript, 'plain transcript');
  assert.match(transcript, /Residents can collect packages during one trip downtown\. \[1, 2\]/);
  assert.match(transcript, /The town will review the trial before deciding whether to continue\. \[2\]/);
  assert.match(transcript, /Alder Bay will review resident feedback before deciding on another parcel trial\. \[2\]/,
    'a third current claim keeps its valid citation and excludes a stale source');
  assert.doesNotMatch(transcript, /PRIVATE REQUEST|Cedar Lantern|Withdrawn fictional claim about a canceled parcel program/);
  assert.match(transcript, /Story withdrawn/);
  assert.doesNotMatch(transcript, /Superseded headline|The withdrawn claim said|Superseded source for a withdrawn fictional claim|fictional\.example\.test|\[3\]/,
    'withdrawn claim text and source URLs stay out of the plain transcript');
  assert.equal(current.sources.find(source => source.id === earlierSource.id).url,
    'https://fictional.example.test/current-procurement', 'current source IDs resolve to refreshed URLs');

  const copied = [];
  const copyResult = await exporter.copyEpisodeTranscript(current, { writeText: async text => copied.push(text) });
  assert.equal(copyResult, transcript);
  assert.deepEqual(copied, [transcript]);
  assertCurrentRevisionDatesAndClaims(copyResult, 'copied transcript');
  assertCurrentRevisionDatesAndClaims(copied[0], 'clipboard text');

  const downloadEvents = [];
  let downloadedBlob;
  const downloadLink = {
    click() { downloadEvents.push('click'); },
    remove() { downloadEvents.push('remove'); },
  };
  const downloaded = exporter.downloadEpisodeTranscript(current, {
    documentRef: {
      createElement: tag => { assert.equal(tag, 'a'); return downloadLink; },
      body: { append: link => { assert.equal(link, downloadLink); downloadEvents.push('append'); } },
    },
    urlApi: {
      createObjectURL: blob => { downloadedBlob = blob; return 'blob:fictional-transcript'; },
      revokeObjectURL: url => { assert.equal(url, 'blob:fictional-transcript'); downloadEvents.push('revoke'); },
    },
    BlobConstructor: class { constructor(parts, options) { this.parts = parts; this.options = options; } },
  });
  assert.equal(downloaded, transcript);
  assertCurrentRevisionDatesAndClaims(downloaded, 'downloaded transcript');
  assert.equal(downloadLink.download, 'episode-transcript.txt');
  assert.deepEqual(downloadEvents, ['append', 'click', 'remove', 'revoke']);
  assert.deepEqual(downloadedBlob.parts, [transcript]);
  assertCurrentRevisionDatesAndClaims(downloadedBlob.parts[0], 'downloaded file contents');
  assert.doesNotMatch(downloadedBlob.parts[0], /Superseded headline|The withdrawn claim said|Fictional Archive Desk/);

  const missingMetadata = {
    id: 'fictional-metadata-gap', revision_id: 'fictional-metadata-gap-r2',
    title: 'A corrected fictional service report', corrected: true,
    asked: 'PRIVATE REQUEST: include the confidential project name Juniper Vale.',
    sources: [
      { id: 'current-valid-source', number: 1, outlet: 'Fictional Service Desk', title: 'Current fictional service notice', date: '18 Nov' },
      { id: 'current-unlabeled-source', number: 2, url: 'https://fictional.example.test/unlabeled' },
    ],
    chapters: [{
      title: 'Current report',
      sentences: [
        { text: 'The corrected report confirms the current service date.', source_ids: ['current-valid-source', 'current-unlabeled-source'], source_numbers: [1, 2] },
        { text: 'This sentence has no displayable source metadata.', source_ids: ['current-unlabeled-source'], source_numbers: [2] },
      ],
    }],
  };
  const metadataTranscript = exporter.buildPlainTranscript(missingMetadata);
  assert.match(metadataTranscript, /The corrected report confirms the current service date\. \[1\]/,
    'a valid current source citation is preserved');
  assert.match(metadataTranscript, /This sentence has no displayable source metadata\./);
  assert.doesNotMatch(metadataTranscript, /This sentence has no displayable source metadata\. \[2\]/);
  assert.doesNotMatch(metadataTranscript, /\[2\] undefined|PRIVATE REQUEST|Juniper Vale/);
  assert.match(metadataTranscript, /\[1\] Fictional Service Desk — Current fictional service notice \(Published 18 Nov\)/,
    'an incomplete source date is retained without inventing a year');

  console.log('Corrected episode transcript export keeps current text and citations, preserves long source titles, and excludes the private request.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
