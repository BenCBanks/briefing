export function renderEarlierScreen({
  content, node, link, currentItems, visibleSections, editionDateRange, hasMaterialCorrection,
  getState, interestProfile, editionHistoryLoaded, pack, dailyBriefings, firstReadableSection,
  revisionDate, briefingDate, displayDate, getLibraryFilter, setLibraryFilter,
  getLibrarySearchQuery, setLibrarySearchQuery, loadEpisodeCards, canListen, listenEstimate,
  createPlayIcon, startListening, toggleSave, browserCanSpeak, navigate, loadEditionHistory,
}) {
  let libraryFilter = getLibraryFilter();
  let librarySearchQuery = getLibrarySearchQuery();
  content.append(node('h1', 'Earlier'));
  const editions = currentItems(pack.editions).filter(item => !(interestProfile && item.kind === 'weekly' && item.generated !== true));
  const storySectionsFor = item => visibleSections(item).filter(part => part.kind !== 'link');
  const storyCountFor = item => storySectionsFor(item).length;
  const readingMinutesFor = item => Math.max(1, Math.ceil(storySectionsFor(item)
    .reduce((count, part) => count + String(part.body || '').split(/\s+/).filter(Boolean).length, 0) / 225));
  const latestRecap = editions.filter(item => item.kind === 'weekly').sort((a, b) => revisionDate(b).localeCompare(revisionDate(a)))[0];
  let recapCard = null;
  if (latestRecap) {
    const recapHash = `#edition/weekly/${encodeURIComponent(latestRecap.id)}`;
    recapCard = link('', recapHash, 'earlier-weekly-card');
    const range = editionDateRange(latestRecap).replace('–', '-');
    recapCard.append(node('strong', 'Weekly recap'));
    recapCard.append(node('span', `${range} · ${storyCountFor(latestRecap)} stories · ${readingMinutesFor(latestRecap)} min read`, 'library-date-meta'));
    recapCard.append(node('span', 'Open recap →', 'earlier-weekly-card-action'));
    if (hasMaterialCorrection(latestRecap)) recapCard.append(node('span', 'Corrected', 'library-status-tag'));
    recapCard.addEventListener('click', event => {
      event.preventDefault();
      navigate(recapHash, { root: 'library', parentHash: '#library', parentScroll: window.scrollY, parentFocus: { type: 'href', id: recapHash } });
    });
  }
  const daily = dailyBriefings();

  const historyStatus = interestProfile && !editionHistoryLoaded ? node('p', 'Loading earlier briefings…', 'library-history-status') : null;
  if (historyStatus) historyStatus.setAttribute('role', 'status');
  const briefingSection = node('section', null, 'library-briefings');
  briefingSection.append(node('h2', 'Past editions'));
  const briefingList = node('ul', null, 'library-briefing-list');
  for (const edition of daily) {
    const first = firstReadableSection(edition);
    if (!first) continue;
    const hash = `#edition/daily/${encodeURIComponent(edition.id)}/${encodeURIComponent(first.id)}`;
    const row = node('li');
    const itemLink = link(briefingDate(edition.local_date), hash, 'library-briefing-link');
    itemLink.addEventListener('click', event => {
      event.preventDefault();
      navigate(hash, { root: 'library', parentHash: '#library', parentScroll: window.scrollY,
        parentFocus: { type: 'href', id: hash }, sectionId: first.id, direct: false });
    });
    row.append(itemLink, node('p', `${storyCountFor(edition)} stories · ${readingMinutesFor(edition)} min read`, 'library-date-meta'));
    if (hasMaterialCorrection(edition)) {
      const corrected = link('Corrected', '#corrections', 'library-status-tag library-updated-link');
      corrected.setAttribute('aria-label', `Corrected · view ${briefingDate(edition.local_date)} corrections`);
      row.append(corrected);
    }
    briefingList.append(row);
  }
  if (!briefingList.children.length) briefingList.append(node('li', 'Nothing here yet. Editions appear after the first morning.', 'library-empty-briefings'));
  briefingSection.append(briefingList);
  const filters = node('div', null, 'library-filters');
  const editionsTab = node('button', 'Editions'); editionsTab.type = 'button'; editionsTab.setAttribute('aria-pressed', String(libraryFilter === 'editions'));
  const savedTab = node('button', 'Saved'); savedTab.type = 'button'; savedTab.setAttribute('aria-pressed', String(libraryFilter === 'saved'));
  const episodesTab = node('button', 'Episodes'); episodesTab.type = 'button';
  filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', 'Earlier');
  filters.append(editionsTab, episodesTab, savedTab); content.querySelector('h1').after(filters);
  const search = node('input', null, 'library-search'); search.type = 'search'; search.value = librarySearchQuery;
  search.setAttribute('aria-label', 'Search earlier briefings, saved items, and topics');
  search.placeholder = 'Search earlier, saved, and topics';
  filters.before(search);
  const searchStatus = node('p', '', 'library-search-status'); searchStatus.setAttribute('role', 'status'); searchStatus.setAttribute('aria-live', 'polite'); searchStatus.hidden = true;
  filters.after(searchStatus);
  const section = node('section', null, 'library-results'); content.append(section);
  const deepDives = currentItems(pack.deepDives);
  const sectionLookup = new Map(editions.flatMap(edition => edition.sections.map(section => [section.id, { section, edition }])));
  const addRow = (title, type, detail, hash, labels = [], listenItem = null, progress = '', estimateItem = listenItem, savedId = null) => {
    const li = node('li', null, 'library-row');
    li.append(node('span', type, 'library-kind-label'));
    const mainRow = node('div', null, 'library-row-main');
    const itemLink = link('', hash, 'library-item-link');
    itemLink.append(node('span', title, 'library-item-title'));
    itemLink.addEventListener('click', event => {
      event.preventDefault();
      const parent = { root: 'library', parentHash: '#library', parentScroll: window.scrollY,
        parentFocus: { type: 'href', id: hash }, direct: false };
      navigate(hash, parent);
    });
    mainRow.append(itemLink);
    if (listenItem && canListen(listenItem)) {
      const estimate = listenEstimate(estimateItem || listenItem);
      const listenGroup = node('div', null, 'library-listen-group');
      const listen = node('button', null, 'library-listen-button'); listen.type = 'button';
      listen.dataset.listenItem = listenItem.id; listen.dataset.listenRevision = listenItem.revision_id;
      listen.dataset.listenTitle = title; listen.dataset.listenEstimate = estimate;
      listen.setAttribute('aria-label', `Listen to ${title}, ${estimate} minutes`); listen.setAttribute('aria-pressed', 'false');
      listen.addEventListener('click', () => startListening(listenItem));
      listen.append(createPlayIcon(14));
      const status = node('span', '', 'library-listen-status'); status.setAttribute('aria-live', 'polite');
      listenGroup.append(listen, status); mainRow.append(listenGroup);
    }
    li.append(mainRow);
    const meta = node('div', null, 'library-row-meta');
    meta.append(node('span', detail, 'library-row-detail'));
    for (const label of labels) {
      if (label === 'Updated') {
        const badge = link('Updated', '#corrections', 'library-status-tag library-updated-link');
        badge.setAttribute('aria-label', 'Updated · view corrections'); meta.append(badge);
      } else meta.append(node('span', label, 'library-status-tag'));
    }
    if (progress) meta.append(node('span', progress, 'library-row-progress'));
    if (savedId && getState().savedIds.includes(savedId)) {
      const remove = node('button', 'Remove', 'library-remove-saved'); remove.type = 'button';
      remove.setAttribute('aria-label', `Remove ${title} from saved items`);
      remove.addEventListener('click', () => { toggleSave(savedId, null, false); renderRows(); });
      meta.append(remove);
    }
    li.append(meta); section.querySelector('.library-list').append(li);
  };
  const renderSearchResults = list => {
    const query = librarySearchQuery.trim().toLocaleLowerCase();
    const matches = value => String(value || '').toLocaleLowerCase().includes(query);
    const savedOnly = libraryFilter === 'saved';
    let count = 0;
    const searchEditions = [...new Map([...editions, ...daily].map(item => [item.id, item])).values()];
    for (const edition of searchEditions) {
      const date = edition.kind === 'weekly' ? editionDateRange(edition) : displayDate(edition.local_date);
      const editionSaved = getState().savedIds.includes(edition.id);
      if (matches(`${edition.title || ''} ${date} ${edition.kind}`) && (!savedOnly || editionSaved)) {
        const hash = `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`;
        addRow(edition.title || `${edition.kind === 'weekly' ? 'Weekly recap' : 'Daily briefing'} · ${date}`,
          edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `${date} · ${readingMinutesFor(edition)} min read`, hash,
          [], null, '', null, editionSaved ? edition.id : null);
        count++;
      }
      for (const part of visibleSections(edition)) {
        const saved = getState().savedIds.includes(part.id);
        if (!matches(`${part.heading || ''} ${part.headline || ''} ${part.body || ''} ${date} ${edition.title || ''}`) || (savedOnly && !saved)) continue;
        const hash = `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}/${encodeURIComponent(part.id)}`;
        const readMinutes = Math.max(1, Math.ceil(String(part.body || '').split(/\s+/).filter(Boolean).length / 225));
        addRow(part.headline || part.heading, saved ? 'Saved story' : 'Story', `${date} · ${readMinutes} min read`, hash,
          [], null, '', null, saved ? part.id : null);
        count++;
      }
    }
    for (const dive of deepDives) {
      const saved = getState().savedIds.includes(dive.id);
      const topic = pack.topics.find(item => item.id === dive.topic_id);
      if (!matches(`${dive.title} ${dive.summary || ''} ${topic?.label || ''}`) || (savedOnly && !saved)) continue;
      addRow(dive.title, `${topic?.label || 'Topic'} explanation`, `${readingMinutesFor(dive)} min read · Reviewed ${displayDate(dive.reviewed_at)}`,
        `#explanation/${encodeURIComponent(dive.id)}`, [], null, '', null, saved ? dive.id : null);
      count++;
    }
    for (const topic of pack.topics) {
      if (!matches(`${topic.label} ${topic.scope || ''} ${topic.description || ''}`)) continue;
      addRow(topic.label, 'Topic', topic.scope || topic.description || 'Follow this topic', `#topic-detail/${encodeURIComponent(topic.id)}`);
      count++;
    }
    searchStatus.textContent = count ? `${count} ${count === 1 ? 'result' : 'results'}` : `No matches for “${librarySearchQuery.trim()}”.`;
    searchStatus.hidden = false;
    if (!count) {
      const empty = node('li', null, 'library-empty-search');
      empty.append(node('h2', 'No matching briefings'));
      empty.append(node('p', 'Try another search, or clear it to browse Earlier.'));
      const clear = node('button', 'Clear search', 'library-empty-search-clear'); clear.type = 'button';
      clear.addEventListener('click', () => { search.value = ''; librarySearchQuery = ''; setLibrarySearchQuery(''); renderRows(); search.focus(); });
      empty.append(clear); list.append(empty);
    }
  };
  const renderRows = () => {
    const searching = Boolean(librarySearchQuery.trim());
    section.classList.toggle('is-searching', searching);
    searchStatus.hidden = !searching;
    briefingSection.hidden = libraryFilter !== 'editions' || searching;
    section.replaceChildren();
    if (searching) {
      const list = node('ul', null, 'library-list'); section.append(list);
      renderSearchResults(list);
      if (!browserCanSpeak()) section.append(node('p', "Listening isn't available in this browser", 'library-device-note'));
      return;
    }
    if (libraryFilter === 'editions') {
      if (historyStatus) section.append(historyStatus);
      if (recapCard) section.append(recapCard);
      section.append(briefingSection);
    }
    const list = node('ul', null, 'library-list'); section.append(list);
    if (libraryFilter === 'episodes') {
      const cards = node('div', null, 'earlier-episodes'); section.append(cards);
      void loadEpisodeCards(cards, null, null, { earlier: true });
    } else if (libraryFilter === 'editions') {
      const weeklyEditions = editions.filter(item => item.kind === 'weekly' && item.id !== latestRecap?.id).sort((a, b) => revisionDate(b).localeCompare(revisionDate(a)));

      for (const edition of weeklyEditions) {
        const date = edition.kind === 'weekly' ? editionDateRange(edition) : displayDate(edition.local_date);
        const corrected = hasMaterialCorrection(edition);
        const times = `${readingMinutesFor(edition)} min read · ${listenEstimate(edition)} min listen`;
        addRow(edition.title, edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `${date} · ${times}`, `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`, corrected ? ['Corrected'] : [], edition, progressFor(edition), edition, getState().savedIds.includes(edition.id) ? edition.id : null);
      }
    } else {
      const savedEditions = editions.filter(item => getState().savedIds.includes(item.id));
      const savedDives = deepDives.filter(item => getState().savedIds.includes(item.id));
      const savedSections = getState().savedIds.filter(id => sectionLookup.has(id));
      if (!savedEditions.length && !savedDives.length && !savedSections.length) {
        const empty = node('div', null, 'library-empty');
        const browse = node('button', 'Browse editions', 'library-empty-action'); browse.type = 'button';
        browse.addEventListener('click', () => { libraryFilter = 'editions'; setLibraryFilter(libraryFilter); sessionStorage.setItem('briefing:library-filter', libraryFilter); editionsTab.click(); });
        empty.append(node('h2', 'Nothing saved yet'), node('p', 'Save a story or briefing to find it here.'), browse);
        section.append(empty);
      }
      for (const edition of savedEditions) {
        const date = edition.kind === 'weekly' ? editionDateRange(edition) : displayDate(edition.local_date);
        const corrected = hasMaterialCorrection(edition);
        const savedRevision = getState().savedRevisions?.[edition.id];
        const updated = savedRevision && savedRevision !== edition.revision_id;
        const tags = [...(corrected ? ['Corrected'] : []), ...(updated ? ['Updated'] : [])];
        const times = `${readingMinutesFor(edition)} min read · ${listenEstimate(edition)} min listen`;
        addRow(edition.title, edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `${date} · ${times}`, `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`, tags, edition, progressFor(edition), edition, edition.id);
      }
      for (const dive of savedDives) {
        const topic = pack.topics.find(item => item.id === dive.topic_id);
        addRow(dive.title, `${topic?.label || 'Topic'} explanation`, `${readingMinutesFor(dive)} min read · ${listenEstimate(dive)} min listen · Reviewed ${displayDate(dive.reviewed_at)}`, `#explanation/${encodeURIComponent(dive.id)}`, [], dive, progressFor(dive), dive, dive.id);
      }
      for (const id of savedSections) {
        const match = sectionLookup.get(id);
        const segment = { ...match.edition, sections: [match.section], start_section_id: match.section.id };
        const playable = { ...match.edition, start_section_id: match.section.id };
        const readMinutes = Math.max(1, Math.ceil(match.section.body.split(/\s+/).filter(Boolean).length / 225));
        addRow(match.section.heading, 'Saved section', `${readMinutes} min read · ${listenEstimate(segment)} min listen`, `#edition/${encodeURIComponent(match.edition.kind)}/${encodeURIComponent(match.edition.id)}/${encodeURIComponent(id)}`, [], playable, progressFor(match.edition), segment, id);
      }
    }
    if (libraryFilter === 'saved') section.append(node('p', 'Saved items stay on this device.', 'library-device-note'));
    if (!browserCanSpeak()) section.append(node('p', "Listening isn't available in this browser", 'library-device-note'));
  };
  const progressFor = item => {
    const listening = getState().progress?.[item.id];
    if (listening?.revisionId === item.revision_id && Number(listening.seconds) > 0) {
      const seconds = Math.floor(Number(listening.seconds));
      return `Listened ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }
    const place = getState().readingProgress?.[item.id];
    if (place?.revisionId === item.revision_id) {
      const index = visibleSections(item).findIndex(part => part.id === (place.storyId || place.sectionId));
      if (index >= 0) return `Read to story ${index + 1}`;
    }
    return getState().finishedIds.includes(`${item.id}:${item.revision_id}`) ? 'Read' : '';
  };
  for (const [button, value] of [[editionsTab, 'editions'], [episodesTab, 'episodes'], [savedTab, 'saved']]) {
    button.setAttribute('aria-pressed', String(libraryFilter === value));
    button.addEventListener('click', () => { libraryFilter = value; setLibraryFilter(value); sessionStorage.setItem('briefing:library-filter', value); filters.querySelectorAll('button').forEach(choice => choice.setAttribute('aria-pressed', String(choice === button))); renderRows(); });
  }
  search.addEventListener('input', () => { librarySearchQuery = search.value; setLibrarySearchQuery(librarySearchQuery); renderRows(); });
  search.addEventListener('keydown', event => {
    if (event.key === 'Escape' && librarySearchQuery.trim()) {
      event.preventDefault(); search.value = ''; librarySearchQuery = ''; setLibrarySearchQuery(''); renderRows();
    }
  });
  renderRows();
  if (historyStatus) void loadEditionHistory(historyStatus);
}

