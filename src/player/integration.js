import { createPlayer } from '../player.js';
import { createPauseIcon, createPlayIcon } from '../ui-icons.js';

export function syncTodayCompletion(content, player) {
  const ending = content.querySelector('[data-today-completion]');
  if (!ending) return;
  const item = player.item;
  const isComplete = item?.kind === 'daily'
    && item.id === ending.dataset.editionId
    && item.revision_id === ending.dataset.revisionId
    && ['complete', 'finished'].includes(player.status);
  ending.hidden = !isComplete;
}

export function createAppPlayer({
  root, content, node, getState, setState, saveState, getPack, getShowingCachedPack,
  openTranscript, transcriptLabel, signalForItem, hasMaterialCorrection,
  invalidatedEditionAudio, visibleSections, paragraphAt, revealStoryMore,
}) {
  let followAudio = true;
  let autoFollowingAudio = false;
  let followAudioItemId = null;
  const PLAY_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1.1 1.1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5Z"/></svg>';
  const PAUSE_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4.5" height="16" rx="1.2"/><rect x="13.5" y="4" width="4.5" height="16" rx="1.2"/></svg>';
const player = createPlayer(root, (id, seconds, revisionId, chapterId = null) => {
  getState().progress[id] = { seconds, revisionId, ...(chapterId ? { chapterId } : {}) };
  setState(saveState(getState()));
}, id => ({ ...(getState().progress[id] || {}), ...(getState().readingProgress[id] || {}) }), () => {
  const item = player.item;
  const selection = item ? { id: item.id, revisionId: item.revision_id } : null;
  if (!getShowingCachedPack() && (getState().activeAudio?.id !== selection?.id || getState().activeAudio?.revisionId !== selection?.revisionId)) {
    getState().activeAudio = selection;
    setState(saveState(getState()));
  }
  syncTodayCompletion(content, player);
  document.querySelectorAll('[data-audio-action]').forEach(button => {
    if (button.matches('[data-listen-control], [data-story-play]')) return;
    const active = button.dataset.audioAction === player.item?.id;
    button.textContent = active ? player.label : button.dataset.audioDefault;
  });
  syncListeningControls();
  document.body.classList.toggle('has-player', Boolean(player.item && player.available && player.status === 'playing'));
  refreshListenControls();
  syncAudioVerification();
}, openTranscript, transcriptLabel, (item, chapterId) => signalForItem('replay', item, chapterId));

function syncAudioVerification() {
  for (const button of content.querySelectorAll('[data-listen-control], [data-listen-item], [data-audio-action]')) {
    if (getShowingCachedPack()) {
      button.dataset.checkingAudio = 'true';
      button.disabled = true;
      button.setAttribute('aria-label', 'Checking audio for updates');
      const label = button.querySelector('.mode-label'); if (label) label.textContent = 'Checking audio';
    } else if (button.dataset.checkingAudio) {
      delete button.dataset.checkingAudio;
      if (!button.hasAttribute('data-listen-control')) button.disabled = false;
    }
  }
}

function listeningAvailable() {
  return Boolean(window.speechSynthesis && typeof window.speechSynthesis.speak === 'function' && window.SpeechSynthesisUtterance);
}
function formatElapsed(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
function listenControlLabel(edition) {
  if (hasMaterialCorrection(edition) || invalidatedEditionAudio.has(edition?.id)) return 'Audio paused';
  const same = player.item?.id === edition?.id && player.item?.revision_id === edition?.revision_id;
  if (same && player.status === 'playing') return 'Pause';
  if (same && player.status === 'complete') return 'Replay';
  if (same && player.status === 'error') return 'Retry';
  if (same && player.status === 'paused') return `Resume · ${formatElapsed(getState().progress[edition.id]?.seconds)} in`;
  const progress = getState().progress[edition?.id];
  if (getState().activeAudio?.id === edition?.id && progress?.revisionId === edition?.revision_id && progress.seconds > 0) {
    return `Resume · ${formatElapsed(progress.seconds)} in`;
  }
  return 'Listen';
}
function syncListeningControls() {
  if (!getPack()) return;
  for (const button of document.querySelectorAll('[data-listen-control]')) {
    const edition = getPack().editions.find(item => item.id === button.dataset.editionId && item.revision_id === button.dataset.revisionId);
    if (!edition || button.dataset.listenControl === 'edition') {
      if (edition && button.dataset.listenControl === 'edition') {
        const label = listenControlLabel(edition);
        const audioPaused = label === 'Audio paused';
        const same = player.item?.id === edition.id && player.item?.revision_id === edition.revision_id;
        const detail = audioPaused ? 'Audio paused'
          : same && ['playing', 'paused'].includes(player.status) ? `Resume ${formatElapsed(getState().progress[edition.id]?.seconds)}` : '';
        const labelNode = button.querySelector('.mode-label');
        const detailNode = button.querySelector('small');
        if (labelNode) labelNode.textContent = button.classList.contains('today-listen-pill') ? same && player.status === 'playing' ? 'Playing · tap to pause' : label === 'Listen' ? 'Play briefing' : label : 'Listen';
        if (detailNode) detailNode.textContent = detail;
        const icon = button.querySelector('.mode-icon');
        if (icon) icon.innerHTML = same && player.status === 'playing' ? PAUSE_ICON : PLAY_ICON;
        button.disabled = audioPaused;
        button.classList.toggle('is-disabled', audioPaused);
        button.setAttribute('aria-label', label === 'Listen' ? 'Play briefing' : `${label} today's briefing`);
      }
      continue;
    }
    const section = edition.sections.find(item => item.id === button.dataset.sectionId);
    const label = listenControlLabel(edition);
    const same = player.item?.id === edition.id && player.item?.revision_id === edition.revision_id;
    const isPlaying = same && player.status === 'playing' && (!player.item.start_section_id || player.item.start_section_id === section?.id);
    const text = isPlaying ? 'Pause' : label === 'Listen' ? button.classList.contains('weekly-listen') ? 'Listen' : 'Listen from here' : label;
    const labelNode = button.querySelector('.story-listen-label');
    const icon = button.querySelector('.story-listen-icon');
    if (labelNode) labelNode.textContent = text;
    if (icon) icon.innerHTML = isPlaying ? PAUSE_ICON : PLAY_ICON;
    const estimate = button.dataset.listenEstimate;
    const detailNode = button.querySelector('small');
    if (detailNode) detailNode.textContent = label === 'Audio paused' ? 'One story was corrected. The text is updated.' : isPlaying ? '· Listening now' : `· ${estimate}`;
    button.disabled = label === 'Audio paused';
    button.classList.toggle('is-disabled', button.disabled);
    button.setAttribute('aria-label', `${text}, ${isPlaying ? 'listening now' : estimate.replace(/ min$/, ' minutes')}`);
  }
  for (const button of document.querySelectorAll('[data-story-play]')) {
    const edition = getPack().editions.find(item => item.id === button.dataset.editionId && item.revision_id === button.dataset.revisionId);
    const section = edition?.sections.find(item => item.id === button.dataset.sectionId);
    if (!edition || !section) continue;
    const sameEdition = player.item?.id === edition.id && player.item?.revision_id === edition.revision_id;
    const activeSectionId = player.item?.start_section_id || getState().progress[edition.id]?.chapterId;
    const isPlaying = sameEdition && player.status === 'playing' && activeSectionId === section.id;
    const audioPaused = invalidatedEditionAudio.has(edition.id) || hasMaterialCorrection(edition);
    const shape = button.querySelector('.story-row-play-circle');
    if (shape) shape.innerHTML = isPlaying ? PAUSE_ICON : PLAY_ICON;
    button.disabled = Boolean(audioPaused);
    button.classList.toggle('is-disabled', Boolean(audioPaused));
    button.setAttribute('aria-pressed', String(isPlaying));
    button.setAttribute('aria-label', audioPaused ? 'Audio paused. One story was corrected. The text is updated.' : `${isPlaying ? 'Pause' : 'Listen to'} ${section.heading}, ${isPlaying ? 'playing' : button.dataset.listenEstimate.replace(/ min$/, ' minutes')}`);
    const stateNode = document.querySelector(`[data-audio-state-for="${CSS.escape(section.id)}"]`);
    if (stateNode) stateNode.textContent = isPlaying ? 'Playing' : '';
  }
}
function startEditionListening(edition) {
  edition = withNarrationTopics(readableEdition(edition));
  if (player.item?.id === edition.id && player.item?.revision_id === edition.revision_id) player.play(edition);
  else if (player.listen) player.listen(edition, { fromParagraph: 0 });
  else player.play(edition);
}
function startStoryListening(item, options = {}) {
  item = withNarrationTopics(readableEdition(item));
  if (player.item?.id === item.id && player.item?.revision_id === item.revision_id &&
      player.item?.start_section_id === item.start_section_id && player.status === 'playing') {
    const toggleItem = { ...item };
    delete toggleItem.start_section_id;
    player.play(toggleItem);
  } else if (player.listen) player.listen(item, options);
  else player.play(item);
}
function startRowListening(edition, sectionId) {
  const item = { ...withNarrationTopics(readableEdition(edition)), start_section_id: sectionId };
  const activeSectionId = player.item?.start_section_id || getState().progress[edition.id]?.chapterId;
  if (player.item?.id === edition.id && player.item?.revision_id === edition.revision_id && activeSectionId === sectionId) {
    player.play(edition);
  } else if (player.listen) player.listen(item, { fromParagraph: 0 });
  else player.play(item);
}
function withNarrationTopics(item) {
  if (!item || item.narrationTopics) return item;
  const ids = new Set((item.sections || []).flatMap(section => section.topic_ids || []));
  if (item.topic_id) ids.add(item.topic_id);
  return { ...item, storyCount: item.sections?.length || 0, narrationTopics: [...ids].map(id => getPack()?.topics.find(topic => topic.id === id)?.label).filter(Boolean) };
}
function readableEdition(item) {
  if (!item) return item;
  const sections = visibleSections(item).filter(section => section.kind !== 'link');
  return { ...item, sections, storyCount: sections.length };
}

player.onPositionChange(place => {
  if (!place.itemId || !place.storyId) return;
  if (followAudioItemId !== place.itemId) { followAudioItemId = place.itemId; followAudio = true; }
  getState().readingProgress[place.itemId] = { storyId: place.storyId, paragraphIndex: place.paragraphIndex, mode: place.mode, updatedAt: place.updatedAt, revisionId: place.revisionId };
  setState(saveState(getState()));
  // Today stays where the reader left it; Read along explicitly follows audio.
  if (place.mode !== 'listen' || (location.hash || '#today') === '#today') return;
  content.querySelectorAll('.spoken-paragraph').forEach(paragraph => paragraph.classList.remove('spoken-paragraph'));
  const section = content.querySelector(`#${CSS.escape(place.storyId)}`);
  const paragraph = paragraphAt(section, place.paragraphIndex);
  if (!paragraph) return;
  revealStoryMore(paragraph);
  paragraph.classList.add('spoken-paragraph');
  let chip = content.querySelector('.back-to-audio');
  if (!chip) {
    chip = node('button', 'Back to audio', 'back-to-audio');
    chip.addEventListener('click', () => { followAudio = true; chip.hidden = true; paragraph.scrollIntoView({ behavior: 'instant', block: 'center' }); });
    content.prepend(chip);
  }
  chip.hidden = followAudio;
  if (followAudio) {
    autoFollowingAudio = true;
    paragraph.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
    setTimeout(() => { autoFollowingAudio = false; }, 300);
  }
});
window.addEventListener('scroll', () => {
  if (autoFollowingAudio || player.status !== 'playing') return;
  followAudio = false;
  const chip = content.querySelector('.back-to-audio');
  if (chip) chip.hidden = false;
}, { passive: true });

// Interface · pass 010: keep Topics, Library, explanation and weekly Listen controls in sync with Player's player.
function refreshListenControls() {
  const current = player.item;
  for (const button of content.querySelectorAll('[data-listen-item]')) {
    const active = current?.id === button.dataset.listenItem && current?.revision_id === button.dataset.listenRevision;
    const playing = active && player.status === 'playing';
    const paused = active && player.status === 'paused';
    const title = button.dataset.listenTitle || 'this item';
    const estimate = button.dataset.listenEstimate || 'about 1';
    const status = playing ? 'Playing' : paused ? 'Paused' : '';
    button.setAttribute('aria-pressed', String(Boolean(playing)));
    button.setAttribute('aria-label', playing ? `Pause ${title}` : paused ? `Resume ${title}` : `Listen to ${title}, ${estimate} minutes`);
    const oldIcon = button.querySelector('svg.icon-play,svg.icon-pause');
    if (oldIcon) oldIcon.replaceWith(playing ? createPauseIcon(oldIcon.getAttribute('width') || 14) : createPlayIcon(oldIcon.getAttribute('width') || 14));
    const copy = button.querySelector('.listen-control-copy');
    if (copy) copy.textContent = `${playing ? 'Pause' : paused ? 'Resume' : 'Listen'} · ${estimate} min`;
    const statusText = button.parentElement?.querySelector('.topic-listen-status,.library-listen-status') || button.querySelector('.listen-control-status');
    if (statusText && statusText.textContent !== status) statusText.textContent = status;
  }
}

  return {
    player, syncAudioVerification, listeningAvailable, formatElapsed, listenControlLabel,
    syncListeningControls, startEditionListening, startStoryListening, startRowListening,
    withNarrationTopics, readableEdition, refreshListenControls,
  };
}
