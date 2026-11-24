export function createStoryInteractionController(app) {
  const { content, node, recordSignal, behaviorWeights, saveProfile, clearSignals, saveState, invalidateAudioProgress, audioScriptText, hasMaterialCorrection, currentRevision, compareRevision, personalized, visibleSections, focusToken, navigateTranscript, navigate, route, invalidatedEditionAudio, showToast } = app;
function topicIdFor(item, sectionId = null) {
  if (!item) return 'unknown';
  if (item.topic_id) return item.topic_id;
  const section = (item.sections || []).find(row => row.id === sectionId) || item.sections?.[0];
  return section?.topic_ids?.[0] || section?.topic_id || item.coverage?.[0]?.topic_id || 'unknown';
}
function signalForItem(type, item, sectionId = null) {
  if (!item) return false;
  const section = (item.sections || []).find(row => row.id === sectionId) || item.sections?.[0];
  const recorded = app.recordSignal(type, section?.id || item.id, topicIdFor(item, sectionId));
  if (recorded && app.interestProfile?.topics?.length) {
    const topicIds = app.interestProfile.topics.map(topic => topic.id);
    app.interestProfile = app.saveProfile({ ...app.interestProfile, behavior_weights: app.behaviorWeights(topicIds) });
  }
  return recorded;
}

function resetBehaviorWeighting() {
  app.clearSignals();
  if (app.interestProfile) {
    app.interestProfile = app.saveProfile({ ...app.interestProfile, behavior_weights: {} });
  }
}
function signalCurrent(type, storyId = null) {
  const hash = location.hash || '#today';
  if (hash.startsWith('#edition/')) {
    const parts = hash.slice(1).split('/').map(decodeURIComponent);
    const item = app.pack?.editions.find(entry => entry.id === parts[2] && entry.kind === parts[1]);
    return signalForItem(type, item, storyId || parts[3]);
  }
  if (hash.startsWith('#explanation/') || hash.startsWith('#topic/')) {
    const id = decodeURIComponent(hash.slice(hash.indexOf('/') + 1));
    const item = app.pack?.deepDives.find(entry => entry.id === id || entry.topic_id === id);
    return signalForItem(type, item);
  }
  return false;
}
function toggleSave(id, revisionId = null, rerender = true) {
  const adding = !app.state.savedIds.includes(id);
  if (app.state.savedIds.includes(id)) {
    app.state.savedIds = app.state.savedIds.filter(x => x !== id);
    delete app.state.savedRevisions[id];
  } else {
    app.state.savedIds = [...app.state.savedIds, id];
    if (revisionId) app.state.savedRevisions[id] = revisionId;
  }
  app.state = app.saveState(app.state);
  if (adding) {
    const item = [...(app.pack?.editions || []), ...(app.pack?.deepDives || [])].find(entry => entry.id === id);
    const parts = (location.hash || '').startsWith('#edition/') ? location.hash.slice(1).split('/').map(decodeURIComponent) : [];
    signalForItem('save', item, parts[3] || null);
  }
  showToast(adding ? 'Saved for later.' : 'Removed from saved items.');
  if (rerender) app.route();
}
function setFeedback(id, category) {
  app.state.feedback[id] = category; app.state = app.saveState(app.state);
  const type = { more: 'more', less: 'less', mute: 'mute' }[category];
  if (type) signalCurrent(type, id);
  app.route();
}
function listenText(item) {
  if (typeof item === 'string') return item;
  const sections = item?.sections || [];
  if (sections.length) return sections.map(section => app.audioScriptText(section) || `${section.heading || ''}. ${section.body || ''}`).join(' ');
  return `${item?.title || ''}. ${item?.body || ''}`;
}

function listenEstimate(item) {
  return app.player.estimateListenMinutes(listenText(item));
}

function listenEstimateWithUnit(item) { return `${listenEstimate(item)} min`; }

function browserCanSpeak() {
  return typeof window.speechSynthesis !== 'undefined' && typeof window.SpeechSynthesisUtterance === 'function';
}

function canListen(item) {
  return Boolean(item && browserCanSpeak() &&
    !app.invalidatedEditionAudio.has(item.id) && !app.hasMaterialCorrection(item));
}

function startListening(item) {
  if (!canListen(item)) return;
  item = app.withNarrationTopics(item);
  if (app.player.item?.id === item.id && app.player.item?.revision_id === item.revision_id) app.player.play({ ...item, start_section_id: null });
  else {
    const saved = app.state.readingProgress[item.id];
    const fromParagraph = saved?.revisionId === item.revision_id ? saved.paragraphIndex : 0;
    app.player.listen(item, { fromParagraph });
  }
}

function currentEdition(kind) {
  const ids = [...new Set(app.pack.editions.filter(item => item.kind === kind).map(item => item.id))];
  const editions = ids.map(id => app.currentRevision(app.pack.editions, id, item => item.kind === kind)).filter(Boolean);
  const latest = editions.sort(app.compareRevision).at(-1);
  return latest ? app.personalized(latest) : null;
}

function stopCorrectedEditionAudio(id) {
  if (!id) return;
  app.invalidatedEditionAudio.add(id);
  for (const edition of app.pack?.editions || []) {
    if (edition.id === id && edition.audio) {
      edition.audio.state = 'superseded';
      edition.audio.superseded = true;
    }
  }
  const stopped = app.player.invalidate(id);
  const hadProgress = Boolean(app.state.progress[id]);
  const wasActive = app.state.activeAudio?.id === id;
  if (hadProgress || wasActive) app.state = app.invalidateAudioProgress(app.state, id);
  document.querySelectorAll('[data-audio-action]').forEach(button => {
    if (button.dataset.audioAction === id) button.remove();
  });
  if (stopped || wasActive) sessionStorage.setItem('briefing:correction-audio-notice', 'The prior recording is no longer available for this corrected edition.');
  app.syncListeningControls();
  app.refreshListenControls();
}

function invalidateMaterialCorrections() {
  for (const edition of app.pack?.editions || []) {
    if (app.hasMaterialCorrection(edition)) stopCorrectedEditionAudio(edition.id);
  }
}

// The same hook is used by app.content refreshes and correction updates arriving during playback.
window.addEventListener('briefing:material-correction', event => stopCorrectedEditionAudio(event.detail?.id));

function transcriptDestination(id, kind) {
  if (!app.transcriptReturnTo || app.transcriptReturnHash !== (location.hash || '#today')) return false;
  if (kind === 'deep-dive') return location.hash === `#explanation/${encodeURIComponent(id)}`;
  const base = `#edition/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
  return location.hash === base || location.hash.startsWith(`${base}/`);
}

function readerAlreadyShows(item) {
  const hash = location.hash || '#today';
  if (item.kind === 'deep-dive') {
    return hash === `#explanation/${encodeURIComponent(item.id)}` || hash === `#topic/${encodeURIComponent(item.topic_id)}`;
  }
  const editionBase = `#edition/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.id)}`;
  return hash === editionBase || hash.startsWith(`${editionBase}/`);
}

function paragraphAt(section, index) {
  return section?.querySelector(`[data-narration-index="${Number(index) || 0}"]`) || section?.querySelectorAll('p')[index];
}

function trackReadingPlace(id, revisionId, selector) {
  app.readingObserver?.disconnect();
  app.readingObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const section = entry.target.closest('.edition-section, .deep-dive-section');
      const paragraphIndex = entry.target.hasAttribute('data-narration-index') ? Number(entry.target.dataset.narrationIndex) : [...section.querySelectorAll('p')].indexOf(entry.target);
      if (app.player.status === 'playing' && app.player.item?.id === id) continue;
      const prior = app.state.readingProgress[id];
      if (prior?.revisionId === revisionId && prior.storyId === section.id && prior.paragraphIndex === paragraphIndex && prior.mode === 'read') continue;
      app.state.readingProgress[id] = { storyId: section.id, paragraphIndex: Math.max(0, paragraphIndex), mode: 'read', updatedAt: new Date().toISOString(), revisionId };
      app.state = app.saveState(app.state);
    }
  }, { rootMargin: '0px 0px -65% 0px', threshold: 0 });
  app.content.querySelectorAll(`${selector} p`).forEach(paragraph => app.readingObserver.observe(paragraph));
}

function currentTranscriptItem(selected) {
  if (!selected || !app.pack) return { available: false };
  const pool = selected.kind === 'deep-dive' ? app.pack.deepDives : app.pack.editions;
  const entry = app.currentRevision(pool, selected.id, item => selected.kind === 'deep-dive' || item.kind === selected.kind);
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
  return 'Read along';
}

function openTranscript(selected) {
  const resolved = currentTranscriptItem(selected);
  if (!resolved.available) return { available: false };
  const { current, corrected, selectedAudioMismatch } = resolved;
  if (readerAlreadyShows(current)) return { available: true, correctedItem: corrected || selectedAudioMismatch ? current : null, navigate: () => {
    const place = app.state.readingProgress[current.id];
    const story = app.content.querySelector(`#${CSS.escape(place?.storyId || '')}`);
    const paragraph = paragraphAt(story, place?.paragraphIndex || 0);
    paragraph?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
    paragraph?.classList.add('spoken-paragraph');
  } };
  app.transcriptReturnTo = { hash: location.hash || '#today', sectionId: null, scrollY: window.scrollY, focus: app.focusToken() };
  const saved = app.state.readingProgress[current.id];
  const validAnchor = !corrected && saved?.revisionId === current.revision_id && current.sections.some(section => section.id === saved.storyId);
  const correctionSection = corrected ? current.corrections?.find(item => item.severity === 'material')?.section_ids?.[0] : null;
  const placement = { id: current.id, sectionId: validAnchor ? saved.storyId : correctionSection || app.visibleSections(current)[0]?.id, paragraphIndex: validAnchor ? saved.paragraphIndex : 0, correction: corrected };
  const itemHash = current.kind === 'deep-dive'
    ? `#explanation/${encodeURIComponent(current.id)}`
    : `#edition/${encodeURIComponent(current.kind)}/${encodeURIComponent(current.id)}/${encodeURIComponent(placement.sectionId || '')}`;
  app.transcriptReturnHash = itemHash;
  return {
    available: true,
    correctedItem: corrected || selectedAudioMismatch ? current : null,
    navigate: () => app.navigateTranscript(itemHash, placement),
  };
}

function trackRouteSignals(routeHash, routeName) {
  if (app.lastSignalRoute === routeHash) return;
  app.lastSignalRoute = routeHash;
  const context = history.state?.routeContext || {};
  if (routeName.startsWith('source/')) {
    const sourceId = routeName.slice(7);
    const parent = context.parentHash || '';
    if (parent.startsWith('#edition/')) {
      const parts = parent.slice(1).split('/').map(decodeURIComponent);
      const edition = app.pack?.editions.find(item => item.id === parts[2] && item.kind === parts[1]);
      signalForItem('sourceOpen', edition, context.sectionId || parts[3]);
    } else if (parent.startsWith('#explanation/') || parent.startsWith('#topic/')) {
      const id = decodeURIComponent(parent.slice(parent.indexOf('/') + 1));
      const dive = app.pack?.deepDives.find(item => item.id === id || item.topic_id === id);
      signalForItem('sourceOpen', dive);
    } else {
      const edition = app.pack?.editions.find(item => item.sections?.some(section => section.source_ids?.includes(sourceId)));
      const section = edition?.sections.find(row => row.source_ids?.includes(sourceId));
      signalForItem('sourceOpen', edition, section?.id);
    }
  } else if (routeName.startsWith('explanation/') || routeName.startsWith('topic/')) {
    const id = decodeURIComponent(routeName.slice(routeName.indexOf('/') + 1));
    const dive = app.pack?.deepDives.find(item => item.id === id || item.topic_id === id);
    if (dive) {
      const storyId = context.parentHash?.startsWith('#edition/') && context.sectionId
        ? context.sectionId
        : context.sectionId && dive.sections?.some(section => section.id === context.sectionId)
          ? context.sectionId : dive.sections?.[0]?.id || dive.id;
      app.recordSignal('explainOpen', storyId, topicIdFor(dive));
    }
  }
}

let storyVisit = null;
let storyReadObserver = null;
function beginStoryVisit(item, sectionId, routeHash) {
  const section = item.sections?.find(row => row.id === sectionId) || item.sections?.[0];
  if (!section) return;
  if (storyVisit?.routeHash === routeHash) return;
  storyReadObserver?.disconnect(); storyReadObserver = null;
  storyVisit = { item, section, routeHash, pastFirstParagraph: false };
  const paragraphs = [...content.querySelectorAll('.edition-section p')];
  const second = paragraphs[1];
  if (second && 'IntersectionObserver' in window) {
    storyReadObserver = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.6)) storyVisit && (storyVisit.pastFirstParagraph = true);
    }, { threshold: 0.6 });
    storyReadObserver.observe(second);
  }
}
function finishStoryVisit(nextHash) {
  if (!storyVisit || storyVisit.routeHash === nextHash) return;
  if (!storyVisit.pastFirstParagraph) signalForItem('skip', storyVisit.item, storyVisit.section.id);
  storyReadObserver?.disconnect(); storyReadObserver = null; storyVisit = null;
}


  return {
    topicIdFor,
    signalForItem,
    resetBehaviorWeighting,
    signalCurrent,
    toggleSave,
    setFeedback,
    listenText,
    listenEstimate,
    listenEstimateWithUnit,
    browserCanSpeak,
    canListen,
    startListening,
    currentEdition,
    stopCorrectedEditionAudio,
    invalidateMaterialCorrections,
    transcriptDestination,
    readerAlreadyShows,
    paragraphAt,
    trackReadingPlace,
    currentTranscriptItem,
    transcriptLabel,
    openTranscript,
    trackRouteSignals,
    beginStoryVisit,
    finishStoryVisit,
  };
}
