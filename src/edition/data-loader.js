function validArray(value, name, revisioned = false) {
  if (!Array.isArray(value)) throw Error(`The ${name} list is unavailable.`);
  const ids = value.map(item => item?.id);
  const revisions = value.map(item => `${item?.id}:${item?.revision_id || ''}`);
  if (ids.some(id => typeof id !== 'string' || !id) ||
      (!revisioned && new Set(ids).size !== ids.length) ||
      (revisioned && (value.some(item => !item?.revision_id) || new Set(revisions).size !== revisions.length))) {
    throw Error(`The ${name} list has invalid IDs.`);
  }
  return value;
}

export function createEditionDataLoader(app) {
  async function readJson(name) {
    const response = await fetch(`/content/${name}.json`, { cache: 'no-cache' });
    if (!response.ok) throw Error(`${name} could not be loaded (${response.status}).`);
    return response.json();
  }

  async function readGeneratedEdition() {
    app.editionCheckSucceeded = false;
    try {
      const response = await fetch('/api/edition?profile=active&history=0', { cache: 'no-store' });
      // A 404 confirms that this profile has no generated edition; static content stays usable.
      if (response.status === 404) { app.editionCheckSucceeded = true; return null; }
      if (!response.ok) return null;
      const result = await response.json();
      app.editionCheckSucceeded = true;
      return (result?.generated === true && result.edition?.generated === true) || Array.isArray(result?.history)
        ? result : null;
    } catch {
      return null;
    }
  }

  async function loadEditionHistory(statusNode = null) {
    if (app.editionHistoryLoaded || !app.interestProfile || !app.pack) return;
    if (app.editionHistoryPromise) return app.editionHistoryPromise;
    app.editionHistoryPromise = (async () => {
      try {
        const response = await fetch('/api/edition?profile=active&history=1', { cache: 'no-store' });
        if (!response.ok) throw Error(`history request failed (${response.status})`);
        const result = await response.json();
        const known = new Set(app.pack.editions.map(item => `${item.id}:${item.revision_id}`));
        const bundles = Array.isArray(result.history) ? result.history : [];
        const historyEditions = bundles.map(bundle => bundle?.edition)
          .filter(edition => edition && typeof edition === 'object' && !known.has(`${edition.id}:${edition.revision_id}`));
        const historySources = bundles.flatMap(bundle => Array.isArray(bundle?.sources) ? bundle.sources : []);
        const sources = [...new Map([...app.pack.sources, ...(Array.isArray(result.sources) ? result.sources : []), ...historySources]
          .filter(source => source?.id).map(source => [source.id, source])).values()];
        app.pack = validatePack({ ...app.pack, sources, editions: [...app.pack.editions, ...historyEditions] });
        app.editionHistoryLoaded = true;
        if (app.location.hash === '#library' || app.location.hash.startsWith('#topic-detail/')) app.route();
      } catch {
        if (statusNode?.isConnected) {
          statusNode.replaceChildren(app.document.createTextNode('Earlier briefings could not be loaded. '));
          const retry = app.node('button', 'Retry'); retry.type = 'button';
          retry.addEventListener('click', () => {
            app.editionHistoryPromise = null;
            statusNode.textContent = 'Loading earlier briefings…';
            void loadEditionHistory(statusNode);
          });
          statusNode.append(retry);
        }
      } finally {
        app.editionHistoryPromise = null;
      }
    })();
    return app.editionHistoryPromise;
  }

  async function loadPack(includeLatestGenerated = false) {
    app.editionCheckSucceeded = true;
    const mayReadGenerated = includeLatestGenerated || (app.interestProfile &&
      app.localStorage.getItem('briefing:v1:built-profile-key') === app.profileBuildKey(app.interestProfile));
    const [topicFile, diveFile, sourceFile, editionFile, generated] = await Promise.all([
      readJson('topics'), readJson('deep-dives'), readJson('sources'), readJson('editions'),
      mayReadGenerated ? readGeneratedEdition() : Promise.resolve(null),
    ]);
    const topics = validArray(topicFile.topics, 'topics');
    const deepDives = validArray(diveFile.deep_dives, 'deep dives', true);
    const staticSources = validArray(sourceFile.sources, 'sources');
    const staticEditions = validArray(editionFile.editions, 'editions', true);
    const staticPack = validatePack({ topics, deepDives, sources: staticSources, editions: staticEditions });
    const generatedBundles = [
      ...(generated?.edition ? [{ edition: generated.edition, sources: generated.sources || [] }] : []),
      ...(Array.isArray(generated?.history) ? generated.history.filter(bundle => bundle && typeof bundle === 'object' && bundle.edition && typeof bundle.edition === 'object') : []),
    ];
    const generatedSources = [
      ...(Array.isArray(generated?.history) ? generated.history : []).flatMap(bundle => Array.isArray(bundle?.sources) ? bundle.sources : []),
      ...(Array.isArray(generated?.sources) ? generated.sources : []),
    ];
    try {
      const sources = validArray([...new Map([...staticSources, ...generatedSources]
        .filter(source => source && typeof source === 'object' && typeof source.id === 'string')
        .map(source => [source.id, source])).values()], 'sources');
      const generatedEditions = generatedBundles.map(bundle => bundle.edition).filter(edition => edition && typeof edition === 'object' && !Array.isArray(edition));
      const editions = validArray([...generatedEditions, ...staticEditions], 'editions', true);
      return validatePack({ topics, deepDives, sources, editions });
    } catch (error) {
      if (!generatedBundles.length && !generatedSources.length) throw error;
      // A malformed server edition must not take down the last good static pack.
      app.editionCheckSucceeded = false;
      return staticPack;
    }
  }

  function validatePack(data) {
    const topics = validArray(data.topics, 'topics');
    const deepDives = validArray(data.deepDives, 'deep dives', true);
    const sources = validArray(data.sources, 'sources');
    const editions = validArray(data.editions, 'editions', true);
    const topicIds = new Set(topics.map(x => x.id));
    const sourceIds = new Set(sources.map(x => x.id));
    for (const source of sources) {
      if (source.kind === 'Internal fictional evidence') {
        if (source.url) throw Error(`The ${source.id} internal source must not have an external link.`);
      } else {
        try { if (new URL(source.url).protocol !== 'https:') throw Error(); }
        catch { throw Error(`The ${source.id} source link is invalid.`); }
      }
    }
    for (const topic of topics) {
      if (!deepDives.some(item => item.topic_id === topic.id)) throw Error(`The ${topic.label} explanation is missing.`);
    }
    for (const item of [...editions, ...deepDives]) {
      if (!item.revision_id || !item.script_hash || !Array.isArray(item.sections) || !item.sections.length) throw Error(`The ${item.id} script is incomplete.`);
      for (const section of item.sections) {
        if (!section.id || !section.heading || (section.kind !== 'link' && !section.body) || !Array.isArray(section.source_ids) || (section.kind === 'link' && !section.source_ids.length)) throw Error(`The ${item.id} section is incomplete.`);
        if (section.source_ids.some(id => !sourceIds.has(id))) throw Error(`The ${item.id} section refers to an unavailable source.`);
      }
    }
    for (const edition of editions) {
      if (!Array.isArray(edition.coverage) || !edition.coverage.length) throw Error(`The ${edition.id} coverage is incomplete.`);
      const coverageIds = edition.coverage.map(row => row.topic_id);
      const uniqueCoverage = new Set(coverageIds).size === coverageIds.length;
      if (edition.generated === true) {
        const validGeneratedIds = coverageIds.every(id => typeof id === 'string' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id));
        const sectionTopicIds = new Set(edition.sections.flatMap(section => section.topic_ids || []));
        if (!uniqueCoverage || !validGeneratedIds || [...sectionTopicIds].some(id => !coverageIds.includes(id))) {
          throw Error(`The ${edition.id} coverage is inconsistent.`);
        }
      } else if (coverageIds.length !== topics.length || !uniqueCoverage || coverageIds.some(id => !topicIds.has(id))) {
        throw Error(`The ${edition.id} coverage is inconsistent.`);
      }
    }
    if (!editions.some(x => x.kind === 'daily') || !editions.some(x => x.kind === 'weekly')) throw Error('The daily or weekly sample is unavailable.');
    return { topics, deepDives, sources, editions };
  }

  return { validArray, readJson, readGeneratedEdition, loadEditionHistory, loadPack, validatePack };
}
