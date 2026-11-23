require('../test_guard.cjs').enforceTestEnvironment();
const assert = require('node:assert/strict');

// Include the pure model and sentence regression tests in the tests-only gate.
async function run() {
  await Promise.all([import('./story_model.mjs'), import('./edition_preview_test.mjs')]);

  const { toStoryModel } = await import('../../src/edition/story-model.js');
  const longTitle = 'Fictional regional payment review explains settlement timing and customer notification requirements';
  const sources = toStoryModel({
    headline: 'A fictional payment update',
    sources: [
      { publisher: 'Example Ledger', title: longTitle, published_at: '2026-11-19', url: 'https://example.test/long-title' },
      { label: 'Sample Finance Desk', title: 'A fictional source with no publication date', updated_at: '2026-11-20', url: 'https://example.test/no-date' },
    ],
  }).sources;
  assert.deepEqual(sources, [
    { outlet: 'Example Ledger', title: longTitle, published_at: '2026-11-19', url: 'https://example.test/long-title' },
    { outlet: 'Sample Finance Desk', title: 'A fictional source with no publication date', updated_at: '2026-11-20', url: 'https://example.test/no-date' },
  ], 'source title stays complete, publication dates keep their meaning, and missing dates remain absent');

  const unlabeled = toStoryModel({
    sources: [{ title: 'An unattributed fictional source title without a date', url: 'https://example.test/unlabeled' }],
  }).sources[0];
  assert.equal(unlabeled.outlet, 'Source', 'an absent outlet gets a plain generic label');
  assert.equal(unlabeled.title, 'An unattributed fictional source title without a date');
  assert.ok(!Object.hasOwn(unlabeled, 'published_at'), 'no publication date is invented');
  assert.ok(!JSON.stringify(unlabeled).includes('undefined'), 'missing source metadata never renders as undefined');

  const sourceHistory = [
    {
      id: 'old-withdrawn-source', outlet: 'Fictional City Desk',
      title: 'Earlier fictional notice with the superseded claim', published_at: '2026-11-16',
      url: 'https://fictional.example.test/earlier-notice',
    },
    {
      id: 'current-context-source', outlet: 'Fictional Review Desk',
      title: 'Current fictional review background', published_at: '2026-11-19',
      url: 'https://fictional.example.test/review-background',
    },
    {
      id: 'current-correction-source', outlet: 'Fictional City Desk',
      title: 'Current fictional correction notice with the complete source title',
      published_at: '2026-11-18', updated_at: '2026-11-20',
      url: 'https://fictional.example.test/current-correction',
    },
  ];
  const correctedRevision = {
    id: 'fictional-history-story', revision_id: 'fictional-history-r2',
    headline: 'A fictional corrected city notice',
    body: 'The corrected fictional notice reports twelve participants.',
    corrected: true, updated: true, corrected_at: '2026-11-20T12:00:00Z',
    source_ids: ['current-context-source', 'current-correction-source'],
    primary_source_id: 'current-correction-source',
    asked: 'PRIVATE REQUEST: keep confidential project Maple Finch out of the story.',
  };
  const correctedHistoryModel = toStoryModel(correctedRevision, { sources: sourceHistory });
  assert.deepEqual(correctedHistoryModel.sources.map(source => source.url), [
    'https://fictional.example.test/review-background',
    'https://fictional.example.test/current-correction',
  ], 'the corrected story selects its current sources from history and excludes the superseded source');
  assert.deepEqual(correctedHistoryModel.primary_source, {
    outlet: 'Fictional City Desk', url: 'https://fictional.example.test/current-correction',
    title: 'Current fictional correction notice with the complete source title',
    published_at: '2026-11-18', updated_at: '2026-11-20',
  }, 'the explicit current source ID selects its matching current citation');
  assert.ok(!JSON.stringify(correctedHistoryModel).includes('Earlier fictional notice'));
  assert.ok(!JSON.stringify(correctedHistoryModel).includes('Maple Finch'));

  const withdrawnRevision = {
    ...correctedRevision,
    revision_id: 'fictional-history-r3',
    headline: 'The corrected fictional notice reports twelve participants',
    body: 'The fictional notice was withdrawn after review.',
    withdrawn: true, withdrawn_at: '2026-11-21T08:30:00Z',
  };
  const withdrawnHistoryModel = toStoryModel(withdrawnRevision, { sources: sourceHistory });
  assert.equal(withdrawnHistoryModel.headline, 'Story withdrawn');
  assert.equal(withdrawnHistoryModel.withdrawn_at, '2026-11-21T08:30:00Z');
  assert.equal(withdrawnHistoryModel.primary_source.url, 'https://fictional.example.test/current-correction');
  assert.equal(withdrawnHistoryModel.primary_source.published_at, '2026-11-18');
  assert.equal(withdrawnHistoryModel.primary_source.updated_at, '2026-11-20');
  assert.ok(!JSON.stringify(withdrawnHistoryModel).includes('twelve participants'));
  assert.ok(!JSON.stringify(withdrawnHistoryModel).includes('Earlier fictional notice'));
  assert.ok(!JSON.stringify(withdrawnHistoryModel).includes('Maple Finch'));

  const reloadedDeepDive = toStoryModel({
    id: 'fictional-deep-dive', revision_id: 'fictional-deep-dive-r2',
    headline: 'A corrected fictional planning notice', corrected: true,
    sources: [
      {
        outlet: 'Fictional Planning Desk', title: 'Earlier fictional planning notice',
        published_at: '2026-11-18', updated_at: '2026-11-19',
        url: 'https://planning.example.test:443/notice',
      },
      {
        outlet: 'Fictional Planning Desk',
        title: 'Complete corrected fictional planning notice with the current source details',
        published_at: '2026-11-18', updated_at: '2026-11-20',
        url: 'https://PLANNING.example.test/notice',
      },
    ],
  });
  assert.deepEqual(reloadedDeepDive.sources, [{
    outlet: 'Fictional Planning Desk',
    title: 'Complete corrected fictional planning notice with the current source details',
    published_at: '2026-11-18', updated_at: '2026-11-20',
    url: 'https://planning.example.test/notice',
  }], 'a topic reload retains the original publication date and latest correction date');

  const reorderedCurrentStory = toStoryModel({
    id: 'fictional-current-order', revision_id: 'fictional-current-order-r2',
    headline: 'A corrected fictional service update', corrected: true,
    source_ids: ['current-source-a', 'current-source-b'],
    primary_source_id: 'current-source-a',
    sources: [
      {
        id: 'withdrawn-source', withdrawn: true, outlet: 'Fictional Service Desk',
        title: 'Withdrawn historical source and superseded claim',
        published_at: '2026-11-15', url: 'https://service.example.test/withdrawn',
      },
      {
        id: 'current-source-b', outlet: 'Fictional Review Desk',
        title: 'Current fictional review source', published_at: '2026-11-20',
        url: 'https://service.example.test/current-review',
      },
      {
        id: 'current-source-a', outlet: 'Fictional Service Desk',
        title: 'Current corrected fictional service notice',
        published_at: '2026-11-19', updated_at: '2026-11-21',
        url: 'https://service.example.test/current-notice',
      },
    ],
  });
  assert.deepEqual(reorderedCurrentStory.sources.map(source => source.url), [
    'https://service.example.test/current-notice',
    'https://service.example.test/current-review',
  ], 'current citations follow source_ids order and exclude withdrawn history');
  assert.equal(reorderedCurrentStory.primary_source.url, 'https://service.example.test/current-notice',
    'the primary link points to the current first citation');
  assert.equal(reorderedCurrentStory.sources[0].published_at, '2026-11-19');
  assert.equal(reorderedCurrentStory.sources[0].updated_at, '2026-11-21');
  assert.ok(!JSON.stringify(reorderedCurrentStory).includes('Withdrawn historical source'));
  assert.ok(!JSON.stringify(reorderedCurrentStory).includes('service.example.test/withdrawn'));
}

run().catch(error => { console.error(error); process.exitCode = 1; });
