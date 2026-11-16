import { renderEdition } from './edition/edition.js';
import { renderTopics, renderDeepDive } from './topics/topics.js';
import { loadState, saveState, resetState } from './storage.js';
import { createPlayer } from './player.js';

const content = document.querySelector('#content');
const nav = [...document.querySelectorAll('[data-nav]')];
let pack = null;
let state = loadState();
let returnTo = { hash: '#today', sectionId: null, scrollY: 0 };
let deepDiveReturnTo = null;
let pendingScrollRestore = null;
let pendingSourceFocusID = null;
let pendingTranscriptPlacement = null;
let transcriptReturnTo = null;
let transcriptReturnHash = null;
let pendingPlayerReturnFocus = false;
let pendingReturnHeadingFocus = false;
let readingObserver = null;

const player = createPlayer(document.querySelector('#player-root'), (id, seconds, revisionId) => {
  state.progress[id] = { seconds, revisionId };
  state = saveState(state);
}, id => state.progress[id], () => {
  const item = player.item;
  const selection = item ? { id: item.id, revisionId: item.revision_id } : null;
  if (state.activeAudio?.id !== selection?.id || state.activeAudio?.revisionId !== selection?.revisionId) {
    state.activeAudio = selection;
    state = saveState(state);
  }
  document.querySelectorAll('[data-audio-action]').forEach(button => {
    const active = button.dataset.audioAction === player.item?.id;
    button.textContent = active ? player.label : button.dataset.audioDefault;
  });
  document.body.classList.toggle('has-player', Boolean(player.item));
}, openTranscript, transcriptLabel);

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function link(text, href, className) {
  const el = node('a', text, className);
  el.href = href;
  return el;
}

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

async function readJson(name) {
  const response = await fetch(`/content/${name}.json`, { cache: 'no-store' });
  if (!response.ok) throw Error(`${name} could not be loaded (${response.status}).`);
  return response.json();
}

async function loadPack() {
  const [topicFile, diveFile, sourceFile, editionFile] = await Promise.all([
    readJson('topics'), readJson('deep-dives'), readJson('sources'), readJson('editions'),
  ]);
  const topics = validArray(topicFile.topics, 'topics');
  const deepDives = validArray(diveFile.deep_dives, 'deep dives', true);
  const sources = validArray(sourceFile.sources, 'sources');
  const editions = validArray(editionFile.editions, 'editions', true);
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
      if (!section.id || !section.heading || !section.body || !Array.isArray(section.source_ids)) throw Error(`The ${item.id} section is incomplete.`);
      if (section.source_ids.some(id => !sourceIds.has(id))) throw Error(`The ${item.id} section refers to an unavailable source.`);
    }
  }
  for (const edition of editions) {
    if (!Array.isArray(edition.coverage) || edition.coverage.length !== topics.length) throw Error(`The ${edition.id} coverage is incomplete.`);
    const coverageIds = edition.coverage.map(row => row.topic_id);
    if (new Set(coverageIds).size !== topics.length || coverageIds.some(id => !topicIds.has(id))) throw Error(`The ${edition.id} coverage is inconsistent.`);
  }
  if (!editions.some(x => x.kind === 'daily') || !editions.some(x => x.kind === 'weekly')) throw Error('The daily or weekly sample is unavailable.');
  return { topics, deepDives, sources, editions };
}

function personalized(edition) {
  return edition;
}

function remember(sectionId = null) { returnTo = { hash: location.hash || '#today', sectionId, scrollY: window.scrollY, sourceID: document.activeElement?.dataset?.source || null }; }
function navigate(hash) { if (location.hash === hash) route(); else location.hash = hash; }
function navigateTranscript(hash, placement) {
  pendingTranscriptPlacement = placement;
  if (location.hash === hash) route();
  else {
    history.pushState({ ...(history.state || {}), transcriptOrigin: { ...transcriptReturnTo }, transcriptHash: hash }, '', hash);
    route();
  }
}
function openSource(id) { remember(); navigate(`#source/${encodeURIComponent(id)}`); }
function openDeepDive(id, sectionId = null) {
  remember(sectionId);
  deepDiveReturnTo = returnTo;
  navigate(`#topic/${encodeURIComponent(id)}`);
}
function backTo(target) {
  pendingScrollRestore = target.scrollY;
  pendingSourceFocusID = target.sourceID || null;
  pendingReturnHeadingFocus = true;
  navigate(target.hash);
}
function back() {
  if (transcriptReturnTo && transcriptReturnHash && location.hash.startsWith('#source/') && returnTo.hash === transcriptReturnHash) {
    pendingScrollRestore = returnTo.scrollY;
    pendingSourceFocusID = returnTo.sourceID || null;
    history.back();
    return;
  }
  backTo(returnTo);
}
function toggleSave(id, revisionId = null, rerender = true) {
  if (state.savedIds.includes(id)) {
    state.savedIds = state.savedIds.filter(x => x !== id);
    delete state.savedRevisions[id];
  } else {
    state.savedIds = [...state.savedIds, id];
    if (revisionId) state.savedRevisions[id] = revisionId;
  }
  state = saveState(state);
  if (rerender) route();
}
function setFeedback(id, category) {
  state.feedback[id] = category; state = saveState(state); route();
}
function revisionNumber(item) {
  const matches = [...String(item.revision_id || '').matchAll(/(?:^|[-._])r(\d+)/gi)];
  return matches.length ? Number(matches[matches.length - 1][1]) : 0;
}

function revisionDate(item) {
  return String(item.published_at || item.reviewed_at || item.local_date || item.week_start || item.created_at || '');
}

function revisionGroup(items, id, predicate = () => true) {
  const candidates = items.filter(item => item.id === id && predicate(item));
  if (!candidates.length) return { candidates: [], current: [] };
  const superseded = new Set(candidates.flatMap(item => (item.corrections || []).map(correction => correction.previous_revision_id).filter(Boolean)));
  const current = candidates.filter(item => !superseded.has(item.revision_id) &&
    !['superseded', 'withdrawn'].includes(item.status) && item.superseded !== true && item.withdrawn !== true &&
    !item.superseded_by && !item.withdrawn_at);
  return { candidates, current };
}

function compareRevision(a, b) {
  return revisionDate(a).localeCompare(revisionDate(b)) || revisionNumber(a) - revisionNumber(b) || String(a.revision_id).localeCompare(String(b.revision_id));
}

function currentRevision(items, id, predicate = () => true) {
  const group = revisionGroup(items, id, predicate);
  return group.current.length === 1 ? group.current[0] : null;
}

function currentItems(items, predicate = () => true) {
  const groups = new Map();
  for (const item of items.filter(predicate)) {
    const type = item.kind || (item.topic_id ? 'deep-dive' : 'item');
    groups.set(`${type}\u0000${item.id}`, { id: item.id, type });
  }
  return [...groups.values()].map(({ id, type }) => currentRevision(items, id, item =>
    predicate(item) && (item.kind || (item.topic_id ? 'deep-dive' : 'item')) === type
  )).filter(Boolean);
}

function currentEdition(kind) {
  const ids = [...new Set(pack.editions.filter(item => item.kind === kind).map(item => item.id))];
  const groups = ids.map(id => {
    const group = revisionGroup(pack.editions, id, item => item.kind === kind);
    return { ...group, latest: group.current.sort(compareRevision).at(-1) };
  }).filter(group => group.latest);
  const latest = groups.sort((a, b) => compareRevision(a.latest, b.latest)).at(-1);
  return latest && latest.current.length === 1 ? personalized(latest.current[0]) : null;
}

function transcriptDestination(id, kind) {
  if (!transcriptReturnTo || transcriptReturnHash !== (location.hash || '#today')) return false;
  if (kind === 'deep-dive') return location.hash === `#explanation/${encodeURIComponent(id)}`;
  return location.hash === `#edition/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
}

function readerAlreadyShows(item) {
  const hash = location.hash || '#today';
  if (item.kind === 'deep-dive') {
    return hash === `#explanation/${encodeURIComponent(item.id)}` || hash === `#topic/${encodeURIComponent(item.topic_id)}`;
  }
  if (hash === `#edition/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.id)}`) return true;
  return (item.kind === 'daily' && hash === '#today') || (item.kind === 'weekly' && hash === '#weekly');
}

function trackReadingPlace(id, revisionId, selector) {
  readingObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      state.readingProgress[id] = { sectionId: entry.target.id, revisionId };
      state = saveState(state);
    }
  }, { rootMargin: '0px 0px -55% 0px', threshold: 0 });
  content.querySelectorAll(selector).forEach(section => readingObserver.observe(section));
}

function currentTranscriptItem(selected) {
  if (!selected || !pack) return { available: false };
  const pool = selected.kind === 'deep-dive' ? pack.deepDives : pack.editions;
  const entry = currentRevision(pool, selected.id, item => selected.kind === 'deep-dive' || item.kind === selected.kind);
  if (!entry) return { available: false };
  const current = selected.kind === 'deep-dive' ? { ...entry, kind: 'deep-dive' } : entry;
  const selectedAudioMismatch = selected.audio?.state === 'recorded' &&
    (selected.audio.revision_id !== selected.revision_id || selected.audio.script_hash !== selected.script_hash);
  const corrected = current.revision_id !== selected.revision_id || current.script_hash !== selected.script_hash;
  const correction = current.corrections?.find(item => item.previous_revision_id === selected.revision_id &&
    (!item.revision_id || item.revision_id === current.revision_id)) || null;
  return { available: true, current, corrected, selectedAudioMismatch, correction };
}

function transcriptLabel(selected) {
  const result = currentTranscriptItem(selected);
  if (!result.available) return 'Read transcript';
  if (result.selectedAudioMismatch && !result.correction) return result.current.kind === 'deep-dive' ? 'Read current explanation' : 'Read current edition';
  if (!result.corrected) return 'Read transcript';
  if (!result.correction) return result.current.kind === 'deep-dive' ? 'Read current explanation' : 'Read current edition';
  return result.current.kind === 'deep-dive' ? 'Read corrected explanation' : 'Read corrected edition';
}

function openTranscript(selected) {
  const resolved = currentTranscriptItem(selected);
  if (!resolved.available) return { available: false };
  const { current, corrected, selectedAudioMismatch } = resolved;
  const hash = current.kind === 'deep-dive'
    ? `#explanation/${encodeURIComponent(current.id)}`
    : `#edition/${encodeURIComponent(current.kind)}/${encodeURIComponent(current.id)}`;
  if (readerAlreadyShows(current)) return { available: true, correctedItem: corrected || selectedAudioMismatch ? current : null };
  transcriptReturnTo = { hash: location.hash || '#today', sectionId: null, scrollY: window.scrollY };
  transcriptReturnHash = hash;
  const saved = state.readingProgress[current.id];
  const validAnchor = !corrected && saved?.revisionId === current.revision_id && current.sections.some(section => section.id === saved.sectionId);
  const placement = { id: current.id, sectionId: validAnchor ? saved.sectionId : null, correction: corrected };
  return {
    available: true,
    correctedItem: corrected || selectedAudioMismatch ? current : null,
    navigate: () => navigateTranscript(hash, placement),
  };
}

function renderEditionPage(kind, exactEdition = null) {
  const edition = exactEdition || currentEdition(kind);
  if (!edition) {
    content.append(node('h1', `${kind === 'weekly' ? 'The weekly recap' : 'Today’s briefing'} is unavailable`));
    content.append(node('p', 'There is no unambiguous current version available right now.'));
    return;
  }
  if (transcriptDestination(edition.id, edition.kind)) {
    const returnButton = node('button', transcriptReturnLabel(), 'back-link transcript-return');
    returnButton.type = 'button'; returnButton.addEventListener('click', () => returnFromTranscript());
    content.append(returnButton);
  }
  const body = node('div'); content.append(body);
  renderEdition(body, edition, pack.topics, {
    sources: pack.sources,
    play: item => player.play(item),
    openTopic: openDeepDive,
    hasDeepDive: id => pack.deepDives.some(item => item.topic_id === id),
    openSource,
    correctionStopped: sessionStorage.getItem('briefing:correction-audio-notice'),
    save: (id, revisionId) => toggleSave(id, revisionId, false),
    finish: (id, revision) => { state.finishedIds = [...new Set([...state.finishedIds, `${id}:${revision}`])]; state = saveState(state); },
    isFinished: (id, revision) => state.finishedIds.includes(`${id}:${revision}`),
    feedback: setFeedback,
    isSaved: id => state.savedIds.includes(id),
    getFeedback: id => state.feedback[id],
    readingPlace: () => {
      const place = state.readingProgress[edition.id];
      return place?.revisionId === edition.revision_id ? place.sectionId : null;
    },
    continueReading: sectionId => {
      document.getElementById(sectionId)?.scrollIntoView({ block: 'start' });
      document.getElementById(`heading-${sectionId}`)?.focus({ preventScroll: true });
    },
  });
  sessionStorage.removeItem('briefing:correction-audio-notice');
  if (!pendingTranscriptPlacement) trackReadingPlace(edition.id, edition.revision_id, '.edition-section');
  const footer = node('div', null, 'button-row');
  footer.append(link(kind === 'daily' ? 'Read the weekly recap →' : 'Return to today →', kind === 'daily' ? '#weekly' : '#today', 'button-link'));
  footer.append(link('Adjust topics', '#preferences', 'button-link'));
  content.append(footer);
}

function renderTopicsPage() {
  const body = node('div'); content.append(body);
  const daily = currentEdition('daily');
  if (!daily) {
    content.append(node('h1', 'Today’s briefing is unavailable'), node('p', 'There is no unambiguous current daily edition available right now.'));
    return;
  }
  const deepDives = currentItems(pack.deepDives);
  renderTopics(body, pack.topics, deepDives, state.selectedIds, daily.coverage, {
    openDeepDive, editTopics: () => navigate('#preferences'), play: item => player.play(item),
  });
}

function renderDeepDivePage(id, exactDive = null) {
  const dive = exactDive || currentItems(pack.deepDives, item => item.topic_id === id)[0];
  const topic = pack.topics.find(item => item.id === (dive?.topic_id || id));
  if (!topic || !dive) return renderMissing();
  const body = node('div'); content.append(body);
  const isTranscriptDestination = transcriptDestination(dive.id, 'deep-dive');
  const origin = isTranscriptDestination ? transcriptReturnTo : (deepDiveReturnTo || returnTo);
  const fromToday = origin.hash === '#today';
  const backLabel = fromToday ? '← Back to today’s briefing' : origin.hash === '#library' ? '← Back to Library' : '← Back to Topics';
  renderDeepDive(body, topic, dive, pack.sources, {
    back: () => isTranscriptDestination ? returnFromTranscript() : backTo(origin),
    backLabel,
    play: item => player.play(item),
    openSource,
  });
  const audioMatches = dive.audio?.state === 'recorded' && dive.audio.url &&
    dive.audio.revision_id === dive.revision_id && dive.audio.script_hash === dive.script_hash;
  if (!audioMatches && body.querySelector('.deep-dive-actions [data-audio-action]')) {
    body.querySelector('.deep-dive-actions [data-audio-action]').replaceWith(node('span', 'Audio unavailable for this version.', 'audio-pending'));
  }
  const sections = [...body.querySelectorAll('.deep-dive-section')];
  sections.forEach((section, index) => {
    section.id = dive.sections[index]?.id || `dive-section-${index}`;
    section.querySelector('h2')?.setAttribute('tabindex', '-1');
  });
  const saved = state.readingProgress[dive.id];
  if (isTranscriptDestination) {
    const back = body.querySelector('.back-link');
    if (back) back.textContent = transcriptReturnLabel();
  } else trackReadingPlace(dive.id, dive.revision_id, '.deep-dive-section');
}

function transcriptReturnLabel() {
  const hash = transcriptReturnTo?.hash || '#today';
  if (hash === '#library') return '← Back to Library';
  if (hash === '#topics' || hash.startsWith('#topic/')) return '← Back to Topics';
  return '← Back to Today';
}

function returnFromTranscript() {
  const target = transcriptReturnTo || { hash: '#today', scrollY: 0 };
  transcriptReturnTo = null; transcriptReturnHash = null;
  pendingScrollRestore = target.scrollY;
  pendingSourceFocusID = target.sourceID || null;
  pendingPlayerReturnFocus = true;
  if (history.state?.transcriptHash === location.hash && history.length > 1) history.back();
  else navigate(target.hash);
}

function renderSourcePage(id) {
  const source = pack.sources.find(item => item.id === id);
  if (!source) return renderMissing();
  const heading = node('h1', source.title);
  heading.tabIndex = -1;
  content.append(node('p', 'SOURCE REFERENCE', 'eyebrow'), heading);
  content.append(node('p', source.note || 'This is a background reference, not evidence for the fictional sample.'));
  const details = node('dl', null, 'source-details');
  const supportedSection = pack.editions.flatMap(edition => edition.sections).find(section => section.id === source.supported_section)?.heading || source.supported_section;
  for (const [label, value] of [['Publisher', source.publisher], ['Date', source.date], ['Kind', source.kind], ['Supported section', supportedSection], ['Scope', source.scope], ['Availability', source.availability]]) {
    if (!value) continue;
    details.append(node('dt', label), node('dd', value));
  }
  content.append(details);
  const backButton = node('button', '← Return to briefing'); backButton.type = 'button'; backButton.addEventListener('click', back);
  const row = node('div', null, 'button-row');
  if (source.url) {
    const external = link('Open the original source ↗', source.url, 'button-link');
    external.target = '_blank'; external.rel = 'noopener noreferrer';
    row.append(external);
  }
  row.append(backButton); content.append(row);
}

function renderPreferences() {
  content.append(node('p', 'YOUR PREFERENCES', 'eyebrow'), node('h1', 'Choose what matters to you'));
  content.append(node('p', 'Select one to five topics for future editions. The current fictional sample is fixed so its text and eventual recording stay in sync. Your choices stay in this browser.'));
  const form = node('form', null, 'settings-list');
  const topics = node('fieldset'); topics.append(node('legend', 'Topics'));
  for (const topic of pack.topics) {
    const label = node('label'); const input = node('input'); input.type = 'checkbox'; input.name = 'topic'; input.value = topic.id; input.checked = state.selectedIds.includes(topic.id);
    label.append(input, node('span', `${topic.label} — ${topic.scope}`)); topics.append(label);
  }
  const duration = node('fieldset'); duration.append(node('legend', 'Daily time budget'));
  for (const minutes of [10, 12, 15]) {
    const label = node('label'); const input = node('input'); input.type = 'radio'; input.name = 'duration'; input.value = String(minutes); input.checked = state.durationMinutes === minutes;
    label.append(input, node('span', `${minutes} minutes`)); duration.append(label);
  }
  const timezone = node('fieldset'); timezone.append(node('legend', 'Time zone'));
  const zoneLabel = node('label', 'Your IANA time zone');
  const zone = node('input'); zone.type = 'text'; zone.name = 'timezone'; zone.value = state.timezone; zone.setAttribute('aria-label', 'Time zone'); zone.autocomplete = 'off';
  timezone.append(zoneLabel, zone);
  const message = node('p', '', 'error'); message.setAttribute('role', 'status');
  const submit = node('button', 'Save topics', 'primary'); submit.type = 'submit';
  form.append(topics, duration, timezone, message, submit);
  form.addEventListener('submit', event => {
    event.preventDefault();
    const picked = [...form.querySelectorAll('input[name=topic]:checked')].map(input => input.value);
    if (!picked.length || picked.length > 5) { message.textContent = 'Choose between one and five topics.'; return; }
    const durationMinutes = Number(form.querySelector('input[name=duration]:checked')?.value);
    try { new Intl.DateTimeFormat('en-US', { timeZone: zone.value.trim() }); }
    catch { message.textContent = 'Enter a valid time zone, such as America/New_York.'; return; }
    state = saveState({ ...state, selectedIds: picked, priorityIds: state.priorityIds.filter(id => picked.includes(id)), durationMinutes, timezone: zone.value.trim() });
    navigate('#today');
  });
  content.append(form);
  const reset = node('button', 'Reset this browser’s choices'); reset.type = 'button';
  reset.addEventListener('click', () => { player.close(); state = resetState(); navigate('#preferences'); });
  content.append(node('hr', null, 'rule'), reset, node('p', 'Reset clears local choices, saves and listening progress. It does not affect another device.', 'local-note'));
}

function renderLibrary() {
  content.append(node('p', 'YOUR LIBRARY', 'eyebrow'), node('h1', 'Editions and explanations'));
  content.append(node('p', 'This is a small illustrative library. Saved items and progress live only in this browser.'));
  const list = node('ul', null, 'library-list');
  const editions = currentItems(pack.editions);
  const deepDives = currentItems(pack.deepDives);
  const sectionLookup = new Map(editions.flatMap(edition => edition.sections.map(section => [section.id, { section, edition }])));
  for (const id of state.savedIds) {
    const match = sectionLookup.get(id);
    if (!match) continue;
    const li = node('li');
    const destination = `#edition/${encodeURIComponent(match.edition.kind)}/${encodeURIComponent(match.edition.id)}`;
    li.append(link(match.section.heading, destination));
    li.append(node('small', ' · Saved section', 'muted'));
    const remove = node('button', 'Remove saved item'); remove.type = 'button'; remove.addEventListener('click', () => toggleSave(id));
    li.append(node('br'), remove); list.append(li);
  }
  for (const edition of editions) {
    const li = node('li'); li.append(link(edition.title, `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`));
    const correction = edition.corrections?.find(item => item.severity === 'material');
    const savedRevision = state.savedRevisions[edition.id];
    li.append(node('small', ` · ${edition.kind === 'daily' ? 'Daily' : 'Weekly'} sample${state.savedIds.includes(edition.id) ? ' · Saved' : ''}${correction ? ' · Corrected' : ''}${savedRevision && savedRevision !== edition.revision_id ? ' · Updated since saved' : ''}`, 'muted')); list.append(li);
  }
  for (const dive of deepDives) {
    const li = node('li'); li.append(link(dive.title, `#explanation/${encodeURIComponent(dive.id)}`));
    li.querySelector('a').addEventListener('click', event => { event.preventDefault(); remember(); deepDiveReturnTo = returnTo; navigate(`#explanation/${encodeURIComponent(dive.id)}`); });
    li.append(node('small', ` · Topic explanation${state.savedIds.includes(dive.id) ? ' · Saved' : ''}`, 'muted')); list.append(li);
  }
  content.append(list);
  const settings = link('Edit your topics', '#preferences', 'button-link'); content.append(settings);
}

function renderMissing() {
  content.append(node('h1', 'This page is unavailable'), node('p', 'Check the link or return to today.'));
  content.append(link('Return to today', '#today', 'button-link'));
}

function route() {
  if (!pack) return;
  readingObserver?.disconnect(); readingObserver = null;
  content.textContent = '';
  const routeName = decodeURIComponent((location.hash || '#today').slice(1));
  const routeHash = location.hash || '#today';
  if (history.state?.transcriptHash === routeHash && history.state?.transcriptOrigin) {
    transcriptReturnTo = history.state.transcriptOrigin;
    transcriptReturnHash = history.state.transcriptHash;
  } else if (transcriptReturnHash && routeHash !== transcriptReturnHash && !routeName.startsWith('source/')) {
    if (routeHash === transcriptReturnTo?.hash) {
      pendingScrollRestore = transcriptReturnTo.scrollY;
      pendingPlayerReturnFocus = true;
    }
    transcriptReturnTo = null; transcriptReturnHash = null;
  }
  if (!routeName.startsWith('topic/') && !routeName.startsWith('explanation/') && !routeName.startsWith('source/')) deepDiveReturnTo = null;
  if (routeName.startsWith('topic/') && !deepDiveReturnTo) {
    deepDiveReturnTo = { hash: '#topics', sectionId: null, scrollY: 0 };
  }
  const group = routeName === 'topics' || routeName.startsWith('topic/') || routeName.startsWith('explanation/') || routeName === 'preferences' ? 'topics' : routeName === 'library' ? 'library' : 'today';
  const editionKind = routeName.startsWith('edition/') ? routeName.slice(8).split('/')[0] : null;
  const pageTitle = routeName === 'preferences' ? 'Settings' : routeName.startsWith('source/') ? 'Source' : group === 'topics' ? 'Topics' : group === 'library' ? 'Library' : (routeName === 'weekly' || editionKind === 'weekly') ? 'Weekly' : 'Today';
  document.querySelector('[data-page-title]').textContent = pageTitle;
  document.querySelector('.settings-link').hidden = routeName === 'preferences';
  document.title = `${pageTitle} · Personal briefing`;
  for (const item of nav) item.setAttribute('aria-current', item.dataset.nav === group ? 'page' : 'false');
  if (routeName === 'today' || !routeName) renderEditionPage('daily');
  else if (routeName === 'weekly') renderEditionPage('weekly');
  else if (routeName.startsWith('edition/')) {
    const parts = routeName.slice(8).split('/');
    const kind = parts.length > 1 ? parts.shift() : null;
    const id = parts.join('/');
    const edition = currentRevision(pack.editions, id, item => !kind || item.kind === kind);
    if (edition) renderEditionPage(edition.kind, edition); else renderMissing();
  }
  else if (routeName.startsWith('explanation/')) {
    const dive = currentRevision(pack.deepDives, routeName.slice(12));
    if (dive) renderDeepDivePage(dive.topic_id, dive); else renderMissing();
  }
  else if (routeName === 'topics') renderTopicsPage();
  else if (routeName.startsWith('topic/')) renderDeepDivePage(routeName.slice(6));
  else if (routeName.startsWith('source/')) renderSourcePage(routeName.slice(7));
  else if (routeName === 'preferences') renderPreferences();
  else if (routeName === 'library') renderLibrary();
  else renderMissing();
  player.refresh();
  const restoreScroll = pendingScrollRestore;
  pendingScrollRestore = null;
  if (restoreScroll != null) requestAnimationFrame(() => window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
  else if (routeName !== 'preferences') window.scrollTo({ top: 0, behavior: 'instant' });
  const sourceFocusID = pendingSourceFocusID;
  pendingSourceFocusID = null;
  if (sourceFocusID) requestAnimationFrame(() => [...content.querySelectorAll('[data-source]')].find(button => button.dataset.source === sourceFocusID)?.focus({ preventScroll: true }));
  else if (routeName.startsWith('source/')) requestAnimationFrame(() => content.querySelector('h1')?.focus({ preventScroll: true }));
  const focusReturnHeading = pendingReturnHeadingFocus;
  pendingReturnHeadingFocus = false;
  if (focusReturnHeading && !sourceFocusID) requestAnimationFrame(() => (content.querySelector('h1') || document.querySelector('[data-page-title]'))?.focus({ preventScroll: true }));
  const placement = pendingTranscriptPlacement;
  pendingTranscriptPlacement = null;
  if (placement) requestAnimationFrame(() => requestAnimationFrame(() => {
    const correction = placement.correction && content.querySelector('.correction-notice');
    const section = placement.sectionId && content.querySelector(`#${CSS.escape(placement.sectionId)}`);
    const target = correction || section || content.querySelector('h1');
    if (correction || target?.tagName === 'H1') target.tabIndex = -1;
    if (section) section.querySelector('h2')?.focus({ preventScroll: true });
    else target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'start', behavior: 'instant' });
    const editionParts = routeName.startsWith('edition/') ? routeName.slice(8).split('/') : [];
    const editionKind = editionParts.length > 1 ? editionParts[0] : null;
    const edition = editionParts.length ? currentRevision(pack.editions, placement.id, item => !editionKind || item.kind === editionKind) : null;
    const dive = routeName.startsWith('explanation/') ? currentRevision(pack.deepDives, placement.id) : null;
    if (edition && !placement.correction) trackReadingPlace(edition.id, edition.revision_id, '.edition-section');
    else if (dive && !placement.correction) trackReadingPlace(dive.id, dive.revision_id, '.deep-dive-section');
  }));
  if (pendingPlayerReturnFocus) requestAnimationFrame(() => requestAnimationFrame(() => {
    pendingPlayerReturnFocus = false;
    (document.querySelector('.mini-player-open') || content.querySelector('h1') || document.querySelector('[data-page-title]'))?.focus({ preventScroll: true });
  }));
}

window.addEventListener('hashchange', route);
loadPack().then(data => {
  pack = data;
  const saved = state.activeAudio;
  if (saved) {
    const item = [...pack.editions, ...pack.deepDives].find(entry => entry.id === saved.id && entry.revision_id === saved.revisionId);
    if (item) player.restore({ ...item, kind: item.kind || 'deep-dive' });
    else {
      state.activeAudio = null;
      state = saveState(state);
      const corrected = pack.editions.find(entry => entry.id === saved.id && entry.corrections?.some(correction => correction.previous_revision_id === saved.revisionId));
      if (corrected) sessionStorage.setItem('briefing:correction-audio-notice', 'The prior recording is no longer available for this corrected edition.');
    }
  }
  route();
}).catch(error => {
  content.textContent = '';
  content.append(node('h1', 'The sample is unavailable'), node('p', error.message, 'error'));
  const retry = node('button', 'Retry'); retry.addEventListener('click', () => location.reload()); content.append(retry);
});
