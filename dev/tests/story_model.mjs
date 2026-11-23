import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { alsoToday, buildGreeting, estimateReadTime, listenTime, shortHeadline, splitSentences, toStoryModel } from '../../src/edition/story-model.js';

const fixture = JSON.parse(await readFile(new URL('./fixtures/story-model-live-shape.json', import.meta.url), 'utf8'));
for (const first of [
  'U.S. peer-to-peer payment network Zelle is partnering with a bank.',
  'The U.K. network added a branch.',
  'Cedar Inc. added a branch.',
  'Mr. Banks met Dr. Green.',
  'Use local examples, e.g. a branch or i.e. a bank office.',
  'The desk opens at 9 a.m. and closes at 5 p.m.',
  'The price rose 3.14 percent from $1.25 to $1.50.',
  '“The pilot is ready.”',
]) {
  assert.deepEqual(splitSentences(`${first} Another sentence follows. A third follows!`),
    [first, 'Another sentence follows.', 'A third follows!']);
  assert.equal(toStoryModel({ body: `${first} Another sentence follows.` }).lede, `${first} Another sentence follows.`);
}
assert.deepEqual(splitSentences('A deal with Cedar Inc. Next comes review.'), ['A deal with Cedar Inc.', 'Next comes review.']);
assert.deepEqual(splitSentences('Cedar Inc. CEO meets U.S. Bank staff at 9 a.m. Monday. Next comes review.'), ['Cedar Inc. CEO meets U.S. Bank staff at 9 a.m. Monday.', 'Next comes review.']);
assert.deepEqual(splitSentences('Rates moved in the U.S. They rose in the U.K. The desk closed.'), ['Rates moved in the U.S.', 'They rose in the U.K.', 'The desk closed.']);
assert.deepEqual(splitSentences('“The desk closes at 5 p.m.” Then it reopens.'), ['“The desk closes at 5 p.m.”', 'Then it reopens.']);
assert.deepEqual(splitSentences('No terminal punctuation'), ['No terminal punctuation']);
assert.deepEqual(splitSentences(''), []);

const shortCases = [
  ['A faster checkout still needs a way back', 'A faster checkout'],
  ['Proof of identity should reveal only what is needed', 'Proof of identity'],
  ['Zelle Picks Solana for ZelleUSD Stablecoin', 'Zelle Picks Solana'],
  ['U.S. peer-to-peer payment network Zelle expands transfers to Canada', 'Zelle expands transfers'],
  ['Payments Giant Zelle Picks Solana for ZelleUSD Stablecoin', 'Zelle Picks Solana'],
  ['Post-quantum payments: Banks begin a trial', 'Post-quantum payments'],
  ['Medicaid Work-Rules Delay by Six Months for States', 'Medicaid Work-Rules Delay'],
  ['Breaking: The latest on Cedar Bank’s New Payment Network for Merchants', 'Cedar Bank’s New Payment Network'],
  ['Cedar Bank will not launch a new payment network', 'Cedar Bank'],
  ['Cedar extends enrollment in six additional regions this month', 'Cedar extends enrollment'],
];
for (const [headline, expected] of shortCases) {
  assert.equal(shortHeadline(headline), expected);
  assert.ok(shortHeadline(headline).split(/\s+/u).length <= 6);
  for (const word of expected.split(/\s+/u)) assert.ok(headline.includes(word), 'Every phrase word comes from its headline');
}
assert.equal(listenTime({duration_seconds: 180}, true).label, '3 min');
assert.equal(listenTime({duration_seconds: 181}, true).label, '4 min');
for (const value of [undefined, null, 0, -1, Infinity, 'unknown']) {
  assert.deepEqual(listenTime({duration_seconds: value}, true), {available: true, minutes: null, label: ''});
}
assert.deepEqual(listenTime({duration_seconds: 180}, false), {available: false, minutes: null, label: ''});
const legacy = toStoryModel(fixture.sections[0], { sources: fixture.sources, topics: [{ id: 'savings-rates', label: 'Savings rates' }] });
assert.equal(legacy.kicker, 'Savings rates');
assert.equal(legacy.headline, 'Savings account rates shift');
assert.equal(legacy.lede, 'Several fictional banks changed their savings rates this week. The sample figures are invented for this fixture. Readers can compare the published terms before moving money.');
assert.equal(legacy.so_what, 'A changed rate can affect the return on new deposits.');
assert.ok(legacy.more.some(paragraph => paragraph.includes('Additional context follows')));
assert.ok(!legacy.more.some(paragraph => paragraph.includes('Readers can compare')),
  'A sentence already used in the lede is not repeated in More');
assert.ok(!legacy.more.some(paragraph => paragraph.includes('promotional rate')),
  'Things to consider is mapped to watch without repeating in More');
assert.equal(legacy.watch, 'Check whether a promotional rate has an end date.');
assert.deepEqual(legacy.sources, [
  { outlet: 'Example Ledger', url: 'https://example.test/rates' },
  { outlet: 'Sample Finance Desk', url: 'https://example.test/terms' },
]);
assert.equal(legacy.primary_source.outlet, 'Example Ledger');

const supersededStory = {
  id: 'fictional-checkout-pilot', revision_id: 'fictional-story-r1',
  heading: 'A fictional checkout pilot',
  body: 'The earlier fictional pilot enrolled 120 merchants. That wording is superseded.',
  sources: [{ outlet: 'Fictional City Desk', title: 'Earlier fictional notice', published_at: '2026-11-16' }],
};
const privateRequest = 'PRIVATE REQUEST: include Cedar Lantern and details from my family account.';
const currentStory = {
  ...supersededStory,
  revision_id: 'fictional-story-r2',
  body: 'The current fictional pilot enrolled 12 merchants. The city will review results in June.',
  corrected: true,
  updated: true,
  corrected_at: '2026-11-20T14:30:00Z',
  correction_note: 'The count changed from 120 to 12.',
  sources: [{
    outlet: 'Fictional City Desk',
    title: 'Fictional checkout pilot review: the complete source title remains visible in the corrected story model',
    published_at: '2026-11-19',
    updated_at: '2026-11-20',
    url: 'https://fictional.example.test/checkout-pilot',
  }],
};
const currentStoryModel = toStoryModel(currentStory);
assert.equal(currentStoryModel.corrected, true, 'the current story correction status survives mapping');
assert.equal(currentStoryModel.updated, true);
assert.equal(currentStoryModel.corrected_at, '2026-11-20T14:30:00Z');
assert.ok(currentStoryModel.lede.includes('12 merchants'));
assert.ok(!JSON.stringify(currentStoryModel).includes('120 merchants'), 'superseded story text is excluded');
assert.equal(currentStoryModel.sources[0].published_at, '2026-11-19',
  'a correction keeps the source publication date');
assert.equal(currentStoryModel.sources[0].updated_at, '2026-11-20',
  'the newer source date remains labeled as an update');
assert.notEqual(currentStoryModel.sources[0].published_at, currentStoryModel.sources[0].updated_at,
  'the update date is not substituted for the publication date');
assert.deepEqual(currentStoryModel.sources, [{
  outlet: 'Fictional City Desk',
  title: 'Fictional checkout pilot review: the complete source title remains visible in the corrected story model',
  published_at: '2026-11-19',
  updated_at: '2026-11-20',
  url: 'https://fictional.example.test/checkout-pilot',
}]);
const withdrawnStory = {
  ...currentStory,
  revision_id: 'fictional-story-r3',
  heading: 'The fictional pilot enrolled 12 merchants',
  body: 'The fictional pilot was withdrawn after its evidence review.',
  withdrawn: true,
  withdrawn_at: '2026-11-21T09:15:00Z',
  asked: privateRequest,
};
const withdrawnStoryModel = toStoryModel(withdrawnStory);
assert.equal(withdrawnStoryModel.withdrawn, true);
assert.equal(withdrawnStoryModel.withdrawn_at, '2026-11-21T09:15:00Z');
assert.equal(withdrawnStoryModel.corrected, true, 'the current correction status remains visible');
assert.equal(withdrawnStoryModel.corrected_at, '2026-11-20T14:30:00Z');
assert.equal(withdrawnStoryModel.headline, 'Story withdrawn');
assert.equal(withdrawnStoryModel.lede, '');
assert.equal(withdrawnStoryModel.so_what, '');
assert.deepEqual(withdrawnStoryModel.more, []);
assert.equal(withdrawnStoryModel.watch, null);
assert.deepEqual(withdrawnStoryModel.sources, currentStoryModel.sources,
  'current source dates and complete titles remain available for a withdrawn story');
assert.ok(!JSON.stringify(withdrawnStoryModel).includes('120 merchants'));
assert.ok(!JSON.stringify(withdrawnStoryModel).includes('12 merchants'));
assert.ok(!JSON.stringify(withdrawnStoryModel).includes(privateRequest));
const previousStoryModel = toStoryModel(supersededStory);
assert.equal(previousStoryModel.corrected, false, 'the previous revision is not marked corrected');
assert.ok(previousStoryModel.lede.includes('120 merchants'));

const titleOnlyModel = toStoryModel({
  headline: 'A fictional source fallback story',
  asked: privateRequest,
  sources: [{
    title: 'Fictional municipal notice: the complete title remains available when the outlet is missing',
    updated_at: '2026-11-20',
    url: 'https://fictional.example.test/title-only-source',
  }],
}, { asked: privateRequest });
const titleOnlySource = titleOnlyModel.sources[0];
assert.deepEqual(titleOnlySource, {
  outlet: 'Source',
  title: 'Fictional municipal notice: the complete title remains available when the outlet is missing',
  updated_at: '2026-11-20',
  url: 'https://fictional.example.test/title-only-source',
});
assert.ok(!Object.hasOwn(titleOnlySource, 'published_at'), 'an update date is not labeled as a publication date');
assert.ok(!JSON.stringify(titleOnlyModel).includes(privateRequest), 'the source fallback excludes private request text');
assert.ok(!JSON.stringify(titleOnlyModel).includes('undefined'));

const urlSafetyModel = toStoryModel({
  headline: 'A fictional citation safety story',
  asked: privateRequest,
  sources: [
    {
      outlet: 'Fictional Records Desk',
      title: 'Complete title for a valid fictional HTTPS citation that should remain inspectable',
      published_at: '2026-11-19', updated_at: '2026-11-20',
      url: 'https://records.example.test/notices/complete-title',
    },
    { outlet: 'Script Source', title: 'Complete title with a script URL', published_at: '2026-11-18', url: 'javascript:alert(1)' },
    { outlet: 'Malformed Source', title: 'Complete title with a malformed URL', updated_at: '2026-11-20', url: 'https://[broken' },
    { outlet: 'Plain HTTP Source', title: 'Complete title with a non-HTTPS URL', published_at: '2026-11-17', url: 'http://records.example.test/notices' },
  ],
  primary_source: { outlet: 'Script Source', url: 'javascript:alert(1)' },
}, { asked: privateRequest });
assert.deepEqual(urlSafetyModel.sources, [
  {
    outlet: 'Fictional Records Desk',
    title: 'Complete title for a valid fictional HTTPS citation that should remain inspectable',
    published_at: '2026-11-19', updated_at: '2026-11-20',
    url: 'https://records.example.test/notices/complete-title',
  },
  { outlet: 'Script Source', title: 'Complete title with a script URL', published_at: '2026-11-18', url: '' },
  { outlet: 'Malformed Source', title: 'Complete title with a malformed URL', updated_at: '2026-11-20', url: '' },
  { outlet: 'Plain HTTP Source', title: 'Complete title with a non-HTTPS URL', published_at: '2026-11-17', url: '' },
]);
assert.equal(urlSafetyModel.primary_source.url, '', 'a primary source cannot bypass URL filtering');
assert.ok(!JSON.stringify(urlSafetyModel).match(/(?:javascript:|data:|https:\/\/\[broken|http:\/\/)/u));
assert.ok(!JSON.stringify(urlSafetyModel).includes(privateRequest));

const duplicateCitationModel = toStoryModel({
  headline: 'A corrected fictional publisher notice',
  body: 'The corrected fictional notice keeps its original publication date and a later update date.',
  corrected: true,
  updated: true,
  corrected_at: '2026-11-21T10:00:00Z',
  sources: [
    {
      outlet: 'Fictional Transit Desk',
      title: 'Fictional notice update',
      published_at: '2026-11-10',
      url: 'https://RECORDS.example.test:443/story',
    },
    {
      publisher: 'fictional transit desk',
      title: 'Complete fictional notice title with the full publication and correction context',
      published_at: '2026-11-10',
      updated_at: '2026-11-20',
      url: 'https://records.example.test/story',
    },
  ],
});
assert.equal(duplicateCitationModel.corrected, true);
assert.equal(duplicateCitationModel.sources.length, 1, 'canonical-equivalent publisher URLs produce one source');
assert.deepEqual(duplicateCitationModel.sources[0], {
  outlet: 'Fictional Transit Desk',
  title: 'Complete fictional notice title with the full publication and correction context',
  published_at: '2026-11-10',
  updated_at: '2026-11-20',
  url: 'https://records.example.test/story',
});

const modern = toStoryModel({
  kicker_topic: 'Health policy', headline: 'A long headline for a story',
  lede: ['The first concrete sentence is here. The second sentence adds context. A third sentence is allowed.'],
  so_what: 'This matters to families. A second sentence must be dropped.',
  more: ['Extra detail.'], watch: 'Watch for a final rule. A second thought is dropped.',
  sources: [{ outlet: 'Named Outlet', url: 'https://news.example.test/a' }], primary_source_index: 0,
});
assert.equal(modern.lede, 'The first concrete sentence is here. The second sentence adds context. A third sentence is allowed.');
assert.equal(modern.so_what, 'This matters to families.');
assert.deepEqual(modern.more, ['Extra detail.']);
assert.equal(modern.watch, 'Watch for a final rule.');
assert.equal(modern.primary_source.url, 'https://news.example.test/a');

const blocks = toStoryModel({
  title: 'Blocks also work',
  section_blocks: [
    { type: 'what happened', text: 'One sentence. Another sentence.' },
    { type: 'why_it_matters', text: 'A single consequence.' },
    { type: 'things_to_consider', text: 'Check next month. More details.' },
  ],
  sources: [{ name: 'Only Source', href: 'https://source.example.test' }],
});
assert.equal(blocks.lede, 'One sentence. Another sentence.');
assert.equal(blocks.so_what, 'A single consequence.');
assert.equal(blocks.watch, 'Check next month.');
assert.equal(blocks.primary_source.outlet, 'Only Source');
assert.equal(blocks.sources.length, 1, 'A single source remains valid');

const sparse = toStoryModel({ heading: 'Long '.repeat(24).trim(), body: 'A lone sentence with no other fields.' });
assert.equal(sparse.headline.split(' ').length, 24, 'Long headlines are preserved without truncation');
assert.equal(sparse.lede, 'A lone sentence with no other fields.');
assert.equal(sparse.so_what, '');
assert.deepEqual(sparse.more, []);
assert.equal(sparse.watch, null);
assert.deepEqual(sparse.sources, []);
assert.equal(sparse.primary_source, null);

const three = [
  { headline: 'Rates move at fictional banks' },
  { headline: 'A second invented market change' },
  { headline: 'A third fictional policy update' },
  { headline: 'This fourth headline is ignored' },
];
assert.match(buildGreeting(three), /^Three things today:/);
assert.ok(/[.!?]$/u.test(buildGreeting(three)), 'Greeting is a complete sentence');
assert.ok(buildGreeting(three).includes(three[2].headline));
assert.ok(!buildGreeting(three).includes(three[3].headline));
assert.ok(buildGreeting(three).trim().split(/\s+/u).length <= 35, 'Greeting never exceeds 35 words');
assert.match(buildGreeting([three[0]]), /^One thing today:/);
assert.match(buildGreeting(three.slice(0, 2)), /^Two things today:/);
assert.equal(buildGreeting([]), '');
const longGreeting = buildGreeting([{ headline: 'A '.repeat(50) }, { headline: 'B '.repeat(50) }, { headline: 'C '.repeat(50) }]);
assert.ok(longGreeting.split(/\s+/u).length <= 35);
assert.ok(/[.!?]$/u.test(longGreeting));

assert.deepEqual(estimateReadTime(''), { words: 0, minutes: 0, label: '0 min' });
assert.deepEqual(estimateReadTime('word '.repeat(100)), { words: 100, minutes: 1, label: '1 min' });
assert.deepEqual(estimateReadTime('word '.repeat(200)), { words: 200, minutes: 1, label: '1 min' });
assert.deepEqual(estimateReadTime('word '.repeat(201)), { words: 201, minutes: 2, label: '2 min' });
assert.equal(estimateReadTime([legacy]).minutes, 1, 'Mapped story time counts its actual visible words');
assert.equal(estimateReadTime('word '.repeat(2000)).label, '10 min');

const extras = [{ headline: 'One' }, { headline: 'Two' }, { headline: 'Three' }];
assert.deepEqual(alsoToday([]), []);
assert.deepEqual(alsoToday(extras.slice(0, 1)), []);
assert.equal(alsoToday(extras), extras, 'Two or more pre-filtered items pass through unchanged');

console.log('Story model: legacy/new mapping, thin-day greeting, 200 wpm estimate, and Also today passed.');
