import { renderEdition, renderToday, visibleSections } from './edition/edition.js';
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
let pendingRouteContext = null;
const rootScroll = new Map();
let libraryFilter = sessionStorage.getItem('briefing:library-filter') === 'saved' ? 'saved' : 'editions';

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

function focusToken() {
  const active = document.activeElement;
  if (!active) return null;
  if (active.dataset?.nav) return { type: 'nav', id: active.dataset.nav };
  if (active.classList?.contains('settings-link')) return { type: 'settings' };
  if (!content.contains(active)) return null;
  if (active.dataset.source) return { type: 'source', id: active.dataset.source };
  if (active.dataset.topic) return { type: 'topic', id: active.dataset.topic };
  if (active.hasAttribute('data-read-start')) return { type: 'read-start' };
  if (active.hasAttribute('data-continue')) return { type: 'continue' };
  if (active.dataset.readSection) return { type: 'read-section', id: active.dataset.readSection };
  if (active instanceof HTMLAnchorElement && active.getAttribute('href')?.startsWith('#')) return { type: 'href', id: active.getAttribute('href') };
  if (active.id) return { type: 'id', id: active.id };
  return null;
}
function remember(sectionId = null) {
  returnTo = { hash: location.hash || '#today', sectionId, scrollY: window.scrollY, focus: focusToken(), sourceID: document.activeElement?.dataset?.source || null };
  return returnTo;
}
function rootForHash(hash = location.hash || '#today', context = history.state?.routeContext) {
  if (hash === '#today') return 'today';
  if (hash === '#topics') return 'topics';
  if (hash === '#library') return 'library';
  if (context?.root && ['today', 'topics', 'library'].includes(context.root)) return context.root;
  if (['#weekly'].includes(hash) || hash.startsWith('#edition/')) return 'today';
  if (hash.startsWith('#topic/') || hash.startsWith('#explanation/')) return 'topics';
  return 'today';
}
function navigate(hash, context = null) {
  const currentHash = location.hash || '#today';
  if (['#today', '#topics', '#library'].includes(currentHash)) rootScroll.set(currentHash, window.scrollY);
  if (currentHash !== hash && ['#today', '#topics', '#library'].includes(hash)) pendingScrollRestore = rootScroll.get(hash) ?? 0;
  const currentState = history.state || {};
  history.replaceState({ ...currentState, viewState: { scrollY: window.scrollY, focus: focusToken() || context?.parentFocus || null } }, '', location.href);
  pendingRouteContext = context || { root: rootForHash(currentHash), parentHash: currentHash, parentScroll: window.scrollY, parentFocus: focusToken() };
  if (currentHash === hash) route(); else location.hash = hash;
}
function navigateTranscript(hash, placement) {
  pendingTranscriptPlacement = placement;
  if (location.hash === hash) route();
  else {
    history.pushState({ ...(history.state || {}), transcriptOrigin: { ...transcriptReturnTo }, transcriptHash: hash,
      routeContext: { root: rootForHash(transcriptReturnTo.hash), parentHash: transcriptReturnTo.hash, parentScroll: transcriptReturnTo.scrollY, parentFocus: transcriptReturnTo.focus, transcript: true } }, '', hash);
    route();
  }
}
function openSource(id) {
  const parent = remember();
  parent.focus = { type: 'source', id }; parent.sourceID = id;
  navigate(`#source/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash, parentScroll: parent.scrollY, parentFocus: parent.focus, parentType: parent.hash.startsWith('#explanation/') || parent.hash.startsWith('#topic/') ? 'explanation' : 'briefing' });
}
function openDeepDive(id, sectionId = null) {
  const parent = remember(sectionId);
  parent.focus ||= { type: 'topic', id };
  deepDiveReturnTo = returnTo;
  navigate(`#topic/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash, parentScroll: parent.scrollY, parentFocus: parent.focus, sectionId });
}
function backTo(target) {
  if (history.state?.routeContext?.parentHash === target.hash && history.length > 1) {
    history.back();
  } else {
    pendingScrollRestore = target.scrollY;
    pendingSourceFocusID = target.sourceID || null;
    pendingReturnHeadingFocus = !target.focus && !target.sourceID;
    navigate(target.hash, { root: rootForHash(target.hash), parentHash: null, restore: { scrollY: target.scrollY, focus: target.focus || (target.sourceID ? { type: 'source', id: target.sourceID } : null) } });
  }
}
function back() {
  if (transcriptReturnTo && transcriptReturnHash && location.hash.startsWith('#source/') && returnTo.hash === transcriptReturnHash) {
    pendingScrollRestore = returnTo.scrollY;
    pendingSourceFocusID = returnTo.sourceID || null;
    history.back();
    return;
  }
  const parent = history.state?.routeContext?.parentHash;
  if (parent && history.length > 1) history.back();
  else backTo(returnTo);
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
function displayDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? String(value || '') : new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
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
  return item.kind === 'weekly' && hash === '#weekly';
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
  transcriptReturnTo = { hash: location.hash || '#today', sectionId: null, scrollY: window.scrollY, focus: focusToken() };
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
  const routeContext = history.state?.routeContext || {};
  const parentHash = routeContext.parentHash;
  const returnHash = parentHash?.startsWith('#edition/') || parentHash === '#weekly' ? parentHash : parentHash === '#library' ? '#library' : '#today';
  const returnLabel = returnHash === '#library' ? 'Library' : returnHash === '#today' ? 'Today' : 'Briefing';
  const readerHeader = node('div', null, 'reader-sticky');
  const backButton = node('button', `‹ ${returnLabel}`, 'reader-back');
  if (transcriptDestination(edition.id, edition.kind)) backButton.classList.add('transcript-return');
  backButton.type = 'button';
  backButton.addEventListener('click', () => {
    if (transcriptDestination(edition.id, edition.kind)) { returnFromTranscript(); return; }
    if (routeContext.parentHash && history.length > 1) history.back();
    else navigate(returnHash, { root: rootForHash(returnHash), parentHash: null });
  });
  const contentsButton = node('button', 'Contents', 'reader-contents-open'); contentsButton.type = 'button';
  readerHeader.append(backButton, contentsButton);
  content.append(readerHeader);
  const body = node('div'); content.append(body);
  renderEdition(body, edition, pack.topics, {
    sources: pack.sources,
    play: item => player.play(item),
    openTopic: openDeepDive,
    hasDeepDive: id => pack.deepDives.some(item => item.topic_id === id),
    deepDives: currentItems(pack.deepDives),
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
  const dialog = node('dialog', null, 'reader-contents-dialog');
  let jumpedFromContents = false;
  const dialogHead = node('div', null, 'reader-contents-heading');
  dialogHead.append(node('h2', 'In this edition'));
  const close = node('button', 'Close'); close.type = 'button'; close.addEventListener('click', () => dialog.close());
  dialogHead.append(close); dialog.append(dialogHead);
  const list = node('ol');
  visibleSections(edition).forEach((section, index) => {
    const item = node('li'); const jump = node('button', `${String(index + 1).padStart(2, '0')}  ${section.heading}`);
    jump.type = 'button'; jump.addEventListener('click', () => { jumpedFromContents = true; dialog.close(); continueToSection(section.id); });
    item.append(jump); list.append(item);
  });
  dialog.append(list); dialog.addEventListener('close', () => { if (!jumpedFromContents) contentsButton.focus(); });
  content.append(dialog);
  contentsButton.addEventListener('click', () => dialog.showModal());
}

function continueToSection(sectionId) {
  const section = document.getElementById(sectionId);
  section?.scrollIntoView({ block: 'start' });
  section?.querySelector('h2')?.focus({ preventScroll: true });
}

function renderTodayPage() {
  const edition = currentEdition('daily');
  if (!edition) return renderMissing();
  const body = node('div'); content.append(body);
  renderToday(body, edition, pack.topics, {
    weekly: currentEdition('weekly'),
    readingPlace: (id, revision) => {
      const place = state.readingProgress[id];
      return place?.revisionId === revision ? place.sectionId : null;
    },
    openReader: sectionId => {
      const hash = `#edition/daily/${encodeURIComponent(edition.id)}${sectionId ? `/${encodeURIComponent(sectionId)}` : ''}`;
      navigate(hash, { root: 'today', parentHash: '#today', parentScroll: window.scrollY, parentFocus: focusToken(), sectionId });
    },
  });
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
  body.className = 'deep-dive-page';
  const isTranscriptDestination = transcriptDestination(dive.id, 'deep-dive');
  const routeContext = history.state?.routeContext;
  const origin = isTranscriptDestination ? transcriptReturnTo : (routeContext?.parentHash ? { hash: routeContext.parentHash, scrollY: routeContext.parentScroll || 0, focus: routeContext.parentFocus } : (deepDiveReturnTo || returnTo));
  const root = rootForHash(location.hash, routeContext);
  const backLabel = origin.hash?.startsWith('#edition/') || origin.hash === '#weekly' ? '‹ Briefing' : root === 'library' ? '‹ Library' : root === 'today' ? '‹ Briefing' : '‹ Topics';
  const sticky = node('div', null, 'reader-sticky');
  const back = node('button', backLabel, 'reader-back'); back.type = 'button';
  if (isTranscriptDestination) back.classList.add('transcript-return');
  back.addEventListener('click', () => {
    if (isTranscriptDestination) { returnFromTranscript(); return; }
    if (routeContext?.parentHash && !routeContext.direct && history.length > 1) history.back();
    else navigate(root === 'library' ? '#library' : root === 'today' ? '#today' : '#topics', { root, parentHash: null });
  });
  sticky.append(back); content.insertBefore(sticky, body);
  renderDeepDive(body, topic, dive, pack.sources, {
    play: item => player.play(item),
    openSource,
    save: (itemId, revision) => toggleSave(itemId, revision, false),
    isSaved: itemId => state.savedIds.includes(itemId),
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
  const routeContext = history.state?.routeContext || {};
  const parentType = routeContext.parentType || (routeContext.parentHash?.startsWith('#explanation/') || routeContext.parentHash?.startsWith('#topic/') ? 'explanation' : 'briefing');
  const sticky = node('div', null, 'reader-sticky');
  const backButton = node('button', parentType === 'explanation' ? '‹ Explanation' : '‹ Briefing', 'reader-back');
  backButton.type = 'button'; backButton.addEventListener('click', back);
  sticky.append(backButton); content.append(sticky);
  const domain = (() => { try { return new URL(source.url).hostname.replace(/^www\./, ''); } catch { return ''; } })();
  const publisherNames = { 'federalreserve.gov': 'Federal Reserve', 'fdic.gov': 'FDIC', 'consumerfinance.gov': 'Consumer Financial Protection Bureau', 'pages.nist.gov': 'NIST', 'content.naic.org': 'National Association of Insurance Commissioners', 'theifab.com': 'The IFAB' };
  const publisher = source.publisher || publisherNames[domain] || domain || 'Reference';
  const heading = node('h1', source.title); heading.tabIndex = -1;
  heading.className = 'source-title';
  content.append(node('p', publisher, 'source-publisher'), heading);
  const row = node('div', null, 'button-row source-actions');
  if (source.url) {
    const external = link('Open original ↗', source.url, 'button-link');
    external.target = '_blank'; external.rel = 'noopener noreferrer';
    row.append(external);
  }
  if (source.url) row.append(node('span', 'Opens in a new tab', 'source-new-tab'));
  content.append(row);
  content.append(node('h2', 'Why this source', 'source-section-heading'));
  content.append(node('p', source.note || 'This source provides background for the explanation.'));
  const limits = source.scope || source.availability || 'This reference supports background context; it does not verify the fictional example or establish an individual outcome.';
  content.append(node('h2', 'Limits', 'source-section-heading'));
  content.append(node('p', limits));
  if (source.date) content.append(node('p', `Published ${source.date}`, 'source-published'));
  if (domain) content.append(node('p', domain, 'source-domain'));
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
    sessionStorage.setItem('briefing:settings-saved', 'Topic choices saved.');
    const parent = history.state?.routeContext?.parentHash;
    if (parent && history.length > 1) history.back();
    else navigate(history.state?.routeContext?.root === 'library' ? '#library' : history.state?.routeContext?.root === 'topics' ? '#topics' : '#today', { root: history.state?.routeContext?.root || 'today', parentHash: null });
  });
  content.append(form);
  const reset = node('button', 'Reset this browser’s choices'); reset.type = 'button';
  reset.addEventListener('click', () => { player.close(); state = resetState(); navigate('#preferences'); });
  content.append(node('hr', null, 'rule'), reset, node('p', 'Reset clears local choices, saves and listening progress. It does not affect another device.', 'local-note'));
}

function renderLibrary() {
  content.append(node('h1', 'Library'));
  content.append(node('p', 'Revisit daily editions, weekly recaps and items you saved on this device.', 'library-intro'));
  const filters = node('div', null, 'library-filters');
  const editionsTab = node('button', 'Editions'); editionsTab.type = 'button'; editionsTab.setAttribute('aria-pressed', String(libraryFilter === 'editions'));
  const savedTab = node('button', 'Saved'); savedTab.type = 'button'; savedTab.setAttribute('aria-pressed', String(libraryFilter === 'saved'));
  filters.append(editionsTab, savedTab); content.append(filters);
  const section = node('section', null, 'library-results'); content.append(section);
  const editions = currentItems(pack.editions);
  const deepDives = currentItems(pack.deepDives);
  const sectionLookup = new Map(editions.flatMap(edition => edition.sections.map(section => [section.id, { section, edition }])));
  const addRow = (title, type, detail, hash) => {
    const li = node('li'); li.append(link(title, hash));
    li.append(node('small', `${type} · ${detail}`, 'muted')); section.querySelector('ul').append(li);
  };
  const renderRows = () => {
    section.replaceChildren();
    const list = node('ul', null, 'library-list'); section.append(list);
    if (libraryFilter === 'editions') {
      for (const edition of editions.sort((a, b) => revisionDate(b).localeCompare(revisionDate(a)))) {
        const date = edition.kind === 'weekly' ? `Week of ${displayDate(edition.week_start)}` : displayDate(edition.local_date);
        addRow(edition.title, edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `${date} · About ${readingMinutesFor(edition)} min`, `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`);
      }
    } else {
      const savedEditions = editions.filter(item => state.savedIds.includes(item.id));
      const savedDives = deepDives.filter(item => state.savedIds.includes(item.id));
      const savedSections = state.savedIds.filter(id => sectionLookup.has(id));
      if (!savedEditions.length && !savedDives.length && !savedSections.length) {
        section.append(node('p', 'Save an edition or explanation to find it here.', 'library-empty'));
      }
      for (const edition of savedEditions) {
        const date = edition.kind === 'weekly' ? `Week of ${displayDate(edition.week_start)}` : displayDate(edition.local_date);
        addRow(edition.title, edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `${date} · About ${readingMinutesFor(edition)} min`, `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}`);
      }
      for (const dive of savedDives) {
        const topic = pack.topics.find(item => item.id === dive.topic_id);
        addRow(dive.title, `${topic?.label || 'Topic'} explanation`, `About ${dive.duration_minutes} min`, `#explanation/${encodeURIComponent(dive.id)}`);
      }
      for (const id of savedSections) {
        const match = sectionLookup.get(id);
        addRow(match.section.heading, 'Saved section', match.edition.kind === 'weekly' ? 'Weekly recap' : 'Daily edition', `#edition/${encodeURIComponent(match.edition.kind)}/${encodeURIComponent(match.edition.id)}/${encodeURIComponent(id)}`);
      }
    }
    section.append(node('p', 'Saved on this device', 'library-device-note'));
  };
  const readingMinutesFor = item => Math.max(1, Math.ceil(visibleSections(item).reduce((count, part) => count + part.body.split(/\s+/).length, 0) / 225));
  editionsTab.addEventListener('click', () => { libraryFilter = 'editions'; sessionStorage.setItem('briefing:library-filter', libraryFilter); editionsTab.setAttribute('aria-pressed', 'true'); savedTab.setAttribute('aria-pressed', 'false'); renderRows(); });
  savedTab.addEventListener('click', () => { libraryFilter = 'saved'; sessionStorage.setItem('briefing:library-filter', libraryFilter); editionsTab.setAttribute('aria-pressed', 'false'); savedTab.setAttribute('aria-pressed', 'true'); renderRows(); });
  renderRows();
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
  const routeState = history.state || {};
  if (pendingRouteContext) {
    history.replaceState({ ...routeState, routeContext: pendingRouteContext }, '', location.href);
    pendingRouteContext = null;
  } else if (!routeState.routeContext) {
    const directRoot = rootForHash(routeHash, null);
    const directParent = routeName.startsWith('explanation/') ? '#topics' : routeName.startsWith('topic/') ? '#topics' : null;
    history.replaceState({ ...routeState, routeContext: { root: directRoot, parentHash: directParent, parentScroll: 0, parentFocus: null, direct: Boolean(directParent) } }, '', location.href);
  }
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
  const group = rootForHash(routeHash, history.state?.routeContext);
  const editionKind = routeName.startsWith('edition/') ? routeName.slice(8).split('/')[0] : null;
  const isReader = routeName === 'weekly' || routeName.startsWith('edition/') || routeName.startsWith('explanation/') || routeName.startsWith('topic/') || routeName.startsWith('source/');
  document.body.classList.toggle('is-reader', isReader);
  const pageTitle = routeName === 'preferences' ? 'Settings' : routeName.startsWith('source/') ? 'Source' : routeName.startsWith('explanation/') || routeName.startsWith('topic/') ? 'Explanation' : group === 'topics' ? 'Topics' : group === 'library' ? 'Library' : (routeName === 'weekly' || editionKind === 'weekly') ? 'Weekly' : 'Today';
  document.querySelector('[data-page-title]').textContent = pageTitle;
  document.querySelector('.settings-link').hidden = routeName === 'preferences';
  document.title = `${pageTitle} · Personal briefing`;
  for (const item of nav) item.setAttribute('aria-current', item.dataset.nav === group ? 'page' : 'false');
  if (routeName === 'today' || !routeName) renderTodayPage();
  else if (routeName === 'weekly') renderEditionPage('weekly');
  else if (routeName.startsWith('edition/')) {
    const parts = routeName.slice(8).split('/');
    const kind = parts.length > 1 ? parts.shift() : null;
    const id = parts.shift();
    const sectionId = parts.shift() || history.state?.routeContext?.sectionId || null;
    const edition = currentRevision(pack.editions, id, item => !kind || item.kind === kind);
    if (edition) {
      renderEditionPage(edition.kind, edition);
      if (sectionId) requestAnimationFrame(() => continueToSection(sectionId));
    } else renderMissing();
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
  const settingsNotice = sessionStorage.getItem('briefing:settings-saved');
  if (settingsNotice && ['today', 'topics', 'library'].includes(group) && !isReader) {
    const status = node('p', settingsNotice, 'settings-confirmation'); status.setAttribute('role', 'status');
    content.prepend(status); sessionStorage.removeItem('briefing:settings-saved');
  }
  player.refresh();
  const restoreScroll = pendingScrollRestore;
  pendingScrollRestore = null;
  const viewState = history.state?.viewState || history.state?.routeContext?.restore;
  if (restoreScroll != null) requestAnimationFrame(() => window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
  else if (viewState && routeName !== 'preferences') requestAnimationFrame(() => window.scrollTo({ top: viewState.scrollY || 0, behavior: 'instant' }));
  else if (routeName !== 'preferences') window.scrollTo({ top: 0, behavior: 'instant' });
  const sourceFocusID = pendingSourceFocusID;
  pendingSourceFocusID = null;
  if (sourceFocusID) requestAnimationFrame(() => [...content.querySelectorAll('[data-source]')].find(button => button.dataset.source === sourceFocusID)?.focus({ preventScroll: true }));
  else if (routeName.startsWith('source/')) requestAnimationFrame(() => content.querySelector('h1')?.focus({ preventScroll: true }));
  else if (viewState?.focus) requestAnimationFrame(() => {
    const selector = viewState.focus.type === 'source' ? `[data-source="${CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'topic' ? `[data-topic="${CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'read-start' ? '[data-read-start]' : viewState.focus.type === 'continue' ? '[data-continue]' :
      viewState.focus.type === 'read-section' ? `[data-read-section="${CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'nav' ? `[data-nav="${CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'settings' ? '.settings-link' : viewState.focus.type === 'href' ? null : `#${CSS.escape(viewState.focus.id)}`;
    const target = viewState.focus.type === 'nav' || viewState.focus.type === 'settings' ? document.querySelector(selector) :
      viewState.focus.type === 'href' ? [...content.querySelectorAll('a[href]')].find(anchor => anchor.getAttribute('href') === viewState.focus.id) : content.querySelector(selector);
    if (target?.closest('details')) target.closest('details').open = true;
    target?.focus({ preventScroll: true });
  });
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
document.addEventListener('click', event => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const anchor = event.target.closest?.('a[href^="#"]');
  if (!anchor || anchor.target === '_blank') return;
  const hash = anchor.getAttribute('href');
  if (!hash || hash === '#content') return;
  event.preventDefault();
  const isRoot = ['#today', '#topics', '#library'].includes(hash);
  const root = isRoot ? hash.slice(1) : rootForHash(location.hash);
  navigate(hash, { root, parentHash: isRoot ? null : (location.hash || '#today'), parentScroll: window.scrollY, parentFocus: focusToken() });
});
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
