const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const byId = (items, id) => items.find(item => item.id === id);

function button(label, className, onClick) {
  const node = element('button', className, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

function safeSourceLink(source) {
  const link = element('a', '', source.title);
  try {
    const url = new URL(source.url);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new Error('Unsupported URL');
    link.href = url.href;
  } catch {
    link.removeAttribute('href');
    link.setAttribute('aria-disabled', 'true');
  }
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function audioItem(topic, deepDive) {
  return {
    id: deepDive.id,
    title: deepDive.title,
    revision_id: deepDive.revision_id,
    script_hash: deepDive.script_hash,
    audio: deepDive.audio,
    kind: 'deep-dive',
    sections: deepDive.sections,
    duration_minutes: deepDive.duration_minutes,
    topic_id: topic.id,
  };
}

export function renderTopics(container, topics, deepDives, selectedIds, coverage, actions) {
  container.replaceChildren();
  const selected = new Set(selectedIds);
  const heading = element('div', 'topics-heading');
  heading.append(element('h1', '', 'Topics'));
  heading.append(element('p', 'topics-intro', 'Clear explanations behind the subjects you follow.'));
  container.append(heading);

  const bar = element('div', 'topics-toolbar');
  bar.append(element('span', 'muted', 'Your topic bookshelf'));
  bar.append(button('Manage topics', 'topics-edit', () => actions.editTopics()));
  container.append(bar);

  const list = element('ol', 'topic-list');
  const visibleTopics = topics.filter(topic => selected.has(topic.id));
  for (const topic of visibleTopics) {
    const dive = byId(deepDives, topic.id) || deepDives.find(item => item.topic_id === topic.id);
    const row = element('li', 'topic-row');
    row.append(element('span', 'topic-label', topic.label));
    if (dive) {
      const link = button('', 'topic-dive-link', () => actions.openDeepDive(topic.id));
      link.dataset.topic = topic.id;
      link.append(element('strong', '', dive.title), element('span', 'topic-duration', `About ${dive.duration_minutes} min read`), element('span', 'topic-arrow', '→'));
      link.setAttribute('aria-label', `Read explanation: ${dive.title}`);
      row.append(link);
    } else {
      row.append(element('p', 'muted', 'An explanation is being prepared.'));
    }
    list.append(row);
  }
  if (!visibleTopics.length) {
    const empty = element('li', 'topic-empty');
    empty.append(element('h2', '', 'Choose a few subjects to begin'));
    empty.append(element('p', '', 'Your daily briefing will follow the topics you select. You can adjust them at any time.'));
    empty.append(button('Choose topics', 'primary', () => actions.editTopics()));
    list.append(empty);
  }
  container.append(list);
  container.append(element('p', 'local-note topics-note', 'These are evergreen explanations, not reports of current events.'));
}

export function renderDeepDive(container, topic, deepDive, sources, actions) {
  container.replaceChildren();
  container.append(element('p', 'eyebrow deep-dive-eyebrow', `${topic.label} · Explanation`));
  container.append(element('h1', '', deepDive.title));
  container.append(element('p', 'deep-dive-meta', `About ${deepDive.duration_minutes} min read`));
  const playRow = element('div', 'deep-dive-actions');
  if (deepDive.audio?.state === 'recorded' && deepDive.audio.url) {
    const audioButton = button('Play explanation', 'primary', () => actions.play(audioItem(topic, deepDive)));
    audioButton.dataset.audioAction = deepDive.id; audioButton.dataset.audioDefault = 'Play explanation';
    playRow.append(audioButton);
  }
  container.append(playRow);
  container.append(element('p', 'deep-dive-summary', deepDive.summary));
  const scope = element('aside', 'deep-dive-scope');
  scope.append(element('p', '', `${topic.scope} This is an evergreen explanation for background; examples are illustrative and do not report current events.`));
  container.append(scope);

  const sections = element('div', 'deep-dive-sections');
  for (const section of deepDive.sections) {
    const article = element('section', 'deep-dive-section');
    article.append(element('h2', '', section.heading));
    String(section.body || '').split(/\n\s*\n/).forEach(paragraph => article.append(element('p', '', paragraph)));
    sections.append(article);
  }
  container.append(sections);

  const used = new Set(deepDive.sections.flatMap(section => section.source_ids));
  const references = sources.filter(source => used.has(source.id));
  const sourceSection = element('details', 'deep-dive-sources');
  sourceSection.append(element('summary', '', `Sources (${references.length})`));
  sourceSection.append(element('p', 'source-context', 'These references support the general concepts described here; they do not verify fictional examples.'));
  const sourceList = element('ol', 'source-list');
  for (const source of references) {
    const row = element('li', 'source-item');
    const open = button(`${source.title} →`, 'source-internal-link', () => actions.openSource?.(source.id));
    open.dataset.source = source.id;
    open.append(element('small', '', source.publisher || 'Reference'));
    row.append(open);
    sourceList.append(row);
  }
  sourceSection.append(sourceList);
  container.append(sourceSection);
  container.append(element('p', 'deep-dive-reviewed', `Reviewed ${deepDive.reviewed_at}`));
  const save = button(actions.isSaved?.(deepDive.id) ? 'Saved explanation' : 'Save explanation', 'deep-dive-save', () => actions.save?.(deepDive.id, deepDive.revision_id));
  save.addEventListener('click', () => { save.textContent = actions.isSaved?.(deepDive.id) ? 'Saved explanation' : 'Save explanation'; });
  container.append(save);
}
