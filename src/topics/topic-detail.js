const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

function displayDate(value, fallback = '') {
  const raw = String(value || fallback || '').slice(0, 10);
  const date = new Date(`${raw}T12:00:00Z`);
  if (!raw || Number.isNaN(date.getTime())) return 'Date not listed';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function storyWhy(section, topic) {
  const explicit = section.why_it_matters || section.so_what || section.section_blocks?.why_it_matters;
  if (typeof explicit === 'string' && explicit.trim()) return `Why it matters: ${explicit.trim().replace(/^why(?: it matters| this matters)?\s*:\s*/i, '')}`;
  if (Array.isArray(explicit) && explicit.some(value => typeof value === 'string' && value.trim())) {
    return `Why it matters: ${explicit.filter(value => typeof value === 'string').join(' ').trim()}`;
  }
  const reason = String(topic.sourcePhrase || topic.label).trim();
  return `Why you’re seeing this: it matches “${reason}” in your interests.`;
}

function relatedSources(section, sources) {
  const isCurrent = source => source && source.withdrawn !== true && source.superseded !== true &&
    !['withdrawn', 'superseded'].includes(String(source.status || '').toLocaleLowerCase());
  const byId = new Map((sources || []).filter(isCurrent).map(source => [source.id, source]));
  const attached = Array.isArray(section.sources) ? section.sources : [];
  if (Array.isArray(section.source_ids) && section.source_ids.length) {
    const attachedById = new Map(attached.filter(source => isCurrent(source) && typeof source === 'object' && source.id)
      .map(source => [source.id, source]));
    return section.source_ids.map(id => byId.get(id) || attachedById.get(id)).filter(isCurrent);
  }
  if (attached.length) return attached.filter(isCurrent);
  return (section.source_ids || []).map(id => byId.get(id)).filter(isCurrent);
}

function sourceDate(sources, corrected = false) {
  if (corrected) {
    const original = sources[0]?.published_at || sources[0]?.published || sources[0]?.date || '';
    return original ? `Published ${displayDate(original)}` : 'Publication date not listed';
  }
  const published = sources.map(source => source.published_at || source.date).filter(Boolean).sort().at(-1) || '';
  const updated = sources.map(source => source.updated_at).filter(Boolean).sort().at(-1) || '';
  if (updated && (!published || updated > published)) return `Updated ${displayDate(updated)}`;
  return published ? `Published ${displayDate(published)}` : 'Date not listed';
}

function currentCorrection(item) {
  const edition = item.edition;
  const sectionId = item.section.id;
  return (edition?.corrections || []).find(correction =>
    correction?.severity === 'material' && correction.revision_id === edition.revision_id &&
    correction.previous_revision_id && correction.previous_revision_id !== edition.revision_id &&
    Array.isArray(correction.section_ids) && correction.section_ids.includes(sectionId) &&
    Number.isFinite(Date.parse(correction.corrected_at || ''))
  ) || null;
}

export function renderTopicDetail(container, { topic, items = [], sources = [], followed = false, paused = false,
  actions = {} } = {}) {
  container.replaceChildren();
  const header = el('header', 'topic-detail-header');
  header.append(el('p', 'topic-detail-kicker', 'Your topic'), el('h1', '', topic.label));
  if (topic.scope) header.append(el('p', 'topic-detail-scope', topic.scope));
  container.append(header);

  const controls = el('section', 'topic-detail-controls');
  controls.setAttribute('aria-label', `${topic.label} settings`);
  const ask = el('a', 'topic-detail-ask', 'Brief me on this');
  ask.append(el('span', 'topic-detail-ask-arrow', '→'));
  ask.querySelector('.topic-detail-ask-arrow').setAttribute('aria-hidden', 'true');
  ask.href = '#ask-episode'; ask.addEventListener('click', event => { event.preventDefault(); actions.askEpisode?.(); });
  controls.append(ask);
  const followRow = el('div', 'topic-detail-follow-row');
  followRow.append(el('span', 'topic-detail-follow-label', 'Daily updates'));
  const follow = el('button', 'topic-detail-follow');
  follow.type = 'button'; follow.setAttribute('role', 'switch');
  follow.setAttribute('aria-label', 'Daily updates');
  follow.setAttribute('aria-checked', String(followed && !paused));
  follow.addEventListener('click', () => followed
    ? actions.setPaused?.(!paused)
    : actions.follow?.());
  followRow.append(follow);
  controls.append(followRow);
  const edit = el('a', 'topic-detail-edit-exclusions', 'Edit topic preferences');
  edit.href = '#interests'; edit.addEventListener('click', event => { event.preventDefault(); actions.editInterests?.(); });
  controls.append(edit);
  container.append(controls);

  const section = el('section', 'topic-detail-stories');
  section.append(el('h2', '', 'Recent stories'));
  if (!items.length) {
    section.append(el('p', 'topic-detail-empty', `No recent stories on ${topic.label} yet. Your next briefing will include anything that matches.`));
  }
  const list = el('ol', 'topic-detail-list');
  for (const item of items.slice(0, 12)) {
    const card = el('li', 'topic-detail-story');
    const withdrawn = item.section.withdrawn === true || item.section.status === 'withdrawn';
    const correction = withdrawn ? null : currentCorrection(item);
    const heading = el('h3');
    if (withdrawn) heading.textContent = 'Story withdrawn';
    else {
      heading.append(el('a', '', item.section.heading));
      heading.querySelector('a').href = item.href || '#today';
      heading.querySelector('a').addEventListener('click', event => { event.preventDefault(); actions.openStory?.(item); });
    }
    const currentSources = relatedSources(item.section, sources);
    card.append(heading, el('p', 'topic-detail-date', sourceDate(currentSources, Boolean(correction))));
    if (correction) card.append(el('p', 'topic-detail-corrected', `Corrected ${displayDate(correction.corrected_at)}`));
    if (!withdrawn) {
      const whyText = storyWhy(item.section, topic);
      const why = el('p', 'topic-detail-why');
      const separator = whyText.indexOf(':');
      if (separator > 0) {
        why.append(el('strong', '', whyText.slice(0, separator + 1)));
        why.append(document.createTextNode(` ${whyText.slice(separator + 1).trim()}`));
      } else why.textContent = whyText;
      card.append(why);
    }
    const references = relatedSources(item.section, sources);
    const sourceList = el('ul', 'topic-detail-sources');
    for (const source of references.slice(0, 4)) {
      const row = el('li');
      const publisher = [source.publisher, source.outlet, source.name]
        .map(value => String(value || '').trim())
        .find(value => value && !/^https?:\/\//iu.test(value)) || 'Source';
      const title = String(source.title || '').replace(/\s+/gu, ' ').trim();
      const safeTitle = title && !/^https?:\/\//iu.test(title) ? title : '';
      const button = el('button', '', `${publisher} · ${safeTitle || 'Open source'}`);
      button.dataset.source = source.id;
      button.setAttribute('aria-label', safeTitle ? `Open source: ${publisher}, ${safeTitle}` : `Open source: ${publisher}`);
      button.type = 'button'; button.addEventListener('click', () => actions.openSource?.(source.id));
      row.append(button); sourceList.append(row);
    }
    if (references.length) card.append(sourceList);
    list.append(card);
  }
  if (items.length) section.append(list);
  container.append(section);
}
