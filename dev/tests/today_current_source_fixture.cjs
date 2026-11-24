require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

const currentSources = [
  { id: 'today-source-a', publisher: 'Fictional Payments Desk', title: 'Current corrected settlement notice', published_at: '2026-11-20', number: 1, url: 'https://fictional.example.test/current-a' },
  { id: 'today-source-b', publisher: 'Fictional Harbor Desk', title: 'Current harbor service review', published_at: '2026-11-19', number: 2, url: 'https://fictional.example.test/current-b' },
  { id: 'today-source-c', publisher: 'Fictional Health Desk', title: 'Current clinic access notice', published_at: '2026-11-18', number: 3, url: 'https://fictional.example.test/current-c' },
  { id: 'today-source-d', publisher: 'Fictional Policy Desk', title: 'Current policy implementation update', published_at: '2026-11-17', number: 4, url: 'https://fictional.example.test/current-d' },
];
const withdrawn = {
  id: 'today-source-withdrawn', publisher: 'Fictional Archive Desk',
  title: 'Superseded withdrawn claim', published_at: '2026-11-16', number: 5,
  status: 'withdrawn', url: 'https://fictional.example.test/withdrawn',
};
const staleEmbedded = currentSources.map(source => ({ ...source,
  publisher: 'Fictional Archive Desk', title: `Superseded ${source.title}`,
  published_at: '2026-11-16', url: `https://fictional.example.test/old-${source.id}`,
}));
const stories = [
  { id: 'today-story-a', topic_id: 'fictional-payments', heading: 'A corrected fictional settlement update', source_ids: ['today-source-a', 'today-source-b'], primary_source_id: 'today-source-a', source_numbers: [1, 2] },
  { id: 'today-story-b', topic_id: 'fictional-health', heading: 'A fictional clinic access update', source_ids: ['today-source-c'], primary_source_id: 'today-source-c', source_numbers: [3] },
  { id: 'today-story-c', topic_id: 'fictional-policy', heading: 'A fictional policy implementation update', source_ids: ['today-source-d', 'today-source-b'], primary_source_id: 'today-source-d', source_numbers: [4, 2] },
];

async function run() {
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  const [{ renderEdition }, { toStoryModel }, { buildPlainTranscript }] = await Promise.all([
    import('../../src/edition/edition.js'),
    import('../../src/edition/story-model.js'),
    import('../../src/episodes/export.js'),
  ]);
  const registry = [...currentSources, withdrawn];
  const edition = {
    id: 'fictional-three-story-today', revision_id: 'fictional-three-story-today-r2',
    local_date: '2026-11-21', sections: stories.map(story => ({
      ...story, topic_ids: [story.topic_id],
      body: `${story.heading} remains current.`,
      lede: `${story.heading} remains current.`,
      sources: [staleEmbedded.find(source => source.id === story.source_ids[0]), withdrawn],
    })),
  };
  const rendered = [];
  for (const story of stories) {
    const section = edition.sections.find(row => row.id === story.id);
    const container = { innerHTML: '', dataset: {}, querySelectorAll: () => [], querySelector: () => null };
    renderEdition(container, edition, [], {
      sectionId: section.id, inline: true, expanded: true,
      sources: registry, listeningAvailable: false,
    });
    const sources = [...container.innerHTML.matchAll(/<button data-source="([^"]+)" aria-label="([^"]+)">([^<]+)<\/button>/gu)];
    const eligibleIds = story.source_ids.filter(id => currentSources.some(source => source.id === id));
    assert.deepEqual(sources.map(match => match[1]), eligibleIds,
      `${story.id} Today source rows follow current source_ids and exclude withdrawn history`);
    const expectedRows = eligibleIds.map(id => currentSources.find(source => source.id === id));
    const sourceDate = source => { const date = new Date(`${source.published_at}T12:00:00Z`); return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date); };
    assert.deepEqual(sources.map(match => match[2]),
      expectedRows.map(source => `Open source from ${source.publisher}`),
      `${story.id} Today source buttons have publisher-specific accessible names`);
    assert.deepEqual(sources.map(match => match[3]),
      expectedRows.map(source => `${source.publisher} · ${sourceDate(source)}`),
      `${story.id} Today source rows use the current publisher and date`);
    assert.ok(container.innerHTML.includes(`data-source="${expectedRows[0].id}"`),
      `${story.id} primary source action resolves the current registry row`);
    assert.doesNotMatch(container.innerHTML, /Superseded|today-source-withdrawn|fictional\.example\.test\/(?:old-|withdrawn)/u,
      `${story.id} hides stale embedded metadata and withdrawn sources`);

    const topicModel = toStoryModel({
      id: story.id, revision_id: edition.revision_id, headline: story.heading,
      source_ids: story.source_ids, primary_source_id: story.primary_source_id,
    }, { sources: registry });
    assert.deepEqual(topicModel.sources.map(source => source.url), expectedRows.map(source => source.url),
      `${story.id} Topic model resolves the same current source IDs and order`);
    rendered.push(container.innerHTML);
  }

  const transcript = buildPlainTranscript({
    id: edition.id, revision_id: edition.revision_id, title: 'Fictional daily source parity',
    sources: [withdrawn, currentSources[2], currentSources[0], currentSources[3], currentSources[1]],
    chapters: stories.map(story => ({ id: story.id, title: story.heading,
      sentences: [{ text: `${story.heading} remains current.`, source_ids: story.source_ids }] })),
  });
  for (const source of currentSources) {
    assert.ok(transcript.includes(source.title), `${source.id} appears in transcript source identity`);
    assert.ok(transcript.includes(source.url) === false, 'plain transcript does not print raw source URLs');
  }
  assert.doesNotMatch(transcript, /Superseded|today-source-withdrawn|fictional\.example\.test\/withdrawn/u,
    'transcript export excludes stale and withdrawn source history');
  for (const html of rendered) {
    for (const source of currentSources) {
      if (html.includes(source.id)) assert.ok(transcript.includes(source.title), `${source.id} label matches transcript identity`);
    }
  }

  const legacySource = {
    id: 'legacy-embedded-source', publisher: 'Fictional Legacy Desk',
    title: 'Legacy embedded source title', url: 'https://fictional.example.test/legacy',
  };
  const legacyWithdrawn = {
    id: 'legacy-embedded-withdrawn', publisher: 'Fictional Archive Desk',
    title: 'Legacy withdrawn source title', status: 'withdrawn',
    url: 'https://fictional.example.test/legacy-withdrawn',
  };
  const legacySection = {
    id: 'legacy-story', heading: 'A fictional legacy story', body: 'The embedded source remains available.',
    lede: 'The embedded source remains available.', source_ids: [legacySource.id],
    sources: [legacySource, legacyWithdrawn],
  };
  const legacyEdition = { id: 'fictional-legacy-edition', revision_id: 'legacy-r1', sections: [legacySection] };
  const legacyContainer = { innerHTML: '', dataset: {}, querySelectorAll: () => [], querySelector: () => null };
  renderEdition(legacyContainer, legacyEdition, [], {
    sectionId: legacySection.id, inline: true, expanded: true, listeningAvailable: false,
  });
  assert.match(legacyContainer.innerHTML, /data-source="legacy-embedded-source"/u,
    'standalone legacy renders keep an embedded source when no current registry is supplied');
  assert.match(legacyContainer.innerHTML, /<button data-source="legacy-embedded-source" aria-label="Open source from Fictional Legacy Desk">Fictional Legacy Desk<\/button>/u,
    'Today lists the publisher while the source action resolves its full details');
  assert.doesNotMatch(legacyContainer.innerHTML, /legacy-embedded-withdrawn|Legacy withdrawn source title|legacy-withdrawn/u,
    'legacy embedded-source fallback still excludes withdrawn source history');

  const emptyRegistryContainer = { innerHTML: '', dataset: {}, querySelectorAll: () => [], querySelector: () => null };
  renderEdition(emptyRegistryContainer, legacyEdition, [], {
    sectionId: legacySection.id, inline: true, expanded: true, sources: [], listeningAvailable: false,
  });
  assert.doesNotMatch(emptyRegistryContainer.innerHTML, /legacy-embedded-source|Legacy embedded source title/u,
    'an explicitly supplied current registry remains authoritative when it lacks the current source ID');
  console.log('Today source parity: current source rows match Topic/export identities; stale and withdrawn actions stay hidden.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
