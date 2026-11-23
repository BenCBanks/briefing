import { createPlayIcon } from '../ui-icons.js';

const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const byId = (items, id) => items.find(item => item.id === id);
const formatReviewDate = value => {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? String(value || 'Date not listed') : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
};

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

// Missing or explicitly pending content is never an explanation link.
export function hasExplanation(dive) {
  return Boolean(dive && dive.placeholder !== true && !/pending|placeholder|preparing|unavailable/i.test(dive.status || dive.state || '') &&
    Array.isArray(dive.sections) && dive.sections.some(section => {
      const text = typeof section.body === 'string' ? section.body.trim() : '';
      return text && !/^(?:an? explanation is being prepared[.!]?|coming soon[.!]?|pending[.!]?)$/i.test(text);
    }));
}

function topicDescription(topic, words) {
  // Scope is quoted from the request, never expanded with profile-wide geography.
  const original = String(words || '');
  const candidates = [topic.sourcePhrase, ...(topic.include || []), topic.label].filter(Boolean);
  for (const phrase of candidates) {
    const start = original.toLocaleLowerCase().indexOf(String(phrase).toLocaleLowerCase());
    if (start >= 0) return original.slice(start, start + phrase.length);
  }
  return original ? '' : topic.sourcePhrase || topic.label;
}

export function renderTopics(container, topics, deepDives, selectedIds, coverage, actions) {
  container.replaceChildren();
  const heading = element('div', 'topics-heading');
  heading.append(element('h1', '', 'Your topics'));
  container.append(heading);
  const form = element('form', 'topics-words-form');
  const label = element('label', 'topics-words-label', 'Tell us in your own words.');
  const words = element('textarea', 'topics-words');
  words.name = 'words'; words.rows = 3; words.maxLength = 2000; words.required = true;
  words.placeholder = 'Tell me about the subjects and places you follow, and what to leave out.';
  words.value = actions.words || ''; words.disabled = Boolean(actions.updating);
  words.addEventListener('input', () => {
    actions.draftWords?.(words.value);
    container.querySelectorAll('.topic-pause').forEach(toggle => { toggle.disabled = words.value !== (actions.savedWords || ''); });
  });
  label.append(words); form.append(label);
  const update = element('button', 'topics-update', actions.updating ? 'Updating… about a minute' : 'Update topics');
  update.type = 'submit'; update.disabled = Boolean(actions.updating); form.append(update);
  form.addEventListener('submit', event => { event.preventDefault(); if (words.value.trim()) actions.updateBriefing?.(words.value.trim()); });
  container.append(form);
  if (actions.updating) {
    const progress = element('div', 'topics-update-progress'); progress.setAttribute('aria-busy', 'true');
    const stage = element('p', 'topics-update-stage', actions.progress || 'Preparing your briefing…');
    stage.setAttribute('role', 'status'); stage.setAttribute('aria-live', 'polite'); stage.setAttribute('aria-atomic', 'true');
    const skeleton = element('div', 'edition-skeleton'); skeleton.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 3; i++) skeleton.append(element('span'));
    progress.append(stage, skeleton); container.append(progress);
    return;
  }
  if (actions.error) {
    const error = element('p', 'topics-update-error', actions.error); error.setAttribute('role', 'alert'); container.append(error);
    const retry = element('button', 'button-link topics-update-retry', 'Retry'); retry.type = 'button';
    retry.addEventListener('click', () => actions.retryUpdate?.(words.value.trim())); container.append(retry);
  }
  const selected = new Set(selectedIds);
  const muted = new Set((actions.muted || []).map(item => item.label.toLowerCase()));
  const coverageByTopic = new Map((coverage || []).map(item => [item.topic_id, item]));
  const list = element('ul', 'topic-list');
  list.setAttribute('aria-label', 'Topics covered');
  for (const topic of topics.filter(topic => selected.has(topic.id))) {
    const paused = muted.has(topic.label.toLowerCase());
    const row = element('li', 'topic-row');
    const top = element('div', 'topic-row-heading');
    top.append(element('h2', 'topic-label', topic.label));
    const toggle = button(paused ? 'Off' : 'On', 'topic-pause', () => actions.pauseTopic?.(topic.id, !paused));
    toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-checked', String(!paused));
    toggle.setAttribute('aria-label', `${topic.label} daily updates`); toggle.disabled = Boolean(actions.updating) || words.value !== (actions.savedWords || '');
    top.append(toggle); row.append(top);
    const description = topicDescription(topic, actions.savedWords || actions.words);
    if (description && description.toLocaleLowerCase().trim() !== topic.label.toLocaleLowerCase().trim()) row.append(element('p', 'topic-description', description));
    const recent = element('a', 'topic-recent-link', 'Recent stories');
    recent.href = `#topic-detail/${encodeURIComponent(topic.id)}`;
    row.append(recent);
    if (!paused && !actions.updating) {
      const info = coverageByTopic.get(topic.id);
      // A missing coverage row says nothing about the day's coverage. Only
      // surface a label when the edition explicitly recorded a status.
      if (info?.state) {
        const state = String(info.state).toLowerCase();
        const plenty = ['covered', 'update'].includes(state);
        if (plenty || state === 'limited') {
          const note = element('p', 'topic-coverage-note', plenty ? 'Plenty' : 'Limited coverage today');
          const reason = String(info.reason || info.note || '').trim();
          if (reason && !plenty) {
            const detail = element('details', 'topic-coverage-detail');
            const summary = element('summary', '', note.textContent);
            summary.className = note.className;
            detail.append(summary, element('p', '', reason)); row.append(detail);
          } else row.append(note);
        }
      }
    }
    const dive = byId(deepDives, topic.id) || deepDives.find(item => item.topic_id === topic.id);
    if (hasExplanation(dive)) {
      const learn = button('Learn more', 'topic-dive-link', () => actions.openDeepDive(topic.id));
      learn.dataset.topic = topic.id; learn.setAttribute('aria-label', 'Learn more about ' + topic.label); row.append(learn);
    }
    list.append(row);
  }
  container.append(list);
}

export function renderDeepDive(container, topic, deepDive, sources, actions) {
  container.replaceChildren();
  const topline = element('div', 'deep-dive-topline');
  topline.append(element('span', 'sample-label', 'Explanation'), element('span', 'sample-label', 'Sample · fictional'));
  container.append(topline);
  container.append(element('h1', '', deepDive.title));
  const listenItem = audioItem(topic, deepDive);
  const listenEstimate = actions.estimateListenMinutes?.(listenItem) || `about ${deepDive.duration_minutes}`;
  if (actions.canListen?.(listenItem)) {
    const listen = button('', 'deep-dive-listen', () => actions.listen?.(listenItem));
    listen.dataset.listenItem = deepDive.id; listen.dataset.listenTitle = deepDive.title; listen.dataset.listenEstimate = listenEstimate;
    listen.dataset.listenRevision = deepDive.revision_id;
    listen.setAttribute('aria-label', `Listen to ${deepDive.title}, ${listenEstimate} minutes`); listen.setAttribute('aria-pressed', 'false');
    listen.append(createPlayIcon(16));
    const copy = element('span', 'listen-control-copy', `Listen · ${listenEstimate} min`); listen.append(copy);
    const status = element('span', 'listen-control-status', ''); status.setAttribute('aria-live', 'polite'); listen.append(status);
    container.append(listen);
  } else {
    container.append(element('p', 'deep-dive-listening-unavailable', "Listening isn't available in this browser"));
  }
  container.append(element('p', 'deep-dive-meta', `${topic.label} · ${deepDive.duration_minutes} min read · Reviewed ${formatReviewDate(deepDive.reviewed_at)}`));
  const scope = element('aside', 'deep-dive-scope');
  scope.append(element('strong', '', 'Scope'), element('p', '', `${topic.scope.replace(/[.\s]+$/, '')}. This explanation provides background; examples are illustrative and do not report current events.`));
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
  const sourceSection = element('section', 'deep-dive-sources');
  sourceSection.append(element('h2', '', 'Citations'));
  sourceSection.append(element('p', 'source-context', 'References for this explanation.'));
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
  const save = button(actions.isSaved?.(deepDive.id) ? 'Saved explanation' : 'Save explanation', 'deep-dive-save', () => actions.save?.(deepDive.id, deepDive.revision_id));
  save.addEventListener('click', () => { save.textContent = actions.isSaved?.(deepDive.id) ? 'Saved explanation' : 'Save explanation'; });
  container.append(save);
}
