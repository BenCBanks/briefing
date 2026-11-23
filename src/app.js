import { readEditionCache, writeEditionCache, clearEditionCache } from './edition/cache.js';
import { renderEdition, renderToday, renderWeeklyRecap, editionDateRange, visibleSections, hasMaterialCorrection } from './edition/edition.js';
// Reed · generated editions: only corrections tied to the current revision affect playback and Corrected labels.
import { renderTopics, renderDeepDive } from './topics/topics.js';
import { renderTopicDetail } from './topics/topic-detail.js';
// Mica · episode pages and the API-backed request flow.
import { renderEpisodeDeck, renderEpisodePage, hasCurrentEpisodeRecording } from './episodes/episodes.js';
import { loadState, saveState, resetState, invalidateAudioProgress } from './storage.js';
import { createPlayer, NARRATOR_KEY } from './player.js';
import { audioScriptText } from './audio-script.js';
import { createPauseIcon, createPlayIcon } from './ui-icons.js';
import { interpretProfile } from './interests/provider.js';
import { defaultProfile } from './interests/profile.js';
import { deriveInterestBrief } from './interests/brief.js';
import { behaviorWeights, clear as clearSignals, events as signalEvents, record as recordSignal } from './interests/signals.js';
import { applySuggestion, clearSuggestionSnoozes, generateSuggestions, snoozeSuggestion } from './interests/suggest.js';
import { renderInterests } from './interests/interests.js';
import { interpret, validateProfile } from './interests/interpret.js';
import { clearProfile, getProfile, saveProfile, syncProfile } from './interests/store.js';
import { renderCorrections } from './corrections/corrections.js';
import { renderTopicsScreen } from './screens/topics.js';
import { renderOnDemandScreen, episodeTopicSuggestions } from './screens/on-demand.js';
import { renderSettingsScreen } from './screens/settings.js';
import { renderTodayScreen } from './screens/today.js';
import { renderEarlierScreen } from './screens/earlier.js';

const app = document.querySelector('#app');
const content = document.querySelector('#content');
const nav = [...document.querySelectorAll('[data-nav]')];
const editableControlSelector = 'textarea, select, [contenteditable="true"], input:not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="checkbox"]):not([type="radio"])';
function syncVirtualKeyboard() {
  const viewport = window.visualViewport;
  const active = document.activeElement;
  const editing = active instanceof HTMLElement && active.matches(editableControlSelector);
  const inset = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
  const open = editing && inset > 120;
  document.body.classList.toggle('keyboard-open', open);
  document.documentElement.style.setProperty('--virtual-keyboard-inset', `${open ? inset : 0}px`);
}
document.addEventListener('focusin', syncVirtualKeyboard);
document.addEventListener('focusout', () => window.setTimeout(syncVirtualKeyboard, 0));
window.addEventListener('resize', syncVirtualKeyboard);
window.visualViewport?.addEventListener('resize', syncVirtualKeyboard);
window.visualViewport?.addEventListener('scroll', syncVirtualKeyboard);
let episodeFixturePromise = null;
let episodeDeckCleanup = null;
const invalidatedEditionAudio = new Set();
const seenEditionMarksInFlight = new Set();
let pack = null;
let showingCachedPack = false;
let editionCheckSucceeded = true;
let cachedPackIdentity = null;
let lastEditionCheckAt = 0;
let editionRefreshInFlight = false;
let slowNetworkTimer = null;
let networkBanner = null;
let editionJustUpdatedAt = 0;
let editionHistoryLoaded = false;
let editionHistoryPromise = null;
let state = loadState();
let interestProfile = getProfile();
let interestDraft = null;
let interestPageFlags = [];
let interestPageNotice = '';
let interestInterpretController = null;
let interestInterpretBusy = false;
let interestInterpretError = null;
let interestInterpretStatus = '';
let buildAttempt = 0;
let buildStartedAt = 0;
let buildTimeoutHandle = null;
let buildInProgress = false;
let buildNotice = sessionStorage.getItem('briefing:build-error') || '';
const BUILD_GENERAL_FALLBACK_KEY = 'briefing:v1/general-fallback-profile-key';
const BUILD_GENERAL_FALLBACK_NOTE = 'Built from the shared topics while your selection updates';
const FIRST_EDITION_WELCOME_PENDING_KEY = 'briefing:first-edition-welcome-pending';
const FIRST_EDITION_WELCOME_KEY = 'briefing:first-edition-welcome';
const FIRST_RUN_BUILD_KEY = 'briefing:v1:first-run-build';
const DAILY_TIME_KEY = 'briefing:v1:daily-time';
const TOPICS_PENDING_KEY = 'briefing:v1:topics-pending';
const TOPICS_DRAFT_KEY = 'briefing:topics-draft';
function readFirstRunBuild() {
  try {
    const pending = JSON.parse(localStorage.getItem(FIRST_RUN_BUILD_KEY));
    return pending && pending.version === 1 && typeof pending.profileKey === 'string'
      ? { version: 1, profileKey: pending.profileKey, jobId: typeof pending.jobId === 'string' ? pending.jobId : null }
      : null;
  } catch { return null; }
}
function writeFirstRunBuild(profile, jobId = null) {
  const pending = { version: 1, profileKey: profileBuildKey(profile), jobId: jobId ? String(jobId) : null };
  firstRunPending = pending;
  try { localStorage.setItem(FIRST_RUN_BUILD_KEY, JSON.stringify(pending)); } catch { /* Keep the current build usable in memory. */ }
}
function clearFirstRunBuild() {
  firstRunPending = null;
  try { localStorage.removeItem(FIRST_RUN_BUILD_KEY); } catch { /* The server job may still finish for this visit. */ }
}
function dailyTimeValue() {
  try {
    const value = localStorage.getItem(DAILY_TIME_KEY);
    return /^([01]\d|2[0-3]):[0-5]\d$/.test(value || '') ? value : '05:00';
  } catch { return '05:00'; }
}
function formatEasternTime(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  const hour = Number(match?.[1] ?? 5);
  const minute = Number(match?.[2] ?? 0);
  const hour12 = hour % 12 || 12;
  return `${hour12}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'am' : 'pm'} ET`;
}
function nextDailyRunTime(value, now = new Date()) {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  const runHour = Number(match?.[1] ?? 5);
  const runMinute = Number(match?.[2] ?? 0);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(now);
  const get = type => Number(parts.find(part => part.type === type)?.value);
  const year = get('year'); const month = get('month'); const day = get('day');
  const isAfterRun = get('hour') * 60 + get('minute') >= runHour * 60 + runMinute;
  const date = new Date(Date.UTC(year, month - 1, day + Number(isAfterRun), 12));
  const dateLabel = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', weekday: 'long', month: 'long', day: 'numeric',
  }).format(date);
  return `${dateLabel} at ${formatEasternTime(`${String(runHour).padStart(2, '0')}:${String(runMinute).padStart(2, '0')}`)}`;
}
let firstRunPending = readFirstRunBuild();
let firstRunRecoveryAvailable = false;
if (firstRunPending && (!interestProfile || firstRunPending.profileKey !== profileBuildKey(interestProfile))) clearFirstRunBuild();
if (firstRunPending) { buildInProgress = true; interestDraft = { profile: interestProfile }; firstRunRecoveryAvailable = true; }
const EPISODE_STAGE_FIXTURES = [
  { title: 'Finding sources', detail: 'Searching for useful, relevant sources.' },
  { title: 'Reading', detail: 'Reading sources and checking claims against them.' },
  { title: 'Writing', detail: 'Turning checked material into chapters.' },
  { title: 'Recording', detail: 'Preparing the narrated episode.' },
  { title: 'Ready', detail: 'Your episode is ready to play.' },
];
const EPISODE_LONG_WAIT_MS = 2 * 60 * 1000;
const EPISODE_MODES = [
  { id: 'brief', label: 'Brief', detail: 'The key points, quickly.' },
  { id: 'deep-dive', label: 'Deep dive', detail: 'More context and connections.' },
  { id: 'simple', label: 'Explain it simply', detail: 'Plain language, step by step.' },
];
const EPISODE_LENGTH_KEY = 'briefing:v1:last-episode-length';
function readLastEpisodeLength() {
  try {
    const value = Number(localStorage.getItem(EPISODE_LENGTH_KEY));
    return [3, 5, 10, 15].includes(value) ? value : 5;
  } catch { return 5; }
}
function rememberEpisodeLength(value) {
  episodeDraft.minutes = value;
  try { localStorage.setItem(EPISODE_LENGTH_KEY, String(value)); } catch { /* Keep the choice for this visit. */ }
}
let episodeDraft = { prompt: '', minutes: readLastEpisodeLength() };
let episodeJob = null;
let episodeJobTimer = null;
let episodeRequestError = '';
let episodeRequestBusy = false;
let episodeRequestBlocked = false;
let episodeHistoryFilter = 'all';
let episodeHistorySearch = '';
let episodeHistorySnapshot = null;
let episodeHistoryUndo = null;
let episodeHistoryUndoTimer = null;
let toastTimer = null;
let activeToast = null;
let topicsUpdating = false;
let topicsProgress = '';
let topicsError = '';
let topicsDraft = sessionStorage.getItem(TOPICS_DRAFT_KEY);
function pendingTopicsUpdate() {
  try { return JSON.parse(localStorage.getItem(TOPICS_PENDING_KEY)); } catch { return null; }
}
let buildQuestion = '';
let removedBuildTopic = null;
let returnTo = { hash: '#today', sectionId: null, scrollY: 0 };
let deepDiveReturnTo = null;
let pendingScrollRestore = null;
let pendingSourceFocusID = null;
let pendingTranscriptPlacement = null;
let transcriptReturnTo = null;
let transcriptReturnHash = null;
let pendingPlayerReturnFocus = false;
let followAudio = true;
let autoFollowingAudio = false;
let followAudioItemId = null;
let pendingReturnHeadingFocus = false;
let readingObserver = null;
let pendingRouteContext = null;
let lastSignalRoute = null;
const rootScroll = new Map();
let libraryFilter = ['saved', 'episodes'].includes(sessionStorage.getItem('briefing:library-filter')) ? sessionStorage.getItem('briefing:library-filter') : 'editions';
let librarySearchQuery = '';
const EDITION_MODE_KEY = 'briefing:v1:edition-mode';
const PLAY_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5Z"/></svg>';
const PAUSE_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4.5" height="16" rx="1.2"/><rect x="13.5" y="4" width="4.5" height="16" rx="1.2"/></svg>';

function getEditionMode(id) {
  try {
    const modes = JSON.parse(localStorage.getItem(EDITION_MODE_KEY));
    return modes?.[id] === 'listen' ? 'listen' : modes?.[id] === 'read' ? 'read' : null;
  } catch { return null; }
}
function rememberEditionMode(id, mode) {
  try {
    const modes = JSON.parse(localStorage.getItem(EDITION_MODE_KEY)) || {};
    modes[id] = mode;
    for (const key of Object.keys(modes).slice(0, -100)) delete modes[key];
    localStorage.setItem(EDITION_MODE_KEY, JSON.stringify(modes));
  } catch { /* The current view still works if local storage is unavailable. */ }
}

const player = createPlayer(document.querySelector('#player-root'), (id, seconds, revisionId, chapterId = null) => {
  state.progress[id] = { seconds, revisionId, ...(chapterId ? { chapterId } : {}) };
  state = saveState(state);
}, id => ({ ...(state.progress[id] || {}), ...(state.readingProgress[id] || {}) }), () => {
  const item = player.item;
  const selection = item ? { id: item.id, revisionId: item.revision_id } : null;
  if (!showingCachedPack && (state.activeAudio?.id !== selection?.id || state.activeAudio?.revisionId !== selection?.revisionId)) {
    state.activeAudio = selection;
    state = saveState(state);
  }
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
    if (showingCachedPack) {
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
  if (same && player.status === 'paused') return `Resume · ${formatElapsed(state.progress[edition.id]?.seconds)} in`;
  const progress = state.progress[edition?.id];
  if (state.activeAudio?.id === edition?.id && progress?.revisionId === edition?.revision_id && progress.seconds > 0) {
    return `Resume · ${formatElapsed(progress.seconds)} in`;
  }
  return 'Listen';
}
function syncListeningControls() {
  if (!pack) return;
  for (const button of document.querySelectorAll('[data-listen-control]')) {
    const edition = pack.editions.find(item => item.id === button.dataset.editionId && item.revision_id === button.dataset.revisionId);
    if (!edition || button.dataset.listenControl === 'edition') {
      if (edition && button.dataset.listenControl === 'edition') {
        const label = listenControlLabel(edition);
        const spokenEstimate = button.dataset.listenEstimate?.replace(/^(\d+)\s+min$/, (_match, minutes) =>
          `${minutes} ${minutes === '1' ? 'minute' : 'minutes'}`) || '';
        const audioPaused = label === 'Audio paused';
        const same = player.item?.id === edition.id && player.item?.revision_id === edition.revision_id;
        const detail = audioPaused ? 'Audio paused'
          : same && ['playing', 'paused'].includes(player.status) ? `Resume ${formatElapsed(state.progress[edition.id]?.seconds)}` : button.dataset.listenEstimate;
        const labelNode = button.querySelector('.mode-label');
        const detailNode = button.querySelector('small');
        if (labelNode) labelNode.textContent = button.classList.contains('today-listen-pill') ? same && player.status === 'playing' ? 'Playing · tap to pause' : label === 'Listen' ? `Play${button.dataset.durationLabel || ''}` : label : 'Listen';
        if (detailNode) detailNode.textContent = detail;
        const icon = button.querySelector('.mode-icon');
        if (icon) icon.innerHTML = same && player.status === 'playing' ? PAUSE_ICON : PLAY_ICON;
        button.disabled = audioPaused;
        button.classList.toggle('is-disabled', audioPaused);
        button.setAttribute('aria-label', label === 'Listen' ? `Play today's briefing${spokenEstimate ? `, about ${spokenEstimate}` : ''}` : `${label} today's briefing`);
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
    const edition = pack.editions.find(item => item.id === button.dataset.editionId && item.revision_id === button.dataset.revisionId);
    const section = edition?.sections.find(item => item.id === button.dataset.sectionId);
    if (!edition || !section) continue;
    const sameEdition = player.item?.id === edition.id && player.item?.revision_id === edition.revision_id;
    const activeSectionId = player.item?.start_section_id || state.progress[edition.id]?.chapterId;
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
  const activeSectionId = player.item?.start_section_id || state.progress[edition.id]?.chapterId;
  if (player.item?.id === edition.id && player.item?.revision_id === edition.revision_id && activeSectionId === sectionId) {
    player.play(edition);
  } else if (player.listen) player.listen(item, { fromParagraph: 0 });
  else player.play(item);
}
function withNarrationTopics(item) {
  if (!item || item.narrationTopics) return item;
  const ids = new Set((item.sections || []).flatMap(section => section.topic_ids || []));
  if (item.topic_id) ids.add(item.topic_id);
  return { ...item, storyCount: item.sections?.length || 0, narrationTopics: [...ids].map(id => pack?.topics.find(topic => topic.id === id)?.label).filter(Boolean) };
}
function readableEdition(item) {
  if (!item) return item;
  const sections = visibleSections(item).filter(section => section.kind !== 'link');
  return { ...item, sections, storyCount: sections.length };
}

player.onPositionChange(place => {
  if (!place.itemId || !place.storyId) return;
  if (followAudioItemId !== place.itemId) { followAudioItemId = place.itemId; followAudio = true; }
  state.readingProgress[place.itemId] = { storyId: place.storyId, paragraphIndex: place.paragraphIndex, mode: place.mode, updatedAt: place.updatedAt, revisionId: place.revisionId };
  state = saveState(state);
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

// Mica · pass 010: keep Topics, Library, explanation and weekly Listen controls in sync with Tern's player.
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

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function dismissToast() {
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = null;
  activeToast?.remove();
  activeToast = null;
}

function showToast(message, { actionLabel = '', onAction = null } = {}) {
  dismissToast();
  const toast = node('div', null, 'app-toast');
  toast.setAttribute('role', 'status');
  toast.setAttribute('aria-live', 'polite');
  toast.append(node('span', message, 'app-toast-message'));
  if (actionLabel && typeof onAction === 'function') {
    const action = node('button', actionLabel, 'app-toast-action'); action.type = 'button';
    action.addEventListener('click', () => { onAction(); dismissToast(); });
    toast.append(action);
  }
  const dismiss = node('button', 'Dismiss', 'app-toast-dismiss'); dismiss.type = 'button';
  dismiss.setAttribute('aria-label', 'Dismiss notice'); dismiss.addEventListener('click', dismissToast);
  toast.append(dismiss);
  document.body.append(toast);
  activeToast = toast;
  toastTimer = setTimeout(dismissToast, 4000);
  return toast;
}

function link(text, href, className) {
  const el = node('a', text, className);
  el.href = href;
  return el;
}

function clearNetworkBanner() {
  const scrollY = window.scrollY;
  networkBanner?.remove();
  networkBanner = null;
  if (window.scrollY !== scrollY) window.scrollTo({ top: scrollY, behavior: 'instant' });
}

function showNetworkBanner(kind, hasSavedBriefing = Boolean(pack || showingCachedPack)) {
  const copy = {
    offline: hasSavedBriefing ? 'You’re offline. Your saved briefing is still here.' : 'You’re offline. Connect to load your briefing.',
    slow: hasSavedBriefing ? 'This is taking longer than usual. Your saved briefing is still here.' : 'This is taking longer than usual. You can retry when ready.',
    failed: hasSavedBriefing ? 'Updates could not be checked. Your saved briefing is still here.' : 'Your briefing could not be loaded just now. You can try again.',
    checking: 'Connection restored. Checking for updates.',
  }[kind] || 'Your connection needs attention. You can try again.';
  if (!networkBanner?.isConnected) {
    networkBanner = node('aside', null, 'network-banner cache-notice');
    networkBanner.setAttribute('role', 'status');
    networkBanner.setAttribute('aria-live', 'polite');
    const message = node('p', copy, 'network-banner-copy');
    const retry = node('button', hasSavedBriefing ? 'Retry updates' : 'Retry', 'network-banner-retry'); retry.type = 'button';
    retry.addEventListener('click', () => {
      if (editionRefreshInFlight) return;
      retry.disabled = true; retry.textContent = 'Checking…';
      void refreshInitialPack().finally(() => {
        if (retry.isConnected) { retry.disabled = false; retry.textContent = hasSavedBriefing ? 'Retry updates' : 'Retry'; }
      });
    });
    networkBanner.append(message, retry);
    app.insertBefore(networkBanner, content);
  } else {
    networkBanner.querySelector('.network-banner-copy').textContent = copy;
  }
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
  const response = await fetch(`/content/${name}.json`, { cache: 'no-cache' });
  if (!response.ok) throw Error(`${name} could not be loaded (${response.status}).`);
  return response.json();
}

async function readGeneratedEdition() {
  editionCheckSucceeded = false;
  try {
    const response = await fetch('/api/edition?profile=active&history=0', { cache: 'no-store' });
    // A 404 is a successful check that this profile has no generated edition.
    // Keep cached sample content usable and don't strand player controls in
    // "Checking audio" while the static edition is still available.
    if (response.status === 404) { editionCheckSucceeded = true; return null; }
    if (!response.ok) return null;
    const result = await response.json();
    editionCheckSucceeded = true;
    return (result?.generated === true && result.edition?.generated === true) || Array.isArray(result?.history)
      ? result : null;
  } catch {
    return null;
  }
}

async function loadEditionHistory(statusNode = null) {
  if (editionHistoryLoaded || !interestProfile || !pack) return;
  if (editionHistoryPromise) return editionHistoryPromise;
  editionHistoryPromise = (async () => {
    try {
      const response = await fetch('/api/edition?profile=active&history=1', { cache: 'no-store' });
      if (!response.ok) throw Error(`history request failed (${response.status})`);
      const result = await response.json();
      const known = new Set(pack.editions.map(item => `${item.id}:${item.revision_id}`));
      const bundles = Array.isArray(result.history) ? result.history : [];
      const historyEditions = bundles.map(bundle => bundle?.edition)
        .filter(edition => edition && typeof edition === 'object' && !known.has(`${edition.id}:${edition.revision_id}`));
      const historySources = bundles.flatMap(bundle => Array.isArray(bundle?.sources) ? bundle.sources : []);
      const sources = [...new Map([...pack.sources, ...(Array.isArray(result.sources) ? result.sources : []), ...historySources]
        .filter(source => source?.id).map(source => [source.id, source])).values()];
      pack = validatePack({ ...pack, sources, editions: [...pack.editions, ...historyEditions] });
      editionHistoryLoaded = true;
      if (location.hash === '#library' || location.hash.startsWith('#topic-detail/')) route();
    } catch {
      if (statusNode?.isConnected) {
        statusNode.replaceChildren(document.createTextNode('Earlier briefings could not be loaded. '));
        const retry = node('button', 'Retry'); retry.type = 'button';
        retry.addEventListener('click', () => { editionHistoryPromise = null; statusNode.textContent = 'Loading earlier briefings…'; void loadEditionHistory(statusNode); });
        statusNode.append(retry);
      }
    } finally {
      editionHistoryPromise = null;
    }
  })();
  return editionHistoryPromise;
}

async function loadPack(includeLatestGenerated = false) {
  editionCheckSucceeded = true;
  const mayReadGenerated = includeLatestGenerated || (interestProfile && localStorage.getItem('briefing:v1:built-profile-key') === profileBuildKey(interestProfile));
  const [topicFile, diveFile, sourceFile, editionFile, generated] = await Promise.all([
    readJson('topics'), readJson('deep-dives'), readJson('sources'), readJson('editions'), mayReadGenerated ? readGeneratedEdition() : Promise.resolve(null),
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
  }
  catch (error) {
    if (!generatedBundles.length && !generatedSources.length) throw error;
    // A malformed server edition must not take down the last-good static pack.
    editionCheckSucceeded = false;
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
      // Reed · generated editions can contain profile-defined topic IDs beyond the sample catalog.
      const validGeneratedIds = coverageIds.every(id => typeof id === 'string' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id));
      const sectionTopicIds = new Set(edition.sections.flatMap(section => section.topic_ids || []));
      if (!uniqueCoverage || !validGeneratedIds ||
          [...sectionTopicIds].some(id => !coverageIds.includes(id))) {
        throw Error(`The ${edition.id} coverage is inconsistent.`);
      }
    } else if (coverageIds.length !== topics.length || !uniqueCoverage || coverageIds.some(id => !topicIds.has(id))) {
      throw Error(`The ${edition.id} coverage is inconsistent.`);
    }
  }
  if (!editions.some(x => x.kind === 'daily') || !editions.some(x => x.kind === 'weekly')) throw Error('The daily or weekly sample is unavailable.');
  return { topics, deepDives, sources, editions };
}

function personalized(edition) {
  if (!edition || (!invalidatedEditionAudio.has(edition.id) &&
      !hasMaterialCorrection(edition))) return edition;
  return { ...edition, audio: { ...(edition.audio || {}), state: 'superseded', superseded: true } };
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
  if (['#library', '#earlier', '#episodes'].includes(hash)) return 'library';
  if (['#ask', '#ask-episode'].includes(hash)) return 'today';
  if (context?.root && ['today', 'topics', 'library'].includes(context.root)) return context.root;
  if (['#weekly'].includes(hash) || hash.startsWith('#edition/')) return 'today';
  if (hash.startsWith('#topic/') || hash.startsWith('#explanation/')) return 'topics';
  if (hash.startsWith('#topic-detail/')) return 'topics';
  return 'today';
}
function navigate(hash, context = null) {
  const currentHash = location.hash || '#today';
  if (['#today', '#topics', '#ask', '#library', '#earlier'].includes(currentHash)) rootScroll.set(currentHash, window.scrollY);
  if (currentHash !== hash && ['#today', '#topics', '#ask', '#library', '#earlier'].includes(hash)) pendingScrollRestore = rootScroll.get(hash) ?? 0;
  const currentState = history.state || {};
  historyViews.delete(currentState.viewKey);
  history.replaceState({ ...currentState, viewState: { scrollY: window.scrollY, focus: context?.returnFocus || focusToken() || context?.parentFocus || null } }, '', location.href);
  pendingRouteContext = context || { root: rootForHash(currentHash), parentHash: currentHash, parentScroll: window.scrollY, parentFocus: focusToken() };
  if (context?.returnFocus) {
    const focusReturn = () => {
      const apply = () => {
        if (location.hash !== hash) return;
        const target = document.querySelector(`[data-nav="${CSS.escape(context.returnFocus.id)}"]`);
        if (target && target.getClientRects().length) target.focus({ preventScroll: true });
      };
      setTimeout(() => requestAnimationFrame(apply), 80);
    };
    if (currentHash === hash) focusReturn();
    else window.addEventListener('hashchange', focusReturn, { once: true });
  }
  if (hash.startsWith('#interests/')) {
    pendingRouteContext.interestReturnHash ||= history.state?.routeContext?.interestReturnHash || (currentHash === '#preferences' ? '#preferences' : '#today');
  }
  if (currentHash === hash) route(); else location.hash = hash;
}
function navigateTranscript(hash, placement) {
  pendingPlayerReturnFocus = false;
  pendingTranscriptPlacement = placement;
  if (location.hash === hash) route();
  else {
    historyViews.set(history.state?.viewKey, { scrollY: transcriptReturnTo.scrollY, focus: transcriptReturnTo.focus });
    history.pushState({ ...(history.state || {}), viewKey: null, viewState: { scrollY: 0, focus: null }, transcriptOrigin: { ...transcriptReturnTo }, transcriptHash: hash,
      routeContext: { root: rootForHash(transcriptReturnTo.hash), parentHash: transcriptReturnTo.hash, parentScroll: transcriptReturnTo.scrollY, parentFocus: transcriptReturnTo.focus, sectionId: placement.sectionId, transcript: true } }, '', hash);
    route();
  }
}
function openSource(id) {
  const parent = remember(document.activeElement?.closest('.edition-section')?.id || null);
  parent.focus = { type: 'source', id }; parent.sourceID = id;
  // Reed · preserve the source page's actual immediate parent for its Back label.
  navigate(`#source/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash, parentLabel: immediateParentLabel(parent.hash, history.state?.routeContext || {}), parentScroll: parent.scrollY, parentFocus: parent.focus, sectionId: parent.sectionId, parentType: parent.hash.startsWith('#explanation/') || parent.hash.startsWith('#topic/') || parent.hash.startsWith('#topic-detail/') ? 'explanation' : 'briefing' });
}
function openDeepDive(id, sectionId = null) {
  const parent = remember(sectionId);
  parent.focus ||= { type: 'topic', id };
  deepDiveReturnTo = returnTo;
  navigate(`#topic/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash, parentScroll: parent.scrollY, parentFocus: parent.focus, sectionId });
}

function showWhySheet(section, edition) {
  const topicId = section.topic_ids?.[0];
  const topic = pack.topics.find(item => item.id === topicId);
  const matched = interestProfile?.topics.find(item => item.id === topicId);
  const dialog = node('dialog', null, 'why-story-dialog');
  const sheet = node('section', null, 'why-story-sheet');
  const close = node('button', 'Close', 'why-story-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close Why this story');
  close.addEventListener('click', () => dialog.close());
  const heading = node('h2', 'Why this story?');
  heading.id = 'why-story-heading';
  dialog.setAttribute('aria-labelledby', heading.id);
  sheet.append(close, node('p', 'WHY THIS STORY', 'eyebrow'), heading);
  sheet.append(node('p', matched ? `You follow ${matched.label.toLowerCase()} because you wrote “${matched.sourcePhrase}”.` : `This story is filed under ${topic?.label || 'your interests'}.`));
  sheet.append(node('p', topic?.scope || '', 'why-story-scope'));
  const actionsRow = node('div', null, 'why-story-actions');
  const more = node('button', 'More like this'); more.type = 'button';
  more.addEventListener('click', () => { if (topicId) recordSignal('more', section.id, topicId); dialog.close(); });
  const less = node('button', 'Less like this'); less.type = 'button';
  less.addEventListener('click', () => { if (topicId) recordSignal('less', section.id, topicId); dialog.close(); });
  const mute = node('button', 'Not interested in this topic'); mute.type = 'button';
  const confirm = node('div', null, 'why-story-confirm'); confirm.hidden = true;
  confirm.append(node('p', `Mute ${topic?.label || 'this topic'}? It will be removed from your interests and stay muted until you add it back.`));
  const confirmMute = node('button', 'Confirm mute', 'primary'); confirmMute.type = 'button';
  confirmMute.addEventListener('click', () => {
    if (topicId && topic) {
      recordSignal('mute', section.id, topicId);
      if (!interestProfile.muted.some(item => item.label === topic.label)) interestProfile.muted.push({ label: topic.label });
      interestProfile.topics = interestProfile.topics.filter(item => item.id !== topicId);
      interestProfile = saveProfile(interestProfile);
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
    }
    dialog.close();
  });
  const cancelMute = node('button', 'Keep this topic'); cancelMute.type = 'button'; cancelMute.addEventListener('click', () => { confirm.hidden = true; actionsRow.hidden = false; });
  confirm.append(confirmMute, cancelMute);
  mute.addEventListener('click', () => { confirm.hidden = false; actionsRow.hidden = true; confirmMute.focus(); });
  const edit = node('button', 'Edit my interests'); edit.type = 'button';
  edit.addEventListener('click', () => {
    dialog.close();
    const current = location.hash || '#today';
    navigate('#interests', { root: rootForHash(current), parentHash: current, parentScroll: window.scrollY, parentFocus: focusToken() });
  });
  actionsRow.append(more, less, mute); sheet.append(actionsRow, confirm, edit); dialog.append(sheet);
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }, { once: true });
  document.body.append(dialog); dialog.addEventListener('close', () => dialog.remove(), { once: true });
  dialog.showModal();
}

function showStoryActionsSheet(section, edition, trigger) {
  const dialog = node('dialog', null, 'story-actions-dialog');
  const sheet = node('section', null, 'story-actions-sheet');
  const close = node('button', 'Close', 'story-actions-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close story actions');
  close.addEventListener('click', () => dialog.close());
  const heading = node('h2', 'Share this story'); heading.id = 'story-actions-heading';
  dialog.setAttribute('aria-labelledby', heading.id);
  sheet.append(close, node('p', 'STORY', 'eyebrow'), heading, node('p', section.heading, 'story-actions-title'));

  const status = node('p', '', 'story-actions-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const storyUrl = location.href;
  const copyText = async text => {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
    const field = node('textarea'); field.value = text; field.setAttribute('readonly', '');
    field.style.position = 'fixed'; field.style.opacity = '0'; document.body.append(field); field.select();
    const copied = document.execCommand?.('copy'); field.remove();
    if (!copied) throw new Error('clipboard_unavailable');
  };
  const share = node('button', 'Share story', 'primary story-share-action'); share.type = 'button';
  share.addEventListener('click', async () => {
    if (typeof navigator.share === 'function') {
      try { await navigator.share({ title: section.heading, text: 'A story from my daily briefing.', url: storyUrl }); showToast('Share options opened.'); }
      catch (error) { if (error?.name !== 'AbortError') status.textContent = 'Sharing is unavailable right now.'; }
      return;
    }
    try { await copyText(storyUrl); showToast('Sharing is unavailable. Story link copied instead.'); }
    catch { status.textContent = 'Sharing and copying are unavailable in this browser.'; }
  });
  const copyLink = node('button', 'Copy link', 'story-copy-link'); copyLink.type = 'button';
  copyLink.addEventListener('click', async () => {
    try { await copyText(storyUrl); showToast('Story link copied.'); }
    catch { status.textContent = 'Copy is unavailable. You can copy the page address from your browser.'; }
  });
  const shareActions = node('div', null, 'story-share-actions'); shareActions.append(share, copyLink); sheet.append(shareActions, status);

  const report = node('section', null, 'story-report-form');
  report.append(node('h3', 'Report a mistake'));
  report.append(node('p', 'What should we check? Nothing is sent automatically; copy the details into your message to the editor.', 'story-report-note'));
  const label = node('label', 'What looks wrong?', 'story-report-label'); label.htmlFor = 'story-report-note';
  const note = node('textarea'); note.id = 'story-report-note'; note.name = 'report'; note.rows = 3; note.maxLength = 1200;
  note.placeholder = 'A date, name, number, or other detail to check';
  const copyReport = node('button', 'Copy report details', 'story-copy-report'); copyReport.type = 'button';
  copyReport.addEventListener('click', async () => {
    if (!note.value.trim()) { status.textContent = 'Add a short note about what to check first.'; note.focus(); return; }
    const reportText = `Story: ${section.heading}\nLink: ${storyUrl}\n\nWhat to check:\n${note.value.trim()}`;
    try { await copyText(reportText); showToast('Report details copied. Paste them into your message to the editor.'); }
    catch { status.textContent = 'Copy is unavailable. Your note is still here; you can select and copy it.'; }
  });
  report.append(label, note, copyReport); sheet.append(report); dialog.append(sheet);
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  document.body.append(dialog);
  dialog.addEventListener('close', () => { dialog.remove(); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); }, { once: true });
  dialog.showModal();
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
function topicIdFor(item, sectionId = null) {
  if (!item) return 'unknown';
  if (item.topic_id) return item.topic_id;
  const section = (item.sections || []).find(row => row.id === sectionId) || item.sections?.[0];
  return section?.topic_ids?.[0] || section?.topic_id || item.coverage?.[0]?.topic_id || 'unknown';
}
function signalForItem(type, item, sectionId = null) {
  if (!item) return false;
  const section = (item.sections || []).find(row => row.id === sectionId) || item.sections?.[0];
  const recorded = recordSignal(type, section?.id || item.id, topicIdFor(item, sectionId));
  if (recorded && interestProfile?.topics?.length) {
    const topicIds = interestProfile.topics.map(topic => topic.id);
    interestProfile = saveProfile({ ...interestProfile, behavior_weights: behaviorWeights(topicIds) });
  }
  return recorded;
}

function resetBehaviorWeighting() {
  clearSignals();
  if (interestProfile) {
    interestProfile = saveProfile({ ...interestProfile, behavior_weights: {} });
  }
}
function signalCurrent(type, storyId = null) {
  const hash = location.hash || '#today';
  if (hash.startsWith('#edition/')) {
    const parts = hash.slice(1).split('/').map(decodeURIComponent);
    const item = pack?.editions.find(entry => entry.id === parts[2] && entry.kind === parts[1]);
    return signalForItem(type, item, storyId || parts[3]);
  }
  if (hash.startsWith('#explanation/') || hash.startsWith('#topic/')) {
    const id = decodeURIComponent(hash.slice(hash.indexOf('/') + 1));
    const item = pack?.deepDives.find(entry => entry.id === id || entry.topic_id === id);
    return signalForItem(type, item);
  }
  return false;
}
function toggleSave(id, revisionId = null, rerender = true) {
  const adding = !state.savedIds.includes(id);
  if (state.savedIds.includes(id)) {
    state.savedIds = state.savedIds.filter(x => x !== id);
    delete state.savedRevisions[id];
  } else {
    state.savedIds = [...state.savedIds, id];
    if (revisionId) state.savedRevisions[id] = revisionId;
  }
  state = saveState(state);
  if (adding) {
    const item = [...(pack?.editions || []), ...(pack?.deepDives || [])].find(entry => entry.id === id);
    const parts = (location.hash || '').startsWith('#edition/') ? location.hash.slice(1).split('/').map(decodeURIComponent) : [];
    signalForItem('save', item, parts[3] || null);
  }
  showToast(adding ? 'Saved for later.' : 'Removed from saved items.');
  if (rerender) route();
}
function setFeedback(id, category) {
  state.feedback[id] = category; state = saveState(state);
  const type = { more: 'more', less: 'less', mute: 'mute' }[category];
  if (type) signalCurrent(type, id);
  route();
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
function plainDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date);
}
function briefingDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(date);
}

function firstReadableSection(edition) {
  return visibleSections(edition).find(section => section.kind !== 'link') || null;
}

function dailyBriefings() {
  const rows = currentItems(pack?.editions || [], edition => edition.kind === 'daily');
  const generated = rows.filter(edition => edition.generated === true);
  const available = generated.length ? generated : rows;
  // Live generation can publish more than one ID for the same local date.
  // Earlier briefings are date-based, so keep only the newest edition per day.
  const newestByDate = new Map();
  for (const edition of available) {
    const date = String(edition.local_date || revisionDate(edition)).slice(0, 10);
    const previous = newestByDate.get(date);
    if (!previous || compareRevision(edition, previous) > 0) newestByDate.set(date, edition);
  }
  return [...newestByDate.values()].sort((a, b) => revisionDate(b).localeCompare(revisionDate(a)));
}

// Mica · pass 010: shared read/listen estimates for Mica-owned surfaces.
function listenText(item) {
  if (typeof item === 'string') return item;
  const sections = item?.sections || [];
  if (sections.length) return sections.map(section => audioScriptText(section) || `${section.heading || ''}. ${section.body || ''}`).join(' ');
  return `${item?.title || ''}. ${item?.body || ''}`;
}

function listenEstimate(item) {
  return player.estimateListenMinutes(listenText(item));
}

function listenEstimateWithUnit(item) { return `${listenEstimate(item)} min`; }

function browserCanSpeak() {
  return typeof window.speechSynthesis !== 'undefined' && typeof window.SpeechSynthesisUtterance === 'function';
}

function canListen(item) {
  return Boolean(item && browserCanSpeak() &&
    !invalidatedEditionAudio.has(item.id) && !hasMaterialCorrection(item));
}

function startListening(item) {
  if (!canListen(item)) return;
  item = withNarrationTopics(item);
  if (player.item?.id === item.id && player.item?.revision_id === item.revision_id) player.play({ ...item, start_section_id: null });
  else {
    const saved = state.readingProgress[item.id];
    const fromParagraph = saved?.revisionId === item.revision_id ? saved.paragraphIndex : 0;
    player.listen(item, { fromParagraph });
  }
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
  return revisionDate(a).localeCompare(revisionDate(b)) ||
    String(a.generated_at || a.updated_at || a.created_at || '').localeCompare(String(b.generated_at || b.updated_at || b.created_at || '')) ||
    revisionNumber(a) - revisionNumber(b) || String(a.revision_id).localeCompare(String(b.revision_id));
}

function currentRevision(items, id, predicate = () => true) {
  const group = revisionGroup(items, id, predicate);
  if (group.current.length === 1) return group.current[0];
  const sameGeneratedEdition = group.current.length > 1 && group.current.every(item =>
    item.generated === true && ['daily', 'weekly'].includes(item.kind)
  );
  return sameGeneratedEdition ? group.current.sort(compareRevision).at(-1) : null;
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
  const editions = ids.map(id => currentRevision(pack.editions, id, item => item.kind === kind)).filter(Boolean);
  const latest = editions.sort(compareRevision).at(-1);
  return latest ? personalized(latest) : null;
}

function latestGeneratedDaily(editions = []) {
  return editions.filter(item => item?.kind === 'daily' && item.generated === true)
    .sort(compareRevision).at(-1) || null;
}

function newerEdition(candidate, previous) {
  if (!candidate) return false;
  if (!previous) return true;
  const candidateDate = String(candidate.local_date || '').slice(0, 10);
  const previousDate = String(previous.local_date || '').slice(0, 10);
  if (candidateDate && previousDate && candidateDate !== previousDate) return candidateDate > previousDate;
  const candidateTime = Date.parse(candidate.generated_at || candidate.updated_at || '');
  const previousTime = Date.parse(previous.generated_at || previous.updated_at || '');
  if (Number.isFinite(candidateTime) && Number.isFinite(previousTime) && candidateTime !== previousTime) return candidateTime > previousTime;
  return candidate.id !== previous.id || candidate.revision_id !== previous.revision_id;
}

function includeNewerCachedEdition(data, previousPack, serverProfileChanged) {
  if (!previousPack || serverProfileChanged) return data;
  const cached = latestGeneratedDaily(previousPack.editions);
  const server = latestGeneratedDaily(data.editions);
  if (!cached || !server) return data;
  const cachedDate = String(cached.local_date || '').slice(0, 10);
  const serverDate = String(server.local_date || '').slice(0, 10);
  const cachedTime = Date.parse(cached.generated_at || cached.updated_at || '');
  const serverTime = Date.parse(server.generated_at || server.updated_at || '');
  const cacheIsAhead = cachedDate && serverDate && cachedDate > serverDate ||
    cachedDate === serverDate && Number.isFinite(cachedTime) && Number.isFinite(serverTime) && cachedTime > serverTime;
  if (!cacheIsAhead || data.editions.some(item => item.id === cached.id && item.revision_id === cached.revision_id)) return data;
  const sources = [...new Map([...data.sources, ...previousPack.sources].map(source => [source.id, source])).values()];
  return { ...data, sources, editions: [...data.editions, cached] };
}

function stopCorrectedEditionAudio(id) {
  if (!id) return;
  invalidatedEditionAudio.add(id);
  for (const edition of pack?.editions || []) {
    if (edition.id === id && edition.audio) {
      edition.audio.state = 'superseded';
      edition.audio.superseded = true;
    }
  }
  const stopped = player.invalidate(id);
  const hadProgress = Boolean(state.progress[id]);
  const wasActive = state.activeAudio?.id === id;
  if (hadProgress || wasActive) state = invalidateAudioProgress(state, id);
  document.querySelectorAll('[data-audio-action]').forEach(button => {
    if (button.dataset.audioAction === id) button.remove();
  });
  if (stopped || wasActive) sessionStorage.setItem('briefing:correction-audio-notice', 'The prior recording is no longer available for this corrected edition.');
  syncListeningControls();
  refreshListenControls();
}

function invalidateMaterialCorrections() {
  for (const edition of pack?.editions || []) {
    if (hasMaterialCorrection(edition)) stopCorrectedEditionAudio(edition.id);
  }
}

// The same hook is used by content refreshes and correction updates arriving during playback.
window.addEventListener('briefing:material-correction', event => stopCorrectedEditionAudio(event.detail?.id));

function transcriptDestination(id, kind) {
  if (!transcriptReturnTo || transcriptReturnHash !== (location.hash || '#today')) return false;
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
  readingObserver?.disconnect();
  readingObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const section = entry.target.closest('.edition-section, .deep-dive-section');
      const paragraphIndex = entry.target.hasAttribute('data-narration-index') ? Number(entry.target.dataset.narrationIndex) : [...section.querySelectorAll('p')].indexOf(entry.target);
      if (player.status === 'playing' && player.item?.id === id) continue;
      const prior = state.readingProgress[id];
      if (prior?.revisionId === revisionId && prior.storyId === section.id && prior.paragraphIndex === paragraphIndex && prior.mode === 'read') continue;
      state.readingProgress[id] = { storyId: section.id, paragraphIndex: Math.max(0, paragraphIndex), mode: 'read', updatedAt: new Date().toISOString(), revisionId };
      state = saveState(state);
    }
  }, { rootMargin: '0px 0px -65% 0px', threshold: 0 });
  content.querySelectorAll(`${selector} p`).forEach(paragraph => readingObserver.observe(paragraph));
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
  return 'Read along';
}

function openTranscript(selected) {
  const resolved = currentTranscriptItem(selected);
  if (!resolved.available) return { available: false };
  const { current, corrected, selectedAudioMismatch } = resolved;
  if (readerAlreadyShows(current)) return { available: true, correctedItem: corrected || selectedAudioMismatch ? current : null, navigate: () => {
    const place = state.readingProgress[current.id];
    const story = content.querySelector(`#${CSS.escape(place?.storyId || '')}`);
    const paragraph = paragraphAt(story, place?.paragraphIndex || 0);
    paragraph?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
    paragraph?.classList.add('spoken-paragraph');
  } };
  transcriptReturnTo = { hash: location.hash || '#today', sectionId: null, scrollY: window.scrollY, focus: focusToken() };
  const saved = state.readingProgress[current.id];
  const validAnchor = !corrected && saved?.revisionId === current.revision_id && current.sections.some(section => section.id === saved.storyId);
  const correctionSection = corrected ? current.corrections?.find(item => item.severity === 'material')?.section_ids?.[0] : null;
  const placement = { id: current.id, sectionId: validAnchor ? saved.storyId : correctionSection || visibleSections(current)[0]?.id, paragraphIndex: validAnchor ? saved.paragraphIndex : 0, correction: corrected };
  const itemHash = current.kind === 'deep-dive'
    ? `#explanation/${encodeURIComponent(current.id)}`
    : `#edition/${encodeURIComponent(current.kind)}/${encodeURIComponent(current.id)}/${encodeURIComponent(placement.sectionId || '')}`;
  transcriptReturnHash = itemHash;
  return {
    available: true,
    correctedItem: corrected || selectedAudioMismatch ? current : null,
    navigate: () => navigateTranscript(itemHash, placement),
  };
}

function trackRouteSignals(routeHash, routeName) {
  if (lastSignalRoute === routeHash) return;
  lastSignalRoute = routeHash;
  const context = history.state?.routeContext || {};
  if (routeName.startsWith('source/')) {
    const sourceId = routeName.slice(7);
    const parent = context.parentHash || '';
    if (parent.startsWith('#edition/')) {
      const parts = parent.slice(1).split('/').map(decodeURIComponent);
      const edition = pack?.editions.find(item => item.id === parts[2] && item.kind === parts[1]);
      signalForItem('sourceOpen', edition, context.sectionId || parts[3]);
    } else if (parent.startsWith('#explanation/') || parent.startsWith('#topic/')) {
      const id = decodeURIComponent(parent.slice(parent.indexOf('/') + 1));
      const dive = pack?.deepDives.find(item => item.id === id || item.topic_id === id);
      signalForItem('sourceOpen', dive);
    } else {
      const edition = pack?.editions.find(item => item.sections?.some(section => section.source_ids?.includes(sourceId)));
      const section = edition?.sections.find(row => row.source_ids?.includes(sourceId));
      signalForItem('sourceOpen', edition, section?.id);
    }
  } else if (routeName.startsWith('explanation/') || routeName.startsWith('topic/')) {
    const id = decodeURIComponent(routeName.slice(routeName.indexOf('/') + 1));
    const dive = pack?.deepDives.find(item => item.id === id || item.topic_id === id);
    if (dive) {
      const storyId = context.parentHash?.startsWith('#edition/') && context.sectionId
        ? context.sectionId
        : context.sectionId && dive.sections?.some(section => section.id === context.sectionId)
          ? context.sectionId : dive.sections?.[0]?.id || dive.id;
      recordSignal('explainOpen', storyId, topicIdFor(dive));
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

function renderEditionPage(kind, exactEdition = null, sectionId = null) {
  const edition = exactEdition || currentEdition(kind);
  if (!edition || (kind === 'weekly' && interestProfile && edition.generated !== true)) {
    content.append(node('h1', kind === 'weekly' && interestProfile ? 'No weekly recap yet' : `${kind === 'weekly' ? 'The weekly recap' : 'Today’s briefing'} is unavailable`));
    content.append(node('p', kind === 'weekly' && interestProfile ? 'A weekly recap will appear here when one is ready.' : 'There is no unambiguous current version available right now.'));
    return;
  }
  if (edition.kind === 'weekly' && !sectionId) return renderWeeklyRecapPage(edition);
  const routeContext = history.state?.routeContext || {};
  const parentHash = routeContext.parentHash;
  const returnHash = parentHash?.startsWith('#edition/') || parentHash?.startsWith('#topic-detail/') || parentHash === '#weekly' ? parentHash : parentHash === '#library' ? '#library' : '#today';
  const returnLabel = routeContext.parentLabel || (returnHash === '#library' ? 'Earlier' : returnHash === '#today' ? 'Today' : returnHash === '#weekly' ? 'Weekly recap' : parentHash?.startsWith('#edition/') ? 'Today' : 'Topics');
  const readerHeader = node('div', null, 'reader-sticky');
  const backButton = node('button', `‹ ${returnLabel}`, 'reader-back');
  if (transcriptDestination(edition.id, edition.kind)) backButton.classList.add('transcript-return');
  backButton.type = 'button';
  backButton.addEventListener('click', () => {
    if (transcriptDestination(edition.id, edition.kind)) { returnFromTranscript(); return; }
    if (routeContext.parentHash && history.length > 1) history.back();
    else navigate(returnHash, { root: rootForHash(returnHash), parentHash: null });
  });
  const sections = visibleSections(edition).filter(item => item.kind !== 'link');
  const activeSection = sections.find(item => item.id === sectionId) || sections[0];
  const matchedInterestTopic = section => interestProfile?.topics.find(topic => section.topic_ids?.includes(topic.id)) || null;
  const feedbackTopicId = section => matchedInterestTopic(section)?.id || topicIdFor(edition, section.id);
  const storyIndex = Math.max(0, sections.findIndex(item => item.id === activeSection?.id));
  rememberEditionMode(edition.id, 'read');
  readerHeader.append(backButton);
  const progress = node('div', `Story ${storyIndex + 1} of ${sections.length}`, 'story-progress');
  progress.setAttribute('aria-label', `Story ${storyIndex + 1} of ${sections.length}`);
  const dots = node('span', null, 'story-dots');
  dots.setAttribute('aria-hidden', 'true');
  sections.forEach((_, index) => { const dot = node('i', null, index <= storyIndex ? 'current' : ''); dots.append(dot); });
  progress.append(dots);
  readerHeader.append(progress);
  content.append(readerHeader);
  const body = node('div'); content.append(body);
  renderEdition(body, personalized(edition), pack.topics, {
    profile: interestProfile,
    sectionId: sectionId || history.state?.routeContext?.sectionId || sections[0]?.id,
    backLabel: returnLabel,
    transcriptView: Boolean(history.state?.routeContext?.transcript || transcriptDestination(edition.id, edition.kind)),
    listeningAvailable: listeningAvailable(),
    listenEstimate: listenEstimateWithUnit,
    listenState: item => ({ label: listenControlLabel(item) === 'Listen' ? 'Listen from here' : listenControlLabel(item) }),
    visibleParagraphIndex: sectionElement => {
      const saved = state.readingProgress[edition.id];
      if (saved?.revisionId === edition.revision_id && saved.storyId === sectionElement?.id && Number.isInteger(saved.paragraphIndex)) return saved.paragraphIndex;
      const paragraphs = [...(sectionElement?.querySelectorAll('p') || [])];
      const index = paragraphs.findIndex(paragraph => { const rect = paragraph.getBoundingClientRect(); return rect.bottom > 0 && rect.top < window.innerHeight; });
      return Math.max(0, index);
    },
    listenFromStory: (item, options) => { rememberEditionMode(edition.id, 'listen'); startStoryListening(item, options); },
    sources: pack.sources,
    play: item => {
      if (invalidatedEditionAudio.has(item.id)) return;
      const paragraphs = [...body.querySelectorAll('.edition-section p')];
      const index = paragraphs.findIndex(paragraph => paragraph.getBoundingClientRect().bottom > 0);
      const saved = state.readingProgress[item.id];
      const fromParagraph = saved?.revisionId === item.revision_id && saved.storyId === item.start_section_id
        ? saved.paragraphIndex : Math.max(0, index);
      player.listen(withNarrationTopics(readableEdition(item)), { fromParagraph });
    },
    openTopic: openDeepDive,
    hasDeepDive: id => pack.deepDives.some(item => item.topic_id === id),
    deepDives: currentItems(pack.deepDives),
    openSource,
    whyLine: section => {
      const topic = matchedInterestTopic(section);
      if (!topic) return '';
      const subject = topic.sourcePhrase?.trim() || topic.label;
      return `Why you're seeing this: it matches “${subject}” in your interest brief.`;
    },
    interestFeedback: (type, section) => recordSignal(type, section.id, feedbackTopicId(section)),
    getInterestFeedback: section => signalEvents().filter(event => event.storyId === section.id && event.topicId === feedbackTopicId(section) && ['more', 'less'].includes(event.type)).at(-1)?.type || null,
    correctionStopped: sessionStorage.getItem('briefing:correction-audio-notice'),
    save: (id, revisionId) => toggleSave(id, revisionId, false),
    finish: (id, revision) => { const key = `${id}:${revision}`; const fresh = !state.finishedIds.includes(key); state.finishedIds = [...new Set([...state.finishedIds, key])]; state = saveState(state); if (fresh) signalCurrent('finish'); },
    isFinished: (id, revision) => state.finishedIds.includes(`${id}:${revision}`),
    feedback: setFeedback,
    isSaved: id => state.savedIds.includes(id),
    getFeedback: id => state.feedback[id],
    readingPlace: () => {
      const place = state.readingProgress[edition.id];
      return place?.revisionId === edition.revision_id ? place.storyId : null;
    },
    continueReading: sectionId => {
      document.getElementById(sectionId)?.scrollIntoView({ block: 'start' });
      document.getElementById(`heading-${sectionId}`)?.focus({ preventScroll: true });
    },
    nextStory: nextSectionId => {
      const hash = `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}/${encodeURIComponent(nextSectionId)}`;
      history.replaceState({ ...(history.state || {}), routeContext: { ...(history.state?.routeContext || {}), sectionId: nextSectionId } }, '', hash);
      route();
    },
  });
  for (const section of body.querySelectorAll('.edition-section')) {
    const story = edition.sections.find(item => item.id === section.id);
    if (!story) continue;
    const tools = node('div', null, 'story-tools-row');
    const why = node('button', 'Why this story', 'why-story-trigger');
    why.type = 'button'; why.addEventListener('click', () => showWhySheet(story, edition));
    const storyActions = node('button', 'Share or report', 'story-actions-trigger'); storyActions.type = 'button';
    storyActions.addEventListener('click', () => showStoryActionsSheet(story, edition, storyActions));
    tools.append(why, storyActions); section.append(tools);
  }
  if (edition.kind === 'weekly') {
    body.querySelector('.story-listen')?.classList.add('weekly-listen');
  }
  beginStoryVisit(edition, sectionId || sections[0]?.id, location.hash || `#edition/${kind}/${edition.id}`);
  trackReadingPlace(edition.id, edition.revision_id, '.edition-section');
  sessionStorage.removeItem('briefing:correction-audio-notice');
  if (!pendingTranscriptPlacement) {
    const activeSection = sections.find(item => item.id === (sectionId || history.state?.routeContext?.sectionId)) || sections[0];
    const prior = state.readingProgress[edition.id];
    if (activeSection && (prior?.revisionId !== edition.revision_id || prior.storyId !== activeSection.id)) {
      state.readingProgress[edition.id] = { storyId: activeSection.id, paragraphIndex: 0, mode: 'read', updatedAt: new Date().toISOString(), revisionId: edition.revision_id };
      state = saveState(state);
    }
  }
}

function renderWeeklyRecapPage(edition) {
  const routeContext = history.state?.routeContext || {};
  const returnHash = routeContext.parentHash === '#library' ? '#library' : '#today';
  const returnLabel = returnHash === '#library' ? 'Earlier' : 'Today';
  const sticky = node('div', null, 'reader-sticky');
  const backButton = node('button', `‹ ${returnLabel}`, 'reader-back'); backButton.type = 'button';
  backButton.addEventListener('click', () => {
    if (routeContext.parentHash && history.length > 1) history.back();
    else navigate(returnHash, { root: rootForHash(returnHash), parentHash: null });
  });
  sticky.append(backButton); content.append(sticky);
  const body = node('div'); content.append(body);
  renderWeeklyRecap(body, edition, pack.sources, {
    listeningAvailable: listeningAvailable(),
    listenEstimate: listenEstimateWithUnit,
    listen: item => { rememberEditionMode(item.id, 'listen'); startEditionListening(item); },
    save: (id, revisionId) => toggleSave(id, revisionId, false),
    isSaved: id => state.savedIds.includes(id),
    openSource: id => {
      const hash = `#source/${encodeURIComponent(id)}`;
      navigate(hash, { root: routeContext.root || rootForHash(location.hash), parentHash: location.hash || '#weekly', parentLabel: 'Weekly recap',
        parentScroll: window.scrollY, parentFocus: { type: 'source', id } });
    },
    originalStoryFor: weeklySection => {
      const topicIds = new Set([...(weeklySection?.topic_ids || []), weeklySection?.topic_id].filter(Boolean));
      if (!topicIds.size) return null;
      const weekStart = String(edition.week_start || '').slice(0, 10);
      const weekEnd = String(edition.week_end || '').slice(0, 10);
      const candidates = dailyBriefings();
      const inWeek = candidates.filter(item => {
        const date = String(item.local_date || '').slice(0, 10);
        return date && (!weekStart || date >= weekStart) && (!weekEnd || date <= weekEnd);
      });
      for (const daily of (inWeek.length ? inWeek : candidates)) {
        const section = visibleSections(daily).find(item => item.kind !== 'link' && ((item.topic_ids || []).some(id => topicIds.has(id)) || topicIds.has(item.topic_id)));
        if (section) return { editionId: daily.id, sectionId: section.id };
      }
      return null;
    },
    openOriginalStory: (editionId, sectionId, opener) => {
      const daily = currentRevision(pack.editions, editionId, item => item.kind === 'daily');
      const section = daily?.sections?.find(item => item.id === sectionId);
      if (!daily || !section) return;
      const hash = `#edition/daily/${encodeURIComponent(daily.id)}/${encodeURIComponent(section.id)}`;
      if (opener && !opener.id) opener.id = `weekly-development-${section.id}`;
      navigate(hash, { root: routeContext.root || rootForHash(location.hash), parentHash: location.hash || '#weekly', parentLabel: 'Weekly recap',
        parentScroll: window.scrollY, parentFocus: opener?.id ? { type: 'id', id: opener.id } : null, sectionId: section.id });
    },
  });
}

function continueToSection(sectionId) {
  const section = document.getElementById(sectionId);
  section?.scrollIntoView({ block: 'start' });
  section?.querySelector('h2')?.focus({ preventScroll: true });
}

function todayListSkeleton() {
  const list = node('div', null, 'today-list-skeleton');
  list.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 3; index++) {
    const story = node('div', null, 'today-story-skeleton');
    story.append(node('span', null, 'loading-skeleton-block today-skeleton-topic'));
    story.append(node('span', null, 'loading-skeleton-block today-skeleton-title'));
    story.append(node('span', null, 'loading-skeleton-block today-skeleton-title today-skeleton-title-short'));
    story.append(node('span', null, 'loading-skeleton-block today-skeleton-summary'));
    story.append(node('span', null, 'loading-skeleton-block today-skeleton-summary today-skeleton-summary-short'));
    list.append(story);
  }
  return list;
}

function easternToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const value = type => parts.find(part => part.type === type)?.value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}

function dailyRunFailedToday(status) {
  const report = status?.daily_run;
  const failed = ['failed', 'timeout'].includes(report?.status) || (!report && status?.ok === false);
  if (!failed) return false;
  const date = value => typeof value === 'string' ? value.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || null : null;
  const runDate = date(report?.local_date) || date(status?.last_run_at);
  return runDate === easternToday();
}

function todayRunFailurePanel() {
  const panel = node('aside', null, 'today-service-preparing');
  panel.setAttribute('role', 'status');
  panel.append(
    node('h2', "Today's briefing is still being prepared"),
    node('p', 'The scheduled run did not finish. Please check back soon.'),
    link('View earlier briefings', '#library', 'button-link'),
  );
  return panel;
}

function startTodayBuildAfterStatusCheck(profile, panel) {
  void (async () => {
    let failed = false;
    try {
      const response = await fetch('/api/status', { cache: 'no-store' });
      if (response.ok) failed = dailyRunFailedToday(await response.json());
    } catch { /* The regular build path remains available if status cannot be read. */ }
    if (!panel.isConnected || location.hash !== '#today') return;
    if (failed) {
      panel.replaceWith(todayRunFailurePanel());
      return;
    }
    panel.remove();
    content.append(node('h1', 'Building your briefing'), node('p', 'Finding stories for the interests you described.', 'build-today-status'));
    navigate('#interests/build', { root: 'today', parentHash: '#today', firstRun: true });
    void startBuildGeneration(profile);
  })();
}


function markDailyEditionSeen(edition) {
  if (edition?.kind !== 'daily' || edition.generated !== true ||
      typeof edition.profile_id !== 'string' || typeof edition.id !== 'string' ||
      typeof edition.revision_id !== 'string') return;
  const key = `${edition.profile_id}:${edition.revision_id}`;
  if (seenEditionMarksInFlight.has(key)) return;
  seenEditionMarksInFlight.add(key);
  void fetch('/api/edition/seen', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
    body: JSON.stringify({ profile_id: edition.profile_id, edition_id: edition.id, revision_id: edition.revision_id }),
  }).then(response => {
    if (!response.ok) seenEditionMarksInFlight.delete(key);
  }).catch(() => seenEditionMarksInFlight.delete(key));
}

function profileTopicNames(profile) {
  const names = (profile?.topics || []).filter(topic => !topic.exclude?.length).map(topic => buildChipLabel(topic));
  return names.length ? names.slice(0, 3).join(' or ') : 'your interests';
}

function profileBuildKey(profile) {
  return JSON.stringify({ words: profile?.words || '', minutes: profile?.minutes || 12,
    topics: (profile?.topics || []).map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude, depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })),
    muted: profile?.muted || [] });
}

async function fetchSavedServerProfile() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch('/api/profile', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) {
      await response.body?.cancel();
      return null;
    }
    const result = await response.json();
    return result?.profile ? validateProfile(result.profile) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

function filterGeneralEditionForProfile(edition, profile) {
  const preferences = (profile?.topics || []).filter(topic => (topic.include || []).length || topic.mustFollow === true)
    .filter(topic => !topic.exclude?.length || topic.mustFollow === true);
  const sourceSections = edition.sections || [];
  const coverageById = new Map((edition.coverage || []).map(row => [row.topic_id, row]));
  const chosenSections = [];
  const coverage = [];
  for (const preference of preferences) {
    const known = pack.topics.find(topic => topic.id === preference.id || topic.label.toLocaleLowerCase() === String(preference.label || '').toLocaleLowerCase());
    const topicId = known?.id || preference.id;
    const matched = sourceSections.filter(section => (section.topic_ids || []).includes(topicId));
    const fallbackPhrases = [preference.scope, preference.label, ...(preference.include || []), preference.sourcePhrase]
      .map(value => String(value || '').trim().toLocaleLowerCase()).filter(value => value.length >= 4);
    const phraseMatches = matched.length ? [] : sourceSections.filter(section => {
      const text = `${section.heading || ''} ${section.body || ''}`.toLocaleLowerCase();
      return fallbackPhrases.some(phrase => text.includes(phrase));
    });
    const stories = matched.length ? matched : phraseMatches;
    for (const section of stories) {
      if (!chosenSections.some(item => item.id === section.id)) {
        chosenSections.push({ ...section, topic_ids: [...new Set([...(section.topic_ids || []), preference.id])] });
      } else {
        const existing = chosenSections.find(item => item.id === section.id);
        existing.topic_ids = [...new Set([...(existing.topic_ids || []), preference.id])];
      }
    }
    if (stories.length) {
      const original = coverageById.get(topicId) || coverageById.get(preference.id);
      coverage.push(original
        ? { ...original, topic_id: preference.id, label: preference.label, state: ['quiet', 'limited'].includes(original.state) ? 'included' : original.state }
        : { topic_id: preference.id, label: preference.label, state: 'included' });
    }
  }
  return { ...edition, sections: chosenSections, coverage };
}

async function showGeneralEditionFallback(profile, attempt) {
  try {
    const loaded = await loadPack(true);
    if (attempt !== buildAttempt) return false;
    pack = loaded;
    const general = currentEdition('daily');
    if (!general?.generated) return false;
    const profileKey = profileBuildKey(profile);
    localStorage.setItem('briefing:v1:built-profile-key', profileKey);
    localStorage.setItem(BUILD_GENERAL_FALLBACK_KEY, profileKey);
    buildNotice = ''; sessionStorage.removeItem('briefing:build-error');
    clearTimeout(buildTimeoutHandle); buildTimeoutHandle = null;
    navigate('#today', { root: 'today', parentHash: null });
    return true;
  } catch {
    return false;
  }
}

function renderNoStoryExplanations(container, profile) {
  const list = node('section', null, 'today-topic-explanations');
  list.append(node('h2', 'Your topics'));
  (profile?.topics || []).filter(topic => !topic.exclude?.length).forEach(topic => {
    const dive = pack.deepDives.find(item => item.topic_id === topic.id);
    if (dive) list.append(link(dive.title, `#explanation/${encodeURIComponent(dive.id)}`, 'topic-explanation-link'));
    else list.append(node('p', topic.label, 'topic-explanation-link'));
  });
  if (list.children.length > 1) container.append(list);
}

function invalidateTopicsEdition() {
  // Persist the gate before interpretation/network work so reload cannot expose an old edition.
  if (!pendingTopicsUpdate()) localStorage.setItem(TOPICS_PENDING_KEY, JSON.stringify({
    revisions: (pack?.editions || []).map(item => item.id + ':' + item.revision_id),
  }));
  localStorage.removeItem('briefing:v1:built-profile-key');
  localStorage.removeItem(BUILD_GENERAL_FALLBACK_KEY);
  clearEditionCache();
  player.close(); state.activeAudio = null; state = saveState(state);
}

function setTopicsProgress(message) {
  topicsProgress = message;
  const status = content.querySelector('.topics-update-stage');
  if (status) status.textContent = message;
}

function topicsProgressFor(stepName, found, progress, job) {
  if (/writing|write|edition_writer/.test(stepName)) {
    const message = typeof progress === 'object' ? progress.message || job.message || '' : job.message || '';
    const story = String(message).match(/writing\s+story\s+(?:candidate\s+)?(\d+)\s+of\s+(\d+)/i);
    return story ? `Writing story ${story[1]} of ${story[2]}` : 'Writing your briefing…';
  }
  if (/reading|read/.test(stepName)) return 'Reading sources…';
  if (/checking|scoring|scorecard|verify/.test(stepName)) return 'Checking sources…';
  if (/finding|story|fetching|feed_fetch|research|queued|discover/.test(stepName) || found != null) {
    return found != null ? `Finding stories · ${found} found` : 'Finding stories…';
  }
  return 'Preparing your briefing…';
}

async function updateTopicsBriefing(words, profile = null) {
  if (topicsUpdating) return;
  const attempt = ++buildAttempt;
  topicsDraft = words; sessionStorage.setItem(TOPICS_DRAFT_KEY, words);
  topicsUpdating = true; topicsError = ''; topicsProgress = profile ? 'Saving your interests…' : 'Understanding your words…'; buildInProgress = true;
  invalidateTopicsEdition();
  route();
  try {
    if (!profile) {
      const result = await interpretProfile(words, { topics: pack.topics, minutes: interestProfile?.minutes || state.durationMinutes });
      if (attempt !== buildAttempt) return;
      if (result.fallbackError || !result.profile?.topics?.length) throw new Error('interpret');
      // Preserve the exact reader-authored sentence and existing pause choices.
      profile = { ...result.profile, words, muted: [...new Map([
        ...(result.profile.muted || []), ...(interestProfile?.muted || []).filter(item =>
          interestProfile.topics.some(topic => topic.label.toLowerCase() === item.label.toLowerCase())),
      ].map(item => [item.label.toLowerCase(), item])).values()].slice(0, 12) };
    }
    interestProfile = saveProfile(profile, { server: false });
    setTopicsProgress('Saving your interests…');
    await startBuildGeneration(interestProfile, attempt, { inline: true, returnToday: true });
  } catch {
    if (attempt !== buildAttempt) return;
    topicsUpdating = false; buildInProgress = false; topicsProgress = '';
    topicsError = "We couldn't build it right now. Your last briefing is still here.";
    if (location.hash === '#topics' || location.hash === '#today') route();
  }
}

function renderTopicsPage() {
  document.querySelector('[data-page-title]').textContent = '';
  const profileTopics = (interestProfile?.topics || pack.topics.filter(topic => state.selectedIds.includes(topic.id)))
    .filter(topic => (topic.include || []).length || !topic.exclude?.length || topic.mustFollow);
  const daily = currentEdition('daily');
  const matched = !interestProfile || localStorage.getItem('briefing:v1:built-profile-key') === profileBuildKey(interestProfile);
  renderTopicsScreen({
    content, document, node, link, renderTopics, profile: interestProfile, profileTopics,
    deepDives: currentItems(pack.deepDives), selectedIds: profileTopics.map(topic => topic.id),
    dailyCoverage: matched && !pendingTopicsUpdate() ? daily?.coverage || [] : [],
    actions: {
    openDeepDive,
    words: topicsDraft ?? interestProfile?.words ?? '',
    savedWords: interestProfile?.words || '',
    muted: interestProfile?.muted || [],
    updating: topicsUpdating || Boolean(pendingTopicsUpdate() && buildInProgress),
    progress: topicsProgress,
    error: topicsError || (pendingTopicsUpdate() && !topicsUpdating ? 'Your changes are waiting. Update your briefing to try again.' : ''),
    draftWords: words => { topicsDraft = words; sessionStorage.setItem(TOPICS_DRAFT_KEY, words); },
    updateBriefing: words => void updateTopicsBriefing(words),
    retryUpdate: words => void updateTopicsBriefing(words),
    pauseTopic: (id, paused) => {
      const profile = interestProfile || defaultProfile(pack.topics);
      const topic = profile.topics.find(item => item.id === id);
      if (!topic) return;
      const muted = profile.muted.filter(item => item.label.toLowerCase() !== topic.label.toLowerCase());
      if (paused) muted.push({ label: topic.label });
      void updateTopicsBriefing(profile.words, { ...profile, muted });
    },
    interestBrief: deriveInterestBrief(interestProfile || { topics: profileTopics, muted: [] }),
    openEpisodeRequest: () => navigate('#ask-episode', { root: 'topics', parentHash: '#topics', parentScroll: window.scrollY,
      parentFocus: { type: 'href', id: '#ask-episode' } }),
  },
  });
}

function renderInterestsPage() {
  if (!interestProfile) interestProfile = saveProfile(defaultProfile(pack.topics), { server: false });
  const context = history.state?.routeContext || {};
  const parentHash = context.parentHash || '#preferences';
  const sticky = node('div', null, 'reader-sticky interests-back');
  const backButton = node('button', parentHash === '#preferences' ? '‹ Settings' : immediateParentLabel(parentHash, context), 'reader-back');
  backButton.type = 'button'; backButton.addEventListener('click', () => {
    if (context.parentHash && !context.direct && history.length > 1) history.back();
    else navigate(parentHash, { root: context.root || rootForHash(parentHash), parentHash: null });
  });
  sticky.append(backButton); content.append(sticky);
  const suggestions = generateSuggestions({ profile: interestProfile, catalog: pack.topics });
  renderInterests(content, interestProfile, suggestions, pack.topics, {
    interpret: async words => {
      interestPageFlags = [];
      interestPageNotice = '';
      const result = await interpretProfile(words, { topics: pack.topics, minutes: interestProfile.minutes });
      interestPageFlags = result.flags || [];
      interestPageNotice = result.fallbackError || '';
      interestProfile = saveProfile(result.profile);
      interestDraft = null;
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
      route();
    },
    updateTopic: (id, changes) => {
      interestProfile = saveProfile({ ...interestProfile, topics: interestProfile.topics.map(topic => topic.id === id ? { ...topic, ...changes } : topic) });
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
      route();
    },
    removeTopic: id => {
      const topic = interestProfile.topics.find(item => item.id === id);
      if (!topic) return;
      interestProfile = saveProfile({ ...interestProfile, topics: interestProfile.topics.filter(item => item.id !== id), muted: [...interestProfile.muted, { label: topic.label }] });
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
      route();
    },
    unmute: label => {
      const topic = pack.topics.find(item => item.label === label);
      interestProfile = saveProfile({ ...interestProfile, muted: interestProfile.muted.filter(item => item.label !== label),
        topics: interestProfile.topics.map(item => item.label === label ? { ...item, exclude: [] } : item).concat(topic && !interestProfile.topics.some(item => item.label === topic.label) ? [{ id: topic.id, label: topic.label, scope: topic.scope, include: [], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: topic.label }] : []) });
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
      route();
    },
    accept: suggestion => {
      interestProfile = saveProfile(applySuggestion(interestProfile, suggestion, pack.topics));
      invalidateTopicsEdition();
      void startBuildGeneration(interestProfile, null, { inline: true });
      route();
    },
    snooze: suggestion => { snoozeSuggestion(suggestion.id); route(); },
    clearSignals: () => {
      if (!window.confirm('Clear the local reading signals and suggestions? Your written interests will stay.')) return;
      resetBehaviorWeighting(); clearSuggestionSnoozes(); route();
    },
  });
  const wordsForm = content.querySelector('.interest-words-form');
  if (interestPageFlags.length) {
    const flags = node('aside', null, 'interest-interpreter-flags');
    flags.setAttribute('role', 'status');
    flags.append(node('p', `Interpretation note: ${interestPageFlags.join('; ')}`));
    wordsForm?.after(flags);
  }
  if (interestPageNotice) {
    const notice = node('aside', null, 'interest-interpreter-error');
    notice.setAttribute('role', 'status');
    notice.append(node('p', interestPageNotice));
    wordsForm?.after(notice);
  }
}

function immediateParentLabel(hash, context = {}) {
  if (hash === '#library') return '‹ Earlier';
  if (hash === '#topics') return '‹ Topics';
  if (hash === '#weekly') return '‹ Weekly recap';
  if (hash === '#today' || !hash) return '‹ Today';
  if (hash.startsWith('#topic-detail/')) return '‹ Topics';
  if (hash.startsWith('#topic/') || hash.startsWith('#explanation/')) return '‹ Explanation';
  if (hash.startsWith('#edition/')) {
    const parts = hash.slice(1).split('/').map(decodeURIComponent);
    const editionId = parts[2];
    const sectionId = parts[3] || context.sectionId;
    const edition = pack.editions.find(item => item.id === editionId && item.kind === parts[1]);
    const sectionIndex = edition?.sections.findIndex(item => item.id === sectionId) ?? -1;
    return sectionIndex >= 0 ? `‹ Story ${sectionIndex + 1}` : '‹ Briefing';
  }
  return '‹ Briefing';
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
  const backLabel = immediateParentLabel(origin?.hash, routeContext);
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
    play: item => startListening(item),
    listen: item => startListening(item),
    canListen,
    estimateListenMinutes: item => listenEstimate(item),
    openSource,
    save: (itemId, revision) => toggleSave(itemId, revision, false),
    isSaved: itemId => state.savedIds.includes(itemId),
  });
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

function renderTopicDetailPage(id) {
  const topic = pack.topics.find(item => item.id === id) || interestProfile?.topics.find(item => item.id === id);
  if (!topic) return renderMissing();
  const body = node('div', null, 'topic-detail-page'); content.append(body);
  const context = history.state?.routeContext || {};
  const sticky = node('div', null, 'reader-sticky topic-detail-sticky');
  const back = node('button', '‹ Topics', 'reader-back'); back.type = 'button';
  back.addEventListener('click', () => {
    if (context.parentHash === '#topics' && !context.direct && history.length > 1) history.back();
    else navigate('#topics', { root: 'topics', parentHash: null });
  });
  sticky.append(back); content.insertBefore(sticky, body);
  const profileTopic = interestProfile?.topics.find(item => item.id === id);
  const isPaused = Boolean(profileTopic && interestProfile?.muted.some(item => item.label.toLocaleLowerCase() === profileTopic.label.toLocaleLowerCase()));
  const editions = currentItems(pack.editions).filter(item => item.kind === 'daily' && item.generated === true)
    .sort((a, b) => String(b.local_date || '').localeCompare(String(a.local_date || '')));
  const items = editions.flatMap(edition => (edition.sections || [])
    .filter(section => (section.topic_ids || []).includes(id) || section.topic_id === id)
    .map(section => ({ section, edition, href: `#edition/daily/${encodeURIComponent(edition.id)}/${encodeURIComponent(section.id)}` })))
    .slice(0, 12);
  renderTopicDetail(body, {
    topic, items, sources: pack.sources, followed: Boolean(profileTopic), paused: isPaused,
    actions: {
      setPaused: paused => {
        const profile = { ...interestProfile, muted: (interestProfile.muted || []).filter(item => item.label.toLocaleLowerCase() !== profileTopic.label.toLocaleLowerCase()) };
        if (paused) profile.muted.push({ label: profileTopic.label });
        void updateTopicsBriefing(profile.words, profile);
      },
      follow: () => {
        const profile = interestProfile || defaultProfile(pack.topics);
        const nextTopic = { id: topic.id, label: topic.label, scope: topic.scope || topic.label,
          include: [topic.sourcePhrase || topic.label], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false,
          sourcePhrase: topic.sourcePhrase || topic.label };
        void updateTopicsBriefing(profile.words || nextTopic.label, { ...profile, topics: [...profile.topics.filter(item => item.id !== topic.id), nextTopic] });
      },
      editInterests: () => navigate('#interests', { root: 'topics', parentHash: location.hash, parentScroll: window.scrollY }),
      askEpisode: () => { episodeDraft.prompt = `Tell me about ${topic.label}`; navigate('#ask-episode', { root: 'topics', parentHash: location.hash, parentScroll: window.scrollY }); },
      openStory: item => navigate(item.href, { root: 'topics', parentHash: location.hash, parentScroll: window.scrollY, sectionId: item.section.id }),
      openSource: sourceId => openSource(sourceId),
    },
  });
  if (interestProfile && !editionHistoryLoaded) void loadEditionHistory();
}

function transcriptReturnLabel() {
  const hash = transcriptReturnTo?.hash || '#today';
  if (hash === '#library') return '← Back to Earlier';
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
  const sticky = node('div', null, 'reader-sticky');
  const backButton = node('button', routeContext.parentLabel || immediateParentLabel(routeContext.parentHash, routeContext), 'reader-back');
  backButton.type = 'button'; backButton.addEventListener('click', back);
  sticky.append(backButton); content.append(sticky);
  const safeURL = (() => { try { const url = new URL(source.url); return url.protocol === 'https:' && url.hostname ? url : null; } catch { return null; } })();
  const domain = safeURL?.hostname.replace(/^www\./, '') || '';
  const publisherNames = { 'federalreserve.gov': 'Federal Reserve', 'fdic.gov': 'FDIC', 'consumerfinance.gov': 'Consumer Financial Protection Bureau', 'pages.nist.gov': 'NIST', 'content.naic.org': 'National Association of Insurance Commissioners', 'theifab.com': 'The IFAB' };
  const publisher = source.publisher || publisherNames[domain] || domain || 'Reference';
  const parentHash = routeContext.parentHash || '';
  const associatedDive = parentHash.startsWith('#topic/')
    ? currentItems(pack.deepDives, item => item.topic_id === decodeURIComponent(parentHash.slice(7)))[0]
    : parentHash.startsWith('#explanation/')
      ? currentRevision(pack.deepDives, decodeURIComponent(parentHash.slice(13)))
      : null;
  const editionMatch = parentHash.startsWith('#edition/') && /^#edition\/[^/]+\/[^/]+/.exec(parentHash);
  const associatedEdition = editionMatch
    ? currentRevision(pack.editions, decodeURIComponent(editionMatch[0].split('/')[2]))
    : null;
  const sourcePublication = source.date || source.published_at || '';
  const sourceUpdate = source.updated_at || '';
  const sourceDateValue = sourcePublication || sourceUpdate;
  const sourceDate = plainDate(sourceDateValue) || 'Date not listed';
  const sourceDateLabel = sourcePublication ? `Document date · ${sourceDate}`
    : sourceUpdate ? `Updated · ${sourceDate}` : 'Document date · Date not listed';
  const reviewedDate = source.reviewed_at || associatedDive?.reviewed_at || associatedEdition?.reviewed_at || associatedEdition?.published_at || associatedEdition?.local_date || '2026-11-16';
  const heading = node('h1', source.title); heading.tabIndex = -1;
  heading.className = 'source-title';
  content.append(node('p', publisher, 'source-publisher'), node('p', 'Document', 'source-document'), heading,
    node('p', sourceDateLabel, 'source-published'));
  content.append(node('h2', 'What this supports', 'source-section-heading'));
  const sourceNote = (source.note || 'This reference provides background for the explanation.')
    .replace(/\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b/g, value => plainDate(value) || value);
  content.append(node('p', sourceNote));
  content.append(node('p', `Reviewed · ${displayDate(reviewedDate)}`, 'source-reviewed'));
  if (source.kind === 'Internal fictional evidence') content.append(node('p', 'Sample · fictional', 'source-sample-tag sample-label'));
  const row = node('div', null, 'button-row source-actions');
  if (safeURL && source.kind !== 'Internal fictional evidence') {
    const external = link('Open original ↗', safeURL.href, 'button-link');
    external.setAttribute('aria-label', `Open original: ${publisher}, ${source.title || 'current source'}`);
    external.target = '_blank'; external.rel = 'noopener noreferrer';
    row.append(external);
  }
  if (safeURL && source.kind !== 'Internal fictional evidence') row.append(node('span', 'Opens in a new tab', 'source-new-tab'));
  content.append(row);
}

function interestOriginHash() {
  return history.state?.routeContext?.interestReturnHash || '#today';
}

function navigateInterest(hash) {
  const routeContext = history.state?.routeContext || {};
  const currentHash = location.hash || '#today';
  navigate(hash, {
    root: routeContext.root || rootForHash(currentHash),
    parentHash: currentHash,
    parentScroll: window.scrollY,
    parentFocus: focusToken(),
    interestReturnHash: routeContext.interestReturnHash || (currentHash === '#preferences' ? '#preferences' : '#today'),
  });
}

function renderInterestTopbar(step) {
  const context = history.state?.routeContext || {};
  const parent = context.parentHash;
  if (!parent || context.firstRun) return;
  const label = parent === '#preferences' ? 'Settings' : parent === '#interests/confirm' ? 'What I heard' : 'Describe';
  const sticky = node('div', null, 'reader-sticky interest-reader-sticky');
  const backButton = node('button', `‹ ${label}`, 'reader-back');
  backButton.type = 'button';
  backButton.addEventListener('click', back);
  sticky.append(backButton, node('span', step, 'interest-step'));
  content.append(sticky);
}

function renderInterestDescribe() {
  document.querySelector('[data-page-title]').textContent = 'Interests';
  content.classList.add('first-run-describe');
  content.append(node('h1', 'What do you want to follow?', 'describe-title'));
  content.append(node('p', 'Say it like you would tell a friend. Topics, what to skip, how often.', 'interest-intro'));
  const delivery = node('div', null, 'interest-delivery-note');
  delivery.append(
    node('p', 'Your daily 5-minute briefing is ready by 6 am ET.', 'interest-delivery-daily'),
    node('p', 'Ask for an episode on anything, any time.', 'interest-delivery-episodes'),
  );
  content.append(delivery);
  const form = node('form', null, 'interest-describe-form');
  form.id = 'interest-describe-form';
  const field = node('div', null, 'interest-word-field');
  const label = node('label', 'Add any topic or describe what matters to you', 'interest-text-label'); label.htmlFor = 'interest-words';
  const words = node('textarea'); words.id = 'interest-words'; words.name = 'words'; words.maxLength = 2000; words.rows = 6;
  words.placeholder = 'For example: I follow local transit and water policy. Skip celebrity coverage.';
  words.setAttribute('aria-label', 'Add any topic or describe what matters to you');
  words.setAttribute('aria-describedby', 'interest-guidance interest-counter');
  words.value = interestDraft?.profile.words || interestProfile?.words || '';
  field.append(label, words);
  form.append(field);
  const timeField = node('div', null, 'interest-time-field');
  const dailyTimeLabel = node('label', 'Preferred time', 'interest-time-label'); dailyTimeLabel.htmlFor = 'interest-daily-time';
  const dailyTime = node('input'); dailyTime.id = 'interest-daily-time'; dailyTime.name = 'daily-time'; dailyTime.type = 'time'; dailyTime.value = dailyTimeValue();
  dailyTime.setAttribute('aria-describedby', 'interest-time-note');
  dailyTime.addEventListener('change', () => { try { localStorage.setItem(DAILY_TIME_KEY, dailyTime.value); } catch { /* Keep this visit's choice. */ } });
  const timeNote = node('p', 'Your briefing is ready by 6 am ET. Your preferred time is saved on this device.', 'interest-time-note'); timeNote.id = 'interest-time-note';
  timeField.append(dailyTimeLabel, dailyTime, timeNote); form.append(timeField);
  const guidance = node('p', '', 'interest-guidance'); guidance.id = 'interest-guidance'; guidance.setAttribute('role', 'status'); guidance.setAttribute('aria-live', 'polite');
  form.append(guidance);
  const counter = node('p', '', 'interest-counter'); counter.id = 'interest-counter';
  form.append(counter);
  const examples = node('div', null, 'interest-examples');
  examples.append(node('p', 'Try a suggestion, then edit it in your own words.'));
  const examplesRow = node('div', null, 'interest-example-chips');
  const sampleWords = [
    ['Payments & banking', 'I follow payments and banking, especially real-time payment rules and savings rates.'],
    ['Tech & privacy', 'I follow technology and privacy, with practical changes explained clearly.'],
    ['Premier League', 'I follow Premier League matches and news on weekends.'],
  ];
  sampleWords.forEach(([title, sentence]) => {
    const chip = node('button', title, 'interest-example-chip'); chip.type = 'button';
    chip.addEventListener('click', () => { words.value = `${words.value.trim()}${words.value.trim() ? ' ' : ''}${sentence}`; words.dispatchEvent(new Event('input')); words.focus(); });
    examplesRow.append(chip);
  });
  examples.append(examplesRow); form.append(examples);
  const starterPreview = node('section', null, 'interest-starter-preview');
  starterPreview.setAttribute('aria-label', 'Starter profile preview');
  starterPreview.append(node('h2', 'Starter profile preview'));
  const starterSummary = node('p', 'Your topics will appear here as you type.', 'interest-starter-summary');
  starterSummary.setAttribute('role', 'status'); starterSummary.setAttribute('aria-live', 'polite');
  starterPreview.append(starterSummary);
  const starterTopics = node('ul', null, 'interest-starter-topics');
  starterPreview.append(starterTopics);
  const starterNote = node('p', '', 'interest-starter-note'); starterPreview.append(starterNote);
  const clarifyTopic = node('button', 'Add a little detail', 'interest-starter-clarify'); clarifyTopic.type = 'button'; clarifyTopic.hidden = true;
  clarifyTopic.addEventListener('click', () => { words.focus(); words.setSelectionRange(words.value.length, words.value.length); });
  starterPreview.append(clarifyTopic); form.append(starterPreview);
  const error = node('p', '', 'error interest-form-error'); error.setAttribute('role', 'alert'); form.append(error);
  error.textContent = interestInterpretError || '';
  const actions = node('div', null, 'interest-form-actions');
  const stickyActions = node('div', null, 'interest-sticky-actions');
  const interpretButton = node('button', 'Build my briefing', 'primary'); interpretButton.type = 'submit'; interpretButton.setAttribute('form', 'interest-describe-form');
  actions.append(interpretButton);
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (typeof SpeechRecognition === 'function') {
    const dictate = node('button', null, 'interest-dictate-icon'); dictate.type = 'button'; dictate.setAttribute('aria-label', 'Dictate'); dictate.title = 'Dictate';
    dictate.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8"/></svg>';
    dictate.addEventListener('click', () => { const recognition = new SpeechRecognition(); recognition.lang = 'en-US'; recognition.interimResults = false; recognition.onresult = event => { const spoken = event.results?.[0]?.[0]?.transcript || ''; words.value = `${words.value.trim()}${words.value.trim() ? ' ' : ''}${spoken}`; words.dispatchEvent(new Event('input')); }; recognition.onerror = () => { guidance.textContent = 'Dictation could not start. You can type instead.'; }; recognition.start(); });
    actions.append(dictate);
  }
  stickyActions.append(actions); content.append(stickyActions);
  const updateInputState = () => {
    const length = words.value.length;
    counter.textContent = `${length.toLocaleString()} / 2,000 characters`;
    counter.hidden = length < 1800;
    words.rows = Math.min(10, Math.max(6, (words.value.match(/\n/g) || []).length + 6));
    guidance.textContent = length > 2000 ? 'Please shorten this to 2,000 characters or fewer.' : '';
    const starter = interpret(words.value, { topics: pack.topics, minutes: state.durationMinutes });
    starterTopics.replaceChildren(); starterNote.textContent = ''; clarifyTopic.hidden = true;
    const count = starter.profile.topics.length;
    starterSummary.textContent = !words.value.trim() ? 'Your topics will appear here as you type.' : count
      ? `${count} topic${count === 1 ? '' : 's'} in your starter profile. Repeated mentions are combined.`
      : 'We have not picked out a topic yet. Try naming a field, place, team, or question.';
    starter.profile.topics.forEach(topic => {
      const row = node('li', null, 'interest-starter-topic');
      row.append(node('strong', topic.label), node('span', topic.scope)); starterTopics.append(row);
    });
    if (starter.profile.muted.length) {
      starterNote.textContent = `Not covering: ${starter.profile.muted.map(item => item.label).join(', ')}.`;
    }
    const unsupported = starter.profile.topics.find(topic => topic.id.startsWith('custom-'));
    if (unsupported) {
      starterNote.textContent = `${starterNote.textContent ? starterNote.textContent + ' ' : ''}${unsupported.label} is not in the sample edition yet. Add a little context if you mean a specific place, league, or field.`;
      clarifyTopic.hidden = false;
    }
  };
  words.addEventListener('input', () => { error.textContent = ''; updateInputState(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!words.value.trim()) { guidance.textContent = 'Add a few words first'; words.focus(); return; }
    if (words.value.trim().split(/\s+/).length < 3) { guidance.textContent = 'Tell me a little more, for example a topic and how often.'; words.focus(); return; }
    if (words.value.length > 2000) { updateInputState(); words.focus(); return; }
    error.textContent = ''; interestInterpretError = ''; interestDraft = { profile: { ...(interestProfile || {}), words: words.value } };
    navigate('#interests/build', { root: 'today', parentHash: '#interests/describe', firstRun: true });
    void runFirstBuild(words.value);
  });
  content.append(form);
  updateInputState();
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function buildChipLabel(topic) {
  const excluded = topic.exclude?.length && !topic.mustFollow;
  const suppliedLabel = String(topic.label || '').trim();
  const raw = String((excluded && topic.exclude?.[0]) || topic.sourcePhrase || topic.include?.[0] || suppliedLabel).replace(/[.!?].*$/, '');
  const concise = raw.replace(/^(?:i\s+)?(?:follow|care about|work in|want updates (?:on|about)|want to know about|want to skip|keep me posted on|track|skip|exclude)\s+/i, '')
    .replace(/\b(?:every day|daily|weekly|on weekends|weekends|in detail|headlines?)\b.*$/i, '')
    .replace(/[,:;]+$/, '').trim();
  const phraseLabel = concise.replace(/^(?:and|or)\s+/i, '').split(/\s+/).slice(0, 6).join(' ') || suppliedLabel;
  const label = topic.cadence === 'weekends' || !suppliedLabel || /^new topic$/i.test(suppliedLabel) || phraseLabel.toLocaleLowerCase() !== suppliedLabel.toLocaleLowerCase()
    ? phraseLabel : suppliedLabel;
  const readable = label ? label[0].toLocaleUpperCase() + label.slice(1) : 'Interest';
  if (excluded) return readable;
  return topic.cadence === 'weekends' && !/\bweekends\b/i.test(readable) ? `${readable} · weekends` : readable;
}

function renderInterestBuild() {
  content.replaceChildren();
  document.querySelector('[data-page-title]').textContent = 'Building';
  const profile = interestDraft?.profile || interestProfile;
  content.classList.add('interest-build-screen');
  content.append(node('h1', 'Building your briefing'));
  const from = node('section', null, 'build-profile'); from.append(node('h2', 'From your words'));
  if (profile?.words) from.append(node('p', profile.words, 'build-original-words'));
  const chips = node('div', null, 'build-interest-chips');
  const includedChips = [];
  const excludedChips = [];
  (profile?.topics || []).forEach(topic => {
    const chip = node('span', null, 'build-interest-chip');
    chip.append(node('span', buildChipLabel(topic)));
    const remove = node('button', '×', 'build-chip-remove'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${topic.label}`);
    remove.addEventListener('click', () => removeBuildTopic(topic.id));
    chip.append(remove); includedChips.push(chip);
  });
  const mutedByKey = new Map();
  for (const item of profile?.muted || []) {
    const label = String(item?.label || '').trim();
    if (label && !mutedByKey.has(label.toLocaleLowerCase())) mutedByKey.set(label.toLocaleLowerCase(), label);
  }
  const mutedLabels = [...mutedByKey.values()];
  mutedLabels.forEach(label => {
    const chip = node('span', null, 'build-interest-chip is-excluded');
    chip.append(node('span', label)); excludedChips.push(chip);
  });
  includedChips.forEach(chip => chips.append(chip));
  if (excludedChips.length) {
    const excluded = node('section', null, 'build-excluded-group'); excluded.append(node('h3', 'Not covering'));
    const excludedRow = node('div', null, 'build-interest-chips'); excludedChips.forEach(chip => excludedRow.append(chip)); excluded.append(excludedRow); chips.append(excluded);
  }
  from.append(chips);
  if (buildQuestion) { const question = node('p', buildQuestion, 'build-clarifying-question'); from.append(question); }
  const edit = node('button', 'Edit my words', 'build-edit-words'); edit.type = 'button';
  edit.addEventListener('click', () => { clearFirstRunBuild(); buildInProgress = false; navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true }); });
  from.append(edit); content.append(from);
  const progress = node('ol', null, 'build-progress'); progress.setAttribute('aria-label', 'Briefing progress');
  ['Understanding your words', 'Finding today’s stories', 'Writing your briefing'].forEach((label, index) => {
    const row = node('li', label, 'build-progress-step'); row.dataset.step = String(index); row.setAttribute('aria-current', index === 0 ? 'step' : 'false'); progress.append(row);
  });
  progress.setAttribute('role', 'status'); progress.setAttribute('aria-live', 'polite'); content.append(progress);
  if (!buildTimeoutHandle) {
    buildTimeoutHandle = setTimeout(() => {
      buildTimeoutHandle = null;
      if (location.hash === '#interests/build') {
        const note = node('p', 'Still working. It will be on Today when ready.', 'build-still-working');
        const go = node('button', 'Go to Today', 'button-link'); go.type = 'button'; go.addEventListener('click', () => navigate('#today', { root: 'today', parentHash: null })); note.append(go); content.append(note);
      }
    }, 20000);
  }
}

function setBuildStep(index, found = null, writingMessage = null) {
  const labels = ['Understanding your words', 'Finding today’s stories', 'Writing your briefing'];
  const steps = [...content.querySelectorAll('.build-progress-step')];
  steps.forEach((step, current) => {
    if (labels[current]) step.textContent = labels[current];
    step.setAttribute('aria-current', current === index ? 'step' : 'false');
    step.classList.toggle('is-complete', current < index);
  });
  if (index === 1 && found != null && steps[1]) steps[1].textContent = `Finding today’s stories (${found} found)`;
  if (index === 2 && steps[2]) {
    const match = String(writingMessage || '').match(/writing\s+story\s+(?:candidate\s+)?(\d+)\s+of\s+(\d+)/i);
    if (match) steps[2].textContent = `Writing story ${match[1]} of ${match[2]}`;
  }
}

async function runFirstBuild(words) {
  const attempt = ++buildAttempt;
  buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); buildQuestion = '';
  renderInterestBuild();
  try {
    const result = await interpretProfile(words, { topics: pack.topics, minutes: state.durationMinutes });
    if (attempt !== buildAttempt) return;
    if (result.fallbackError || !result.profile?.topics?.length) {
      interestDraft = { profile: { ...(interestProfile || defaultProfile(pack.topics)), words } };
      interestInterpretError = result.fallbackError ? 'I could not pick out topics. Try naming a few, like payment rules.' : 'I could not pick out topics. Try naming a few, like payment rules.';
      navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true }); return;
    }
    buildQuestion = result.question || '';
    interestProfile = saveProfile(result.profile); interestDraft = { profile: interestProfile };
    writeFirstRunBuild(interestProfile);
    sessionStorage.setItem(FIRST_EDITION_WELCOME_PENDING_KEY, profileBuildKey(interestProfile));
    renderInterestBuild(); setBuildStep(1);
    await startBuildGeneration(interestProfile, attempt, { firstRun: true });
  } catch {
    if (attempt !== buildAttempt) return;
    interestDraft = { profile: { ...(interestProfile || defaultProfile(pack.topics)), words } };
    interestInterpretError = 'I could not pick out topics. Try naming a few, like payment rules.';
    navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true });
  }
}

async function startBuildGeneration(profile, parentAttempt = null, options = {}) {
  const attempt = parentAttempt ?? ++buildAttempt;
  buildInProgress = true; topicsError = '';
  if (pendingTopicsUpdate()) setTopicsProgress('Saving your interests…');
  buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); buildStartedAt = Date.now();
  if (!options.inline) { renderInterestBuild(); setBuildStep(1); }
  try {
    try { await syncProfile(profile); } catch (error) { if (pendingTopicsUpdate()) throw error; }
    if (attempt !== buildAttempt) return;
    if (pendingTopicsUpdate()) setTopicsProgress('Finding stories…');
    let jobId = options.resumeJobId || null;
    if (!jobId) {
      const response = await fetch('/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile }) });
      if (!response.ok) throw new Error('generation_failed');
      const started = await response.json();
      const jobValue = started.job ?? started.job_id;
      jobId = typeof jobValue === 'string' || typeof jobValue === 'number' ? String(jobValue) : jobValue?.id || jobValue?.job_id;
      if (jobId && options.firstRun) writeFirstRunBuild(profile, jobId);
    }
    if (!jobId) throw new Error('generation_failed');
    // Vale permits a generation job to run for up to 180 seconds. Keep polling
    // through that window, with room for request and scheduling overhead.
    for (let poll = 0; poll < 210; poll += 1) {
      if (attempt !== buildAttempt) return;
      const jobResponse = await fetch(`/api/generate/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
      if (!jobResponse.ok) throw new Error('generation_failed');
      const job = await jobResponse.json();
      if (attempt !== buildAttempt) return;
      const progress = job.progress || {};
      const stepName = typeof progress === 'string' ? progress.toLowerCase()
        : String(progress.stage || progress.step || '').toLowerCase();
      const found = typeof progress === 'object'
        ? (progress.found ?? progress.stories_found ?? progress.story_count ?? progress.count)
        : job.found;
      if (/writing|write/.test(stepName)) setBuildStep(2, null, progress.message || job.message);
      else if (/scoring|scorecard|edition_writer/.test(stepName)) setBuildStep(2);
      else if (/finding|story|fetching|feed_fetch/.test(stepName) || found != null) setBuildStep(1, found);
      if (pendingTopicsUpdate()) setTopicsProgress(topicsProgressFor(stepName, found, progress, job));
      if (job.status === 'failed' || job.status === 'error') throw new Error('generation_failed');
      if (job.status === 'ready' || job.status === 'complete' || job.edition_ready) {
        setBuildStep(3);
        if (pendingTopicsUpdate()) setTopicsProgress('Checking your new briefing…');
        let loaded = await loadPack(true);
        const ready = typeof job.edition_ready === 'object' ? job.edition_ready : null;
        if (ready?.edition) loaded.editions = [ready.edition, ...loaded.editions.filter(item => item.id !== ready.edition.id)];
        if (Array.isArray(ready?.sources)) loaded.sources = [...new Map([...loaded.sources, ...ready.sources].map(item => [item.id, item])).values()];
        if (attempt !== buildAttempt) return;
        const pending = pendingTopicsUpdate();
        if (pending) {
          const fresh = ready?.edition;
          if (!fresh?.generated || pending.revisions.includes(fresh.id + ':' + fresh.revision_id)) throw new Error('stale_edition');
          // Only the completed job's daily may become Today, even if an older response has a later date.
          loaded.editions = [fresh, ...loaded.editions.filter(item => item.kind !== 'daily' || item.local_date < fresh.local_date)];
        }
        pack = validatePack(loaded);
        if (!currentEdition('daily')) throw new Error('generation_failed');
        buildInProgress = false; topicsUpdating = false; topicsProgress = ''; topicsError = '';
        localStorage.removeItem(TOPICS_PENDING_KEY);
        sessionStorage.removeItem(TOPICS_DRAFT_KEY); topicsDraft = null;
        localStorage.setItem('briefing:v1:built-profile-key', profileBuildKey(profile));
        if (sessionStorage.getItem(FIRST_EDITION_WELCOME_PENDING_KEY) === profileBuildKey(profile)) {
          sessionStorage.setItem(FIRST_EDITION_WELCOME_KEY, profileBuildKey(profile));
          sessionStorage.removeItem(FIRST_EDITION_WELCOME_PENDING_KEY);
        }
        localStorage.removeItem(BUILD_GENERAL_FALLBACK_KEY);
        if (options.firstRun) clearFirstRunBuild();
        buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); clearTimeout(buildTimeoutHandle); buildTimeoutHandle = null;
        if (!options.inline || options.returnToday || location.hash === '#today' || location.hash === '#topics') navigate('#today', { root: 'today', parentHash: null });
        return;
      }
      await wait(1000);
    }
    throw new Error('generation_timeout');
  } catch {
    if (attempt !== buildAttempt) return;
    buildInProgress = false;
    if (options.firstRun) clearFirstRunBuild();
    if (pendingTopicsUpdate()) {
      topicsUpdating = false; topicsProgress = '';
      topicsError = "We couldn't build it right now. Your last briefing is still here.";
      if (location.hash === '#topics' || location.hash === '#today') route();
      return;
    }
    if (await showGeneralEditionFallback(profile, attempt)) return;
    clearTimeout(buildTimeoutHandle); buildTimeoutHandle = null;
    buildNotice = 'Today’s stories did not load'; sessionStorage.setItem('briefing:build-error', buildNotice);
    navigate('#today', { root: 'today', parentHash: null });
  }
}

function removeBuildTopic(id) {
  const profile = interestProfile || interestDraft?.profile;
  const index = profile.topics.findIndex(topic => topic.id === id);
  if (index < 0) return;
  const value = profile.topics.splice(index, 1)[0];
  profile.muted = [...(profile.muted || []).filter(item => item.label !== value.label), { label: value.label }];
  interestProfile = saveProfile(profile); interestDraft = { profile: interestProfile };
  if (removedBuildTopic) clearTimeout(removedBuildTopic.timer);
  const entry = { topic: { value, index }, timer: null };
  entry.timer = setTimeout(() => { if (removedBuildTopic === entry) removedBuildTopic = null; }, 4000);
  removedBuildTopic = entry;
  showToast(`Removed ${value.label}.`, { actionLabel: 'Undo', onAction: () => {
    if (removedBuildTopic !== entry) return;
    clearTimeout(entry.timer); removedBuildTopic = null;
    interestProfile.topics.splice(index, 0, value);
    interestProfile.muted = (interestProfile.muted || []).filter(item => item.label !== value.label);
    updateInterestMuted(interestProfile); interestProfile = saveProfile(interestProfile); interestDraft = { profile: interestProfile }; renderInterestBuild(); void startBuildGeneration(interestProfile);
  } });
  renderInterestBuild(); void startBuildGeneration(interestProfile);
}

function updateDescribeInterpretationState() {
  const words = document.querySelector('#interest-words');
  if (!words) return;
  const guidance = document.querySelector('#interest-guidance');
  const counter = document.querySelector('#interest-counter');
  const submit = document.querySelector('.interest-form-actions button[type="submit"]');
  const cancel = document.querySelector('.interest-cancel');
  const error = document.querySelector('.interest-form-error');
  const length = words.value.length;
  if (counter) counter.textContent = `${length.toLocaleString()} / 2,000 characters`;
  if (counter) counter.hidden = length < 1800;
  if (guidance) guidance.textContent = interestInterpretBusy ? 'Interpreting…' : interestInterpretStatus;
  if (submit) submit.disabled = false;
  words.disabled = interestInterpretBusy;
  if (cancel) cancel.hidden = !interestInterpretBusy;
  if (error) error.textContent = interestInterpretError || '';
}

function cancelInterestInterpretation() {
  interestInterpretController?.abort();
  interestInterpretController = null;
  interestInterpretBusy = false;
  interestInterpretError = null;
  interestInterpretStatus = 'Canceled. Your words are still here.';
  if (location.hash === '#interests/confirm') renderInterestConfirm();
  else updateDescribeInterpretationState();
}

async function runInterestInterpretation(words, destination, localOnly = false) {
  interestInterpretController?.abort();
  const controller = new AbortController();
  interestInterpretController = controller;
  interestInterpretBusy = true;
  interestInterpretError = null;
  interestInterpretStatus = 'Interpreting…';
  if (destination === 'confirm') renderInterestConfirm();
  else updateDescribeInterpretationState();
  try {
    const result = await interpretProfile(words, {
      topics: pack.topics,
      minutes: state.durationMinutes,
      signal: controller.signal,
      localOnly,
    });
    if (interestInterpretController !== controller) return;
    interestDraft = result;
    interestInterpretError = result.fallbackError || null;
    interestInterpretStatus = '';
    interestInterpretBusy = false;
    interestInterpretController = null;
    if (destination === 'describe') navigateInterest('#interests/confirm');
    else renderInterestConfirm();
  } catch (error) {
    if (interestInterpretController !== controller) return;
    interestInterpretController = null;
    interestInterpretBusy = false;
    interestInterpretError = 'Something went wrong while interpreting your words. You can retry or use basic matching.';
    interestInterpretStatus = '';
    if (destination === 'confirm') renderInterestConfirm();
    else updateDescribeInterpretationState();
  }
}

function updateInterestMuted(profile) {
  const labels = new Map();
  for (const item of profile.muted || []) {
    const label = String(item?.label || '').trim();
    if (label) labels.set(label.toLocaleLowerCase(), { label });
  }
  profile.muted = [...labels.values()];
}

function persistInterestDraft(profile) {
  updateInterestMuted(profile);
  interestProfile = saveProfile(profile);
}

function renderInterestConfirm() {
  content.replaceChildren();
  document.querySelector('[data-page-title]').textContent = 'Interests';
  if (!interestDraft) {
    const words = interestProfile?.words || '';
    interestDraft = interpret(words, { topics: pack.topics, minutes: state.durationMinutes });
  }
  const draft = interestDraft;
  renderInterestTopbar('2 of 2');
  content.append(node('p', 'YOUR BRIEFING', 'eyebrow'), node('h1', 'What I heard'));
  content.append(node('p', 'These are suggestions from your words. Change anything, or edit the words and start again.', 'interest-intro'));
  if (interestInterpretBusy) {
    const progress = node('div', 'Interpreting…', 'interest-progress');
    progress.setAttribute('role', 'status'); progress.setAttribute('aria-live', 'polite');
    const cancel = node('button', 'Cancel', 'interest-cancel'); cancel.type = 'button'; cancel.addEventListener('click', cancelInterestInterpretation);
    progress.append(cancel); content.append(progress);
  }
  if (interestInterpretError) {
    const notice = node('aside', null, 'interest-interpreter-error');
    notice.setAttribute('role', 'alert');
    notice.append(node('p', interestInterpretError));
    const retry = node('button', 'Retry', 'interest-retry'); retry.type = 'button';
    retry.addEventListener('click', () => void runInterestInterpretation(draft.profile.words, 'confirm'));
    const basic = node('button', 'Use basic matching', 'interest-basic'); basic.type = 'button';
    basic.addEventListener('click', () => void runInterestInterpretation(draft.profile.words, 'confirm', true));
    notice.append(retry, basic); content.append(notice);
  }
  if (draft.flags?.length) {
    const flags = node('aside', null, 'interest-interpreter-flags');
    flags.setAttribute('role', 'status');
    flags.append(node('p', `Interpretation note: ${draft.flags.join('; ')}`));
    content.append(flags);
  }
  const quote = node('blockquote', draft.profile.words || 'No words entered yet.', 'interest-original-words');
  content.append(quote);
  const list = node('div', null, 'interest-topic-list');
  for (const topic of draft.profile.topics) {
    const card = node('section', null, 'interest-topic');
    const title = node('div', null, 'interest-topic-title');
    const heading = node('div', null, 'interest-topic-heading');
    heading.append(node('h2', topic.label));
    title.append(heading, node('button', 'Remove', 'interest-remove')); title.lastChild.type = 'button';
    title.lastChild.setAttribute('aria-label', `Remove ${topic.label}`);
    title.lastChild.addEventListener('click', () => {
      draft.profile.topics = draft.profile.topics.filter(item => item.id !== topic.id);
      persistInterestDraft(draft.profile); renderInterestConfirm();
    });
    card.append(title, node('p', `You said: “${topic.sourcePhrase}”`, 'interest-source-phrase'));
    card.append(node('p', topic.scope, 'interest-scope'));
    const include = !(topic.exclude.length && !topic.mustFollow);
    const choices = node('div', null, 'interest-include-choices');
    for (const [value, text] of [[true, 'Include'], [false, 'Skip']]) {
      const button = node('button', text, value ? 'interest-choice' : 'interest-choice'); button.type = 'button';
      button.setAttribute('aria-pressed', String(include === value));
      button.addEventListener('click', () => {
        topic.include = value ? [topic.sourcePhrase] : [];
        topic.exclude = value ? [] : [topic.sourcePhrase];
        if (!value) topic.mustFollow = false;
        persistInterestDraft(draft.profile); renderInterestConfirm();
      });
      choices.append(button);
    }
    card.append(choices);
    const depths = node('div', null, 'interest-depth-choices');
    depths.setAttribute('role', 'group'); depths.setAttribute('aria-label', `${topic.label} depth`);
    for (const [value, text] of [['headline', 'Headlines'], ['standard', 'Standard'], ['deeper', 'Deeper']]) {
      const button = node('button', text, 'interest-depth-chip'); button.type = 'button';
      button.setAttribute('aria-pressed', String(topic.depth === value));
      button.addEventListener('click', () => { topic.depth = value; renderInterestConfirm(); });
      depths.append(button);
    }
    card.append(depths);
    const cadence = node('p', `Cadence · ${topic.cadence === 'change' ? 'Only when it changes' : topic.cadence === 'weekends' ? 'Weekends' : topic.cadence === 'weekly' ? 'Weekly' : 'Every day'}`, 'interest-cadence');
    card.append(cadence);
    if (topic.mustFollow) card.append(node('p', 'Must follow', 'interest-must-follow'));
    list.append(card);
  }
  content.append(list);
  if (draft.profile.muted.length) {
    const muted = node('section', null, 'interest-muted-list'); muted.append(node('h2', 'Muted topics'));
    for (const item of draft.profile.muted) {
      const row = node('div', null, 'interest-muted-row'); row.append(node('span', item.label));
      const restore = node('button', 'Include', 'interest-choice'); restore.type = 'button';
      restore.addEventListener('click', () => {
        const topic = draft.profile.topics.find(candidate => candidate.label === item.label);
        if (topic) { topic.exclude = []; topic.include = [topic.sourcePhrase]; }
        persistInterestDraft(draft.profile); renderInterestConfirm();
      });
      row.append(restore); muted.append(row);
    }
    content.append(muted);
  }
  if (draft.question) {
    const question = node('aside', null, 'interest-question'); question.append(node('h2', 'One question'), node('p', draft.question)); content.append(question);
  }
  const edit = node('button', 'Edit words', 'interest-edit-words'); edit.type = 'button';
  edit.addEventListener('click', () => navigateInterest('#interests/describe'));
  content.append(edit);
  const save = node('button', 'Save interests', 'primary interest-save'); save.type = 'button';
  save.addEventListener('click', () => {
    persistInterestDraft(draft.profile);
    interestDraft = null;
    navigate(interestOriginHash());
  });
  content.append(save);
}

function formatLastUpdated(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return 'Updated not yet';
  const parts = new Intl.DateTimeFormat('en-US', {
    hour: 'numeric', minute: '2-digit', hour12: true,
  }).formatToParts(date);
  const hour = parts.find(part => part.type === 'hour')?.value;
  const minute = parts.find(part => part.type === 'minute')?.value;
  const period = parts.find(part => part.type === 'dayPeriod')?.value?.toLowerCase();
  return hour && minute && period ? `Updated ${hour}:${minute} ${period}` : 'Updated unavailable';
}

async function refreshTodayRunSummary(edition, article) {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) return;
    const status = await response.json();
    const report = status?.daily_run;
    const brief = report?.daily_briefing;
    if (!article.isConnected) return;
    if (dailyRunFailedToday(status) && report?.local_date !== edition.local_date) {
      article.prepend(todayRunFailurePanel());
      return;
    }
    if (!report || report.local_date !== edition.local_date) return;
    const audioSeconds = Number(report.daily_audio?.duration_seconds);
    const briefSeconds = Number(brief?.duration_seconds);
    const spokenWords = Number(brief?.spoken_words);
    const seconds = Number.isFinite(audioSeconds) && audioSeconds > 0
      ? audioSeconds
      : Number.isFinite(briefSeconds) && briefSeconds > 0
        ? briefSeconds
        : Number.isFinite(spokenWords) && spokenWords > 0
          ? Math.round(spokenWords / 150 * 60) : 0;
    if (!seconds || seconds > 60 * 60) return;

    const minutes = Math.max(1, Math.round(seconds / 60));
    const durationLabel = ` · about ${minutes} min`;
    const summary = article.querySelector('.today-run-meta');
    if (summary) {
      const storyCount = (Array.isArray(edition.sections) ? edition.sections : []).filter(section => section.kind !== 'link').length;
      summary.textContent = `${storyCount} ${storyCount === 1 ? 'story' : 'stories'} · about ${minutes} min`;
    }
    const listen = article.querySelector('.today-listen-pill');
    if (listen) {
      listen.dataset.listenEstimate = `${minutes} min`;
      listen.dataset.durationLabel = durationLabel;
      const label = listen.querySelector('.mode-label');
      if (label && (label.textContent === 'Play' || label.textContent.startsWith('Play ·') || label.textContent.startsWith('Play about'))) label.textContent = `Play${durationLabel}`;
      listen.setAttribute('aria-label', `Play today's briefing, about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`);
    }
  } catch {
    // The edition remains useful if the optional run summary is unavailable.
  }
}

function correctionPassage(content, correction, targetType) {
  if (targetType === 'story') {
    const section = content?.sections?.find(item => item.id === correction.target_id);
    if (!section) return { title: 'Daily story', text: '' };
    const paragraphs = String(section.body || '').split(/\n\s*\n/);
    const paragraph = paragraphs[correction.paragraph_index] || '';
    if (!Number.isInteger(correction.sentence_index)) return { title: section.heading, text: paragraph };
    const sentences = paragraph.match(/\S.*?(?:[.!?](?=\s|$)|$)/g) || [];
    return { title: section.heading, text: sentences[correction.sentence_index] || '' };
  }
  const episode = content?.episode || content;
  const chapter = episode?.chapters?.find(item => item.id === correction.chapter_id);
  const sentence = chapter?.sentences?.[correction.sentence_index];
  return { title: episode?.title || episode?.topic_label || 'Episode', text: sentence?.text || '' };
}

async function correctionRevision(targetType, targetId, revisionId) {
  if (!targetId || !revisionId) return null;
  const response = await fetch(`/api/revisions/${targetType === 'story' ? 'edition' : 'episode'}/${encodeURIComponent(targetId)}/${encodeURIComponent(revisionId)}`, { cache: 'no-store' });
  if (!response.ok) return null;
  const result = await response.json().catch(() => ({}));
  return result[targetType === 'story' ? 'edition' : 'episode'] || null;
}

async function correctionViewModels(records) {
  return Promise.all(records.map(async correction => {
    const targetType = correction.target_type === 'episode' ? 'episode' : 'story';
    const targetId = targetType === 'story' ? correction.edition_id : correction.target_id;
    const [beforeRevision, afterRevision] = await Promise.all([
      correctionRevision(targetType, targetId, correction.previous_revision_id),
      correctionRevision(targetType, targetId, correction.revision_id),
    ]);
    const before = correctionPassage(beforeRevision, correction, targetType);
    const after = correctionPassage(afterRevision, correction, targetType);
    const embedded = (afterRevision?.corrections || []).find(item => item.id === correction.id);
    const afterText = embedded?.current_text || after.text;
    const title = after.title || before.title;
    const targetHash = targetType === 'story'
      ? `#edition/${encodeURIComponent(afterRevision?.kind || 'daily')}/${encodeURIComponent(correction.edition_id)}/${encodeURIComponent(correction.target_id)}`
      : `?episodes=1#episode/${encodeURIComponent(correction.target_id)}`;
    return { ...correction, target_type: targetType, title, target_hash: targetHash, before: before.text, after: afterText };
  }));
}

async function renderCorrectionsPage() {
  const renderError = () => renderCorrections(content, [], {
    error: "Corrections couldn't load. Please try again.", onRetry: renderCorrectionsPage,
  });
  renderCorrections(content, [], { loading: true });
  try {
    const response = await fetch('/api/corrections', { cache: 'no-store' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !Array.isArray(result.corrections)) throw new Error('corrections_unavailable');
    const items = await correctionViewModels(result.corrections);
    if (location.hash === '#corrections') renderCorrections(content, items);
  } catch {
    if (location.hash === '#corrections') renderError();
  }
}

async function refreshLastUpdated(line) {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw Error('status unavailable');
    const status = await response.json();
    line.textContent = formatLastUpdated(status.last_run_at);
  } catch {
    line.textContent = 'Updated unavailable';
  }
}


function revealStoryMore(target) {
  const more = target?.closest('.story-more'); if (!more) return; more.hidden = false;
  const toggle = more.parentElement.querySelector('.story-more-toggle'); toggle?.setAttribute('aria-expanded', 'true'); if (toggle) toggle.innerHTML = 'Less <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m7 10 5 5 5-5"/></svg>';
}

function renderMissing() {
  const page = node('section', null, 'unavailable-page');
  page.setAttribute('aria-labelledby', 'unavailable-title');
  const heading = node('h1', 'This page is unavailable');
  heading.id = 'unavailable-title';
  page.append(heading, node('p', 'The link may have moved or expired. You can head back to Today and keep browsing.', 'unavailable-copy'));
  page.append(link('Return to today', '#today', 'button-link primary unavailable-action'));
  content.append(page);
}

function renderEpisodeRequestPage() {
  renderOnDemandScreen({
    node, document, content, draft: episodeDraft,
    getRequestState: () => ({ busy: episodeRequestBusy, blocked: episodeRequestBlocked, error: episodeRequestError }),
    setRequestError: value => { episodeRequestError = value; },
    setRequestBlocked: value => { episodeRequestBlocked = value; },
    rememberLength: rememberEpisodeLength,
    startEpisode: startEpisodeJob,
    loadEpisodeCards,
    defaultMode: EPISODE_MODES[0],
  });
}

async function loadEpisodeCards(cards, examples = null, prompt = null, { earlier = false } = {}) {
  cards.setAttribute('aria-busy', 'true');
  const earlierAsk = earlier ? link('Ask for a briefing', '#ask-episode', 'earlier-ask-link') : null;
  const skeleton = episodeCardsSkeleton();
  const status = node('p', 'Loading your on-demand briefings…', 'sr-only'); status.setAttribute('role', 'status');
  cards.replaceChildren(...(earlierAsk ? [earlierAsk] : []), skeleton, status);
  try {
    const response = await fetch('/api/episodes', { cache: 'no-store' });
    if (response.status === 404) {
      if (!cards.isConnected) return;
      cards.setAttribute('aria-busy', 'false');
      if (examples) examples.hidden = false;
      const empty = node('p', earlier ? 'No earlier episodes yet. Briefings you request will appear here.' : 'No on-demand briefings yet. Your first one will appear here.', 'episode-empty-copy');
      cards.replaceChildren(...(earlierAsk ? [earlierAsk] : []), empty);
      return;
    }
    if (!response.ok) throw new Error();
    const result = await response.json(); if (!cards.isConnected) return;
    if (!result || typeof result !== 'object' || !Array.isArray(result.jobs)) throw new Error('invalid_episode_list');
    const jobs = result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string');
    if (result.jobs.length && !jobs.length) throw new Error('invalid_episode_list');
    cards.setAttribute('aria-busy', 'false');
    if (examples) examples.hidden = false;
    cards.replaceChildren();
    if (!jobs.length) {
      cards.append(node('p', earlier ? 'No earlier episodes yet. Briefings you request will appear here.' : 'No on-demand briefings yet. Your first one will appear here.', 'episode-empty-copy'));
    }
    if (earlierAsk) cards.prepend(earlierAsk);
    for (const job of jobs) {
      const status = episodeHistoryState(job); const id = job.job_id || job.id;
      const card = link('', status.key === 'ready' ? `#episode/${encodeURIComponent(id)}` : `#episode-making/${encodeURIComponent(id)}`, 'ask-episode-card');
      card.append(node('h3', job.episode?.title || job.topic || job.title || 'Your briefing'), node('p', episodeLengthCopy(job), 'episode-length-badge'));
      const state = node('span', null, `episode-status-chip episode-status-${status.key}`);
      const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true'); ring.dataset.check = status.ring === 'done' ? '✓' : status.ring === 'failed' ? '!' : '';
      state.append(ring, node('span', status.key === 'ready' ? episodeHasReadyAudio(job) ? 'Ready · Listen' : 'Transcript ready' : status.key === 'failed' ? 'Could not finish · Retry' : 'Making'));
      card.append(state); cards.append(card);
    }
    episodeHistorySnapshot = { result, jobs, corrections: [] };
  } catch {
    if (!cards.isConnected) return;
    cards.setAttribute('aria-busy', 'false');
    const empty = node('div', null, 'episode-list-error'); const message = node('p', earlier ? 'Earlier episodes could not be loaded. Try again in a moment.' : 'Your episodes could not be loaded. Try again in a moment.');
    const retry = node('button', 'Try again', 'episode-list-retry'); retry.addEventListener('click', () => loadEpisodeCards(cards, examples, prompt, { earlier }));
    empty.append(message, retry); cards.replaceChildren(empty);
  }
}

function episodeCardsSkeleton() {
  const list = node('div', null, 'episode-list-skeleton'); list.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 2; index++) {
    const card = node('div', null, 'episode-card-skeleton');
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-title'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-title episode-skeleton-title-short'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-meta'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-state'));
    list.append(card);
  }
  return list;
}

function episodeProfileTopic(episode, profile) {
  const source = String(episode.topic_label || episode.topic || episode.asked || episode.title || '')
    .replace(/\s+/g, ' ').trim().slice(0, 500);
  if (!source) throw new Error('This episode has no topic to add.');
  const interpreted = interpret(`Follow ${source}`, { topics: pack?.topics || [], minutes: profile.minutes });
  const topic = interpreted.profile.topics[0];
  if (!topic) throw new Error('Could not identify this episode topic.');
  return {
    ...topic,
    label: topic.label.slice(0, 60),
    scope: topic.scope.slice(0, 180),
    include: topic.include.slice(0, 8).map(value => value.slice(0, 240)),
    exclude: topic.exclude.slice(0, 8).map(value => value.slice(0, 240)),
    sourcePhrase: source.slice(0, 500),
  };
}

function isEpisodeInDailyBriefing(episode) {
  const profile = interestProfile;
  if (!profile) return false;
  const topic = episodeProfileTopic(episode, profile);
  const muted = new Set((profile.muted || []).map(item => item.label.toLocaleLowerCase()));
  return profile.topics.some(item => (item.id === topic.id || item.label.toLocaleLowerCase() === topic.label.toLocaleLowerCase()) && !muted.has(item.label.toLocaleLowerCase()));
}

async function setEpisodeDailyBriefing(episode, included) {
  const current = interestProfile || { version: 1, words: '', minutes: 12, background: null, topics: [], muted: [] };
  const topic = episodeProfileTopic(episode, current);
  const key = topic.label.toLocaleLowerCase();
  const muted = (current.muted || []).filter(item => item.label.toLocaleLowerCase() !== key);
  let topics = [...current.topics];
  const existingIndex = topics.findIndex(item => item.id === topic.id || item.label.toLocaleLowerCase() === key);
  if (included) {
    if (existingIndex < 0 && topics.length >= 12) throw new Error('Your daily briefing already has 12 topics. Remove one in Topics first.');
    if (existingIndex < 0) {
      let id = topic.id;
      let suffix = 2;
      while (topics.some(item => item.id === id)) id = `${topic.id}-${suffix++}`;
      topics.push({ ...topic, id });
    }
  } else if (existingIndex >= 0) {
    topics = topics.filter((_, index) => index !== existingIndex);
  }
  const updated = validateProfile({ ...current, topics, muted });
  try { await syncProfile(updated); }
  catch { throw new Error('Could not update your daily briefing. Try again.'); }
  interestProfile = saveProfile(updated, { server: false });
}

async function renderEpisodeRoute(routeName) {
  const loading = node('section', null, 'episode-loading');
  loading.setAttribute('role', 'status'); loading.append(node('h1', 'Loading episode'), node('div', null, 'edition-skeleton'));
  content.replaceChildren(loading);
  try {
    if (routeName === 'episodes' && new URLSearchParams(location.search).get('episodes') !== '1') {
      const [response, correctionsResponse] = await Promise.all([
        fetch('/api/episodes', { cache: 'no-store' }),
        fetch('/api/corrections?target_type=episode', { cache: 'no-store' }).catch(() => null),
      ]);
      const result = await response.json().catch(() => ({}));
      const correctionData = await correctionsResponse?.json().catch(() => ({})) || {};
      if (location.hash !== '#episodes') return;
      const jobs = Array.isArray(result?.jobs) ? result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string') : [];
      const listAvailable = response.ok && Array.isArray(result?.jobs) && (!result.jobs.length || jobs.length > 0);
      renderEpisodeHistory(result, listAvailable, Array.isArray(correctionData.corrections) ? correctionData.corrections : []);
      return;
    }
    if (routeName.startsWith('episode-making/')) {
      const id = routeName.slice('episode-making/'.length);
      let job = episodeHistorySnapshot?.jobs.find(item => (item.job_id || item.id) === id) || null;
      if (!job && /^[a-f0-9]{32}$/.test(id)) {
        const response = await fetch(`/api/episodes/${encodeURIComponent(id)}`, { cache: 'no-store' });
        job = await response.json().catch(() => null);
        if (!response.ok || !job || typeof job !== 'object' || Array.isArray(job) || !(job.status || job.stage || job.topic)) job = null;
      }
      if (location.hash !== `#${routeName}`) return;
      if (job) renderEpisodeMakingFromHistory(job); else renderEpisodeRouteFailure('Episode unavailable', 'This episode could not be loaded right now.');
      return;
    }
    if (routeName.startsWith('episode/')) {
      const id = routeName.slice('episode/'.length);
      if (/^[a-f0-9]{32}$/.test(id)) {
        const response = await fetch(`/api/episodes/${encodeURIComponent(id)}`, { cache: 'no-store' });
        const result = await response.json().catch(() => ({}));
        if (location.hash !== `#${routeName}`) return;
        if (!response.ok || result.status !== 'done' || !result.episode || typeof result.episode !== 'object' || Array.isArray(result.episode) || !Array.isArray(result.episode.chapters)) {
          renderEpisodeRouteFailure('Episode unavailable', 'This episode could not be loaded right now. Try again in a moment.');
          return;
        }
        const audioReady = episodeHasReadyAudio(result);
        renderEpisodePage(content, { ...result.episode, kind: 'episode', id: result.job_id || id,
          topic_label: result.topic || result.episode.topic_label, asked: result.topic || result.episode.asked,
          duration_minutes: result.episode.duration_minutes, requested_minutes: result.length || result.episode.requested_minutes,
          audio_status: audioReady ? 'recorded' : 'pending',
          made_date: String(result.finished_at || result.updated_at || '').slice(0, 10), audio_recorded: audioReady }, {
          onListen: episode => player.playRecording(episode),
          compactSources: true,
          inDailyBriefing: isEpisodeInDailyBriefing({ ...result.episode, topic_label: result.topic || result.episode.topic_label }),
          onDailyBriefingChange: setEpisodeDailyBriefing,
        });
        return;
      }
    }
    if (routeName.startsWith('episode/') && new URLSearchParams(location.search).get('episodes') !== '1') {
      if (location.hash === `#${routeName}`) renderMissing();
      return;
    }
    if (!episodeFixturePromise) episodeFixturePromise = fetch('/dev/tests/fixtures/episodes.json', { cache: 'no-store' }).then(response => {
      if (!response.ok) throw new Error('fixture unavailable');
      return response.json();
    }).then(data => {
      if (!Array.isArray(data.episodes)) throw new Error('invalid episode fixture');
      return data.episodes;
    }).catch(error => { episodeFixturePromise = null; throw error; });
    const episodes = await episodeFixturePromise;
    if (location.hash !== `#${routeName}`) return;
    if (routeName === 'episodes') {
      episodeDeckCleanup = renderEpisodeDeck(content, episodes, {
        onClose: () => navigate('#today', { root: 'today', parentHash: null, returnFocus: { type: 'nav', id: 'today' } }),
        onOpen: id => navigate(`#episode/${encodeURIComponent(id)}`, { root: 'today', parentHash: '#episodes', parentLabel: 'Episodes' }),
        onListen: item => player.playRecording({ ...item, kind: 'episode' }),
      });
      return;
    }
    const id = routeName.slice('episode/'.length);
    const episode = episodes.find(item => item.id === id);
    if (episode) renderEpisodePage(content, { ...episode, kind: 'episode' }, {
      onListen: item => player.playRecording(item),
      inDailyBriefing: isEpisodeInDailyBriefing(episode),
      onDailyBriefingChange: setEpisodeDailyBriefing,
    });
    else renderMissing();
  } catch {
    if (location.hash !== `#${routeName}`) return;
    renderEpisodeRouteFailure('Episodes are unavailable', 'This screen could not be loaded right now. Try again in a moment.');
  }
}

function renderEpisodeRouteFailure(title, message) {
  const page = node('section', null, 'episode-route-error');
  page.setAttribute('role', 'status');
  page.append(node('h1', title), node('p', message));
  const retry = node('button', 'Try again', 'episode-route-retry'); retry.type = 'button'; retry.addEventListener('click', route);
  page.append(retry, link('Return to today', '#today', 'button-link'));
  content.replaceChildren(page);
}

function episodeHistoryState(job) {
  const status = String(job.status || job.stage || 'queued').toLowerCase();
  if (['done', 'ready', 'complete', 'completed'].includes(status)) return { key: 'ready', label: 'Ready', ring: 'done' };
  if (['failed', 'error'].includes(status)) return { key: 'failed', label: 'Failed', ring: 'failed' };
  if (['cancelled', 'canceled'].includes(status)) return { key: 'cancelled', label: 'Cancelled', ring: 'cancelled' };
  return { key: 'making', label: 'Making', ring: 'active' };
}

function episodeHistoryDismissed() {
  try {
    const value = JSON.parse(localStorage.getItem('briefing:v1:episode-list-dismissed') || '[]');
    return Array.isArray(value) ? value.filter(id => typeof id === 'string').slice(0, 100) : [];
  } catch { return []; }
}

function saveEpisodeHistoryDismissed(ids) {
  try { localStorage.setItem('briefing:v1:episode-list-dismissed', JSON.stringify([...new Set(ids)].slice(-100))); }
  catch { /* The visible list still works for this visit if storage is blocked. */ }
}

function renderEpisodeMakingFromHistory(job) {
  const page = node('section', null, 'episode-history-making');
  page.append(link('‹ Episodes', '#episodes', 'episode-back'));
  const view = episodeHistoryState(job);
  const heading = view.key === 'failed' ? 'This episode could not be completed' : view.key === 'cancelled' ? 'This episode was cancelled' : 'Your episode is being made';
  page.append(node('h1', heading));
  if (job.topic) page.append(node('p', job.topic, 'episode-topic'));
  const chip = node('span', null, `episode-status-chip episode-status-${view.key}`);
  chip.dataset.state = view.ring;
  const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
  ring.dataset.check = view.ring === 'done' ? '✓' : view.ring === 'failed' ? '!' : view.ring === 'cancelled' ? '–' : '';
  chip.append(ring, node('span', view.label));
  page.append(chip);
  const detail = view.key === 'making' ? (job.stage || job.status || 'queued').replaceAll('_', ' ') :
    view.key === 'failed' ? 'You can ask for another episode when you’re ready.' : 'This request will stay in your episode history.';
  page.append(node('p', detail, 'episode-job-card-status'));
  content.replaceChildren(page);
}

function renderEpisodeHistory(result, available, corrections = []) {
  const jobs = Array.isArray(result?.jobs) ? result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string') : [];
  episodeHistorySnapshot = { result: result && typeof result === 'object' ? result : {}, corrections, jobs };
  document.querySelector('[data-page-title]').textContent = '';
  const page = node('section', null, 'episode-list-page');
  page.append(node('h1', 'Episodes'));
  if (!available) {
    page.append(node('p', 'Your episode list is not available right now.', 'episode-list-empty'));
    const retry = node('button', 'Try again', 'episode-list-retry'); retry.type = 'button'; retry.addEventListener('click', () => void renderEpisodeRoute('episodes'));
    page.append(retry);
    content.replaceChildren(page); return;
  }
  const safeResult = episodeHistorySnapshot.result;
  if (jobs.length) page.append(link('Ask for an episode', '#ask-episode', 'button-link'));
  if (Number.isInteger(safeResult.left_today)) page.append(node('p', `${safeResult.left_today} left today`, 'episode-list-allowance'));
  const dismissed = episodeHistoryDismissed();
  const correctionIDs = new Set(corrections.filter(item => item?.target_type === 'episode').map(item => item.target_id).filter(Boolean));
  const visibleJobs = jobs.filter(job => !dismissed.includes(job.job_id || job.id));
  const byNewest = [...visibleJobs].sort((a, b) => String(b.created_at || b.updated_at || b.finished_at || '').localeCompare(String(a.created_at || a.updated_at || a.finished_at || '')));
  const filters = [
    ['all', 'All'], ['making', 'Making'], ['ready', 'Ready'], ['saved', 'Saved'], ['corrected', 'Corrected'],
  ];
  const filterBar = node('div', null, 'episode-history-filters');
  filterBar.setAttribute('role', 'group'); filterBar.setAttribute('aria-label', 'Filter episodes');
  for (const [key, label] of filters) {
    const button = node('button', label, 'episode-history-filter'); button.type = 'button';
    button.dataset.episodeFilter = key;
    button.setAttribute('aria-pressed', String(episodeHistoryFilter === key));
    button.addEventListener('click', () => { episodeHistoryFilter = key; renderEpisodeHistory(result, available, corrections); content.querySelector(`[data-episode-filter="${key}"]`)?.focus({ preventScroll: true }); });
    filterBar.append(button);
  }
  page.append(filterBar);
  const searchLabel = node('label', 'Search episodes', 'episode-history-search-label');
  const search = node('input'); search.type = 'search'; search.placeholder = 'Search titles or topics'; search.value = episodeHistorySearch; search.setAttribute('aria-label', 'Search episodes');
  search.addEventListener('input', () => { episodeHistorySearch = search.value; renderEpisodeHistory(result, available, corrections); const next = content.querySelector('.episode-history-search-label input'); next?.focus(); next?.setSelectionRange(episodeHistorySearch.length, episodeHistorySearch.length); });
  searchLabel.append(search); page.append(searchLabel);
  const filtered = byNewest.filter(job => {
    const id = job.job_id || job.id;
    const status = episodeHistoryState(job);
    const corrected = Boolean(job.updated || job.corrected || job.episode?.updated || correctionIDs.has(id));
    const saved = state.savedIds.includes(id) || (job.episode?.id && state.savedIds.includes(job.episode.id));
    if (episodeHistoryFilter === 'making' && status.key !== 'making') return false;
    if (episodeHistoryFilter === 'ready' && status.key !== 'ready') return false;
    if (episodeHistoryFilter === 'saved' && !saved) return false;
    if (episodeHistoryFilter === 'corrected' && !corrected) return false;
    const query = episodeHistorySearch.trim().toLocaleLowerCase();
    if (query && ![job.topic, job.asked, job.episode?.title, job.episode?.asked, job.episode?.topic_label].some(value => String(value || '').toLocaleLowerCase().includes(query))) return false;
    return true;
  });
  const count = node('p', `${filtered.length} ${filtered.length === 1 ? 'episode' : 'episodes'}`, 'episode-history-count');
  count.setAttribute('aria-live', 'polite'); page.append(count);
  const list = node('div', null, 'episode-job-list');
  for (const job of filtered) {
    const id = job.job_id || job.id;
    const status = episodeHistoryState(job);
    const episode = job.episode || {};
    const corrected = Boolean(job.updated || job.corrected || episode.updated || correctionIDs.has(id));
    const saved = state.savedIds.includes(id) || (episode.id && state.savedIds.includes(episode.id));
    const card = node('article', null, 'episode-job-card');
    card.dataset.jobId = id || '';
    const title = episode.title || job.topic || 'Episode request';
    const hash = status.key === 'ready' ? `#episode/${encodeURIComponent(id)}` : `#episode-making/${encodeURIComponent(id)}`;
    const titleLink = link(title, hash, 'episode-job-title');
    const heading = node('h2'); heading.append(titleLink); card.append(heading);
    if (episode.topic_label && episode.topic_label !== title) card.append(node('p', episode.topic_label, 'episode-job-topic'));
    const chip = node('span', null, `episode-status-chip episode-status-${status.key}`); chip.dataset.state = status.ring;
    const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
    ring.dataset.check = status.ring === 'done' ? '✓' : status.ring === 'failed' ? '!' : status.ring === 'cancelled' ? '–' : '';
    chip.append(ring, node('span', status.label));
    const statusLine = node('div', null, 'episode-job-status-line'); statusLine.append(chip);
    if (corrected) {
      const updated = link('Updated', '#corrections', 'episode-updated');
      updated.setAttribute('aria-label', 'Updated · view corrections'); statusLine.append(updated);
    }
    card.append(statusLine);
    const date = job.created_at || job.finished_at || job.updated_at || '';
    const parsedDate = date ? new Date(date) : null;
    const dateText = parsedDate && !Number.isNaN(parsedDate.getTime()) ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(parsedDate) : '';
    card.append(node('p', `${job.length || episode.duration_minutes || 5} min${dateText ? ` · ${dateText}` : ''}`, 'episode-job-card-status'));
    const actions = node('div', null, 'episode-job-actions');
    const save = node('button', saved ? 'Saved' : 'Save', 'episode-job-save'); save.type = 'button';
    save.setAttribute('aria-label', `${saved ? 'Remove' : 'Save'} ${title}${saved ? ' from saved episodes' : ''}`); save.setAttribute('aria-pressed', String(saved));
    save.addEventListener('click', () => {
      toggleSave(id, episode.revision_id || job.revision_id || null, false);
      renderEpisodeHistory(result, available, corrections);
      const next = content.querySelector(`[data-job-id="${CSS.escape(id)}"] .episode-job-save`); next?.focus({ preventScroll: true });
    });
    const remove = node('button', 'Delete', 'episode-job-delete'); remove.type = 'button'; remove.setAttribute('aria-label', `Delete ${title} from this list`);
    remove.addEventListener('click', () => {
      saveEpisodeHistoryDismissed([...episodeHistoryDismissed(), id]);
      episodeHistoryUndo = { id, title };
      if (episodeHistoryUndoTimer) clearTimeout(episodeHistoryUndoTimer);
      renderEpisodeHistory(result, available, corrections);
      const undoEntry = episodeHistoryUndo;
      showToast(`${title} removed from this list.`, { actionLabel: 'Undo', onAction: () => {
        if (episodeHistoryUndo !== undoEntry) return;
        saveEpisodeHistoryDismissed(episodeHistoryDismissed().filter(removedId => removedId !== id));
        episodeHistoryUndo = null;
        if (episodeHistoryUndoTimer) clearTimeout(episodeHistoryUndoTimer);
        episodeHistoryUndoTimer = null;
        renderEpisodeHistory(result, available, corrections);
      } }).querySelector('.app-toast-action')?.focus({ preventScroll: true });
      episodeHistoryUndoTimer = setTimeout(() => { episodeHistoryUndo = null; episodeHistoryUndoTimer = null; }, 4000);
    });
    actions.append(save, remove); card.append(actions);
    list.append(card);
  }
  if (filtered.length) page.append(list);
  else {
    const empty = node('section', null, 'episode-history-empty');
    const query = episodeHistorySearch.trim();
    const messages = {
      all: ['Ask for your first episode', 'Episodes you request will appear here.'],
      making: ['No episodes being made', 'When an episode is in progress, it will appear here.'],
      ready: ['No ready episodes yet', 'Completed episodes will appear here.'],
      saved: ['No saved episodes', 'Save an episode to keep it close at hand.'],
      corrected: ['No corrected episodes', 'Corrections to an episode will appear here.'],
    };
    if (query) empty.append(node('h2', 'No matching episodes'), node('p', `No episodes match “${query}”.`));
    else if (!visibleJobs.length && episodeHistoryFilter === 'all') {
      empty.append(node('h2', messages.all[0]), node('p', messages.all[1]), link('Ask for your first episode', '#ask-episode', 'button-link'));
    } else empty.append(node('h2', messages[episodeHistoryFilter][0]), node('p', messages[episodeHistoryFilter][1]));
    page.append(empty);
  }
  content.replaceChildren(page);
}

function openEpisodeStyleSheet(opener) {
  const dialog = node('dialog', null, 'episode-style-dialog');
  const sheet = node('section', null, 'episode-style-sheet');
  const heading = node('h2', 'How should it be told?'); heading.id = 'episode-style-heading';
  const close = node('button', 'Close', 'episode-style-close'); close.type = 'button';
  close.setAttribute('aria-label', 'Close narration style choices');
  close.addEventListener('click', () => dialog.close());
  sheet.append(close, heading, node('p', 'Choose the shape of your episode.', 'episode-style-intro'));
  for (const mode of EPISODE_MODES) {
    const choice = node('button', null, 'episode-style-choice'); choice.type = 'button';
    choice.append(node('strong', mode.label), node('small', mode.detail));
    choice.addEventListener('click', () => {
      dialog.close();
      startEpisodeJob(mode);
    });
    sheet.append(choice);
  }
  dialog.setAttribute('aria-labelledby', heading.id);
  dialog.append(sheet);
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { dialog.remove(); if (location.hash === '#ask-episode') opener?.focus(); }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
}

function episodeModeStyle(mode) {
  return mode.id === 'brief' ? 'news-brief' : mode.id === 'deep-dive' ? 'explainer' : 'conversational';
}

async function startEpisodeJob(mode) {
  if (episodeRequestBusy) return;
  if (episodeJobTimer) clearTimeout(episodeJobTimer);
  episodeJobTimer = null; episodeJob = null;
  const request = episodeDraft.prompt;
  episodeRequestBusy = true;
  episodeRequestError = '';
  const status = content.querySelector('.episode-request-error');
  if (status) status.textContent = 'Starting your episode…';
  const start = content.querySelector('#episode-request-start');
  if (start) { start.disabled = true; start.textContent = 'Starting your episode…'; }
  try {
    // The private job API attaches requests to its saved profile. A first-time
    // listener gets the standard local profile until their own interests exist.
    await syncProfile(interestProfile || defaultProfile(pack.topics));
    const response = await fetch('/api/episodes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: request, length: episodeDraft.minutes, style: episodeModeStyle(mode) }),
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 429 && result.error === 'cost_cap_reached') {
      episodeRequestError = 'The current cost limit has been reached. Your request is still here; try again later.';
      episodeRequestBlocked = true;
      return;
    }
    if (response.status === 429 && result.error === 'daily_limit_reached') {
      const leftToday = Number.isInteger(result.left_today) ? Math.max(0, result.left_today) : 0;
      episodeRequestError = `${leftToday} left today`;
      episodeRequestBlocked = true;
      return;
    }
    if (response.status === 429 && result.error === 'rate_limited') {
      episodeRequestError = 'Please wait a moment, then try again.';
      return;
    }
    if (response.status === 422 && result.error === 'unsupported_format') {
      episodeRequestError = 'That kind of episode is not supported yet. Try a factual question.';
      return;
    }
    if (response.status === 400 || response.status === 422) {
      episodeRequestError = 'That request needs a little more detail. Edit the topic or question and try again.';
      return;
    }
    if (!response.ok || !result.job_id) throw new Error('episode_request_failed');
    episodeJob = {
      costEstimate: result.cost_estimate?.label, jobId: result.job_id, request, minutes: episodeDraft.minutes, mode,
      status: result.status || 'queued',
      stage: result.stage || 'queued', counts: result.counts || { chapters: 0, sentences: 0, sources: 0 },
      startedAt: Date.now(), stageStartedAt: Date.now(), stageIndex: 0,
      announcedStage: -1, activityKey: '', done: false,
    };
    navigate('#episode-making', { root: 'today', parentHash: '#ask-episode', parentScroll: window.scrollY,
      parentFocus: { type: 'id', id: 'episode-request-start' } });
    void pollEpisodeJob(episodeJob);
  } catch {
    episodeRequestError = 'Your episode could not be started. Please try again.';
  } finally {
    episodeRequestBusy = false;
    if (location.hash === '#ask-episode' && !episodeJob) route();
  }
}

function announceEpisodeStage(job, message) {
  const region = content.querySelector('.episode-stage-announcement');
  if (region) region.textContent = message;
  job.announcedStage = job.stageIndex;
}

function episodeStageIndex(stage) {
  if (stage === 'done') return 4;
  if (stage === 'evidence' || stage === 'checking') return 1;
  const index = ['researching', 'reading', 'writing', 'recording'].indexOf(stage);
  return index < 0 ? 0 : index;
}

function episodeStageTitle(_job, index) {
  return EPISODE_STAGE_FIXTURES[index]?.title || 'Finding sources';
}

function episodeHasReadyAudio(job) {
  return (!job?.audio_status || job.audio_status === 'recorded') && hasCurrentEpisodeRecording(job?.episode);
}

function episodeLengthCopy(job) {
  const episode = job?.episode || {};
  const minutes = Number(episode.duration_minutes);
  if (episodeHasReadyAudio(job) && episode.duration_basis === 'measured rendered audio'
      && Number.isFinite(minutes) && minutes > 0) return `${minutes} min of audio`;
  const requested = job?.length ?? job?.minutes ?? episode.requested_minutes;
  return Number.isFinite(Number(requested)) && Number(requested) > 0
    ? `Requested: ${Number(requested)} min` : 'Requested length not listed';
}

async function pollEpisodeJob(job) {
  if (!job || job !== episodeJob || !job.jobId) return;
  if (episodeJobTimer) clearTimeout(episodeJobTimer);
  try {
    const response = await fetch(`/api/episodes/${encodeURIComponent(job.jobId)}`, { cache: 'no-store' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result) throw new Error('episode_status_unavailable');
    job.counts = result.counts || job.counts;
    const nextStage = result.stage || result.status || job.stage;
    const nextIndex = episodeStageIndex(nextStage);
    if (nextStage !== job.stage) {
      job.stage = nextStage;
      job.stageIndex = nextIndex;
      job.stageStartedAt = Date.now();
      const stage = { ...EPISODE_STAGE_FIXTURES[nextIndex], title: episodeStageTitle(job, nextIndex) };
      if (nextStage === 'done') announceEpisodeStage(job, 'Your episode is ready.');
      else if (nextStage === 'failed') announceEpisodeStage(job, 'This episode could not be completed.');
      else if (nextStage === 'cancelled') announceEpisodeStage(job, 'Episode cancelled.');
      else if (job.announcedStage !== nextIndex) announceEpisodeStage(job, `${stage.title}. ${stage.detail}`);
    }
    job.status = result.status || nextStage;
    job.message = result.message || '';
    job.failureReason = result.failure_reason || result.error || '';
    job.episode = result.episode || job.episode || null;
    job.audio_status = result.audio_status || job.episode?.audio_status || job.episode?.audio?.status || '';
    job.shorterThanRequested = Boolean(result.shorter_than_requested || result.shortfall_notice ||
      job.episode?.shorter_than_requested || job.episode?.shortfall_notice);
    job.done = job.status === 'done';
    job.failed = job.status === 'failed' || job.status === 'cancelled';
    job.pollError = '';
  } catch {
    job.pollError = 'Checking progress…';
  }
  if (location.hash === '#episode-making') updateEpisodeMakingView(job, Date.now() - job.stageStartedAt, Date.now() - job.startedAt);
  if (!job.done && !job.failed) episodeJobTimer = setTimeout(() => void pollEpisodeJob(job), 1000);
}

function renderEpisodeMakingPage() {
  if (!episodeJob) {
    content.append(node('h1', 'No episode is being made'), link('Return to episode request', '#ask-episode', 'button-link'));
    return;
  }
  const job = episodeJob;
  const sticky = node('div', null, 'reader-sticky');
  const back = node('button', '‹ On demand', 'reader-back'); back.type = 'button';
  back.addEventListener('click', () => navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 }));
  sticky.append(back); content.append(sticky);

  const announcement = node('p', '', 'episode-stage-announcement episode-visually-hidden');
  announcement.setAttribute('role', 'status'); announcement.setAttribute('aria-live', 'polite'); announcement.setAttribute('aria-atomic', 'true');
  const page = node('section', null, 'episode-making-page');
  page.append(node('p', job.done ? 'Ready' : job.failed ? 'Not finished' : 'Making your episode', 'episode-making-kicker'));
  page.append(node('blockquote', `“${job.request}”`, 'episode-making-quote'));
  page.append(node('p', `You chose ${job.minutes} min`, 'episode-making-meta'));
  const longWait = node('p', 'This is taking a little longer. Your briefing will stay in the On demand list if you leave this page.', 'episode-long-wait');
  longWait.setAttribute('role', 'status'); longWait.hidden = true;
  page.append(longWait);

  const stages = node('ol', null, 'episode-stage-list');
  EPISODE_STAGE_FIXTURES.forEach((stage, index) => {
    const row = node('li', null, 'episode-stage-row'); row.dataset.index = String(index);
    const ring = node('span', '', 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
    const copy = node('div', null, 'episode-stage-copy');
    copy.append(node('strong', stage.title), node('small', stage.detail, 'episode-stage-status'));
    row.append(ring, copy); stages.append(row);
  });
  page.append(stages);

  const activity = node('p', null, 'episode-progress-note');
  page.append(activity);

  const actions = node('div', null, 'episode-making-actions');
  if (!job.done && !job.failed) {
    const cancel = node('button', 'Cancel this episode', 'episode-cancel-button'); cancel.type = 'button';
    cancel.addEventListener('click', cancelEpisodeJob);
    actions.append(cancel); page.append(actions);
  }
  if (job.pollError) { const notice = node('p', job.pollError, 'episode-job-status'); notice.setAttribute('role', 'status'); page.append(notice); }
  content.append(announcement, page);
  if (job.announcedStage < 0) announceEpisodeStage(job, `${episodeStageTitle(job, job.stageIndex)}. ${EPISODE_STAGE_FIXTURES[job.stageIndex].detail}`);
  if (job.done) announceEpisodeStage(job, 'Your episode is ready.');
  updateEpisodeMakingView(job, Date.now() - job.stageStartedAt, Date.now() - job.startedAt);
  if (job.done) appendEpisodeDoneCard(page, job);
}

function resetBrowserChoices() {
  player.close(); clearEditionCache(); state = resetState(); clearProfile(); clearFirstRunBuild();
  localStorage.removeItem(DAILY_TIME_KEY); localStorage.removeItem(TOPICS_PENDING_KEY);
  sessionStorage.removeItem(TOPICS_DRAFT_KEY);
  topicsDraft = null; topicsProgress = ''; topicsError = ''; topicsUpdating = false;
  buildInProgress = false; buildAttempt++; interestProfile = null; navigate('#preferences');
}

function episodeFailureCopy(job) {
  if (job.status === 'cancelled') return 'This episode was canceled. You can start a new one from On demand.';
  if (job.failureReason === 'cost_cap_reached') return 'The cost limit was reached while preparing this episode. Your request is still here; try again later.';
  if (job.failureReason === 'insufficient_evidence') return 'We could not find enough verified information to finish this episode. Try a narrower question.';
  return 'We could not finish this episode this time. You can try again.';
}

function updateEpisodeMakingView(job, elapsed, totalElapsed = elapsed) {
  const page = content.querySelector('.episode-making-page');
  if (!page) return;
  const kicker = page.querySelector('.episode-making-kicker');
  const longWait = !job.done && !job.failed && totalElapsed >= EPISODE_LONG_WAIT_MS;
  const failureKicker = job.failureReason === 'cost_cap_reached' ? 'Try again later' : 'Ready to try again';
  if (kicker) kicker.textContent = job.done ? 'Episode ready' : job.failed ? failureKicker : longWait ? 'Still working' : 'Making your episode';
  const longWaitNote = page.querySelector('.episode-long-wait');
  if (longWaitNote) longWaitNote.hidden = !longWait;
  if (job.done || job.failed) page.querySelector('.episode-making-actions')?.remove();
  if (job.failed) renderEpisodeFailureState(page, job);
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const progress = job.done ? 1 : Math.min(.94, Math.max(.08, elapsed / 4000));
  page.querySelectorAll('.episode-stage-row').forEach((row, index) => {
    const stateName = job.done || index < job.stageIndex ? 'done' : index === job.stageIndex ? 'active' : 'upcoming';
    row.dataset.state = stateName;
    if (stateName === 'active') row.setAttribute('aria-current', 'step'); else row.removeAttribute('aria-current');
    const ring = row.querySelector('.episode-stage-ring');
    row.querySelector('.episode-stage-copy strong').textContent = episodeStageTitle(job, index);
    ring.textContent = '';
    ring.dataset.check = stateName === 'done' ? '✓' : '';
    const fill = stateName === 'done' ? 360 : stateName === 'active' ? (reducedMotion ? 90 : Math.round(progress * 360)) : 0;
    ring.style.setProperty('--episode-ring-fill', `${fill}deg`);
    const read = Number(job.counts?.sources_read ?? (index === 1 ? job.counts?.sources : 0)) || 0;
    const detail = index === 1 && read ? `${read} ${read === 1 ? 'source' : 'sources'} read; checking claims.` : EPISODE_STAGE_FIXTURES[index].detail;
    row.querySelector('.episode-stage-status').textContent = stateName === 'done' ? 'Complete.' : stateName === 'active' && job.status === 'queued' ? 'Waiting to start.' : detail;
  });
  const activityKey = `${job.done ? 'done' : job.stageIndex}:${JSON.stringify(job.counts)}`;
  if (job.activityKey !== activityKey) {
    job.activityKey = activityKey;
    renderEpisodeActivityPanel(page.querySelector('.episode-progress-note'), job);
  }
  if (job.done && !page.querySelector('.episode-done-card')) appendEpisodeDoneCard(page, job);
}

function renderEpisodeFailureState(page, job) {
  page.querySelector('.episode-stage-list')?.remove();
  page.querySelector('.episode-progress-note')?.remove();
  let notice = page.querySelector('.episode-job-error');
  if (!notice) {
    notice = node('p', '', 'episode-job-error'); notice.setAttribute('role', 'status');
    page.append(notice);
  }
  notice.textContent = episodeFailureCopy(job);
  if (job.status === 'cancelled' || job.failureReason === 'cost_cap_reached' || page.querySelector('.episode-retry-button')) return;
  const retry = node('button', 'Try again', 'primary episode-retry-button'); retry.type = 'button';
  retry.addEventListener('click', () => {
    episodeDraft.prompt = job.request; rememberEpisodeLength(job.minutes);
    navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 });
    void startEpisodeJob(job.mode);
  });
  page.append(retry);
}

function renderEpisodeActivityPanel(panel, job) {
  if (!panel) return;
  const counts = job.counts || {};
  const episodeSources = Array.isArray(job.episode?.sources) ? job.episode.sources.length : 0;
  const found = Number(counts.sources_found) || Number(counts.sources) || episodeSources;
  const read = Number(counts.sources_read) || 0;
  panel.textContent = found ? `${found} ${found === 1 ? 'source' : 'sources'} found${read ? `, ${read} read` : ''}` : '';
}

function appendEpisodeDoneCard(page, job) {
  const audioReady = episodeHasReadyAudio(job);
  const card = node('section', null, 'episode-done-card');
  card.append(node('p', audioReady ? 'Episode ready' : 'Transcript ready', 'episode-done-kicker'),
    node('h2', audioReady ? 'Your episode is ready' : 'Your transcript is ready'));
  card.append(node('p', `You chose ${job.minutes} min`, 'episode-done-meta'));
  if (!audioReady) card.append(node('p', 'Audio is not recorded yet. The transcript is ready to read.', 'episode-audio-pending-note'));
  if (job.shorterThanRequested || job.episode?.shortfall_notice) card.append(node('p', 'We found limited coverage; here is a shorter episode.', 'episode-done-notice episode-thin-evidence'));
  const actions = node('div', null, 'episode-done-actions');
  const open = node('button', audioReady ? 'Open episode' : 'Open transcript', 'episode-read-button'); open.type = 'button';
  open.addEventListener('click', () => navigate(`#episode/${encodeURIComponent(job.jobId)}`, { root: 'today', parentHash: '#episode-making', parentScroll: window.scrollY }));
  const another = node('button', 'Ask for another episode', 'episode-back-button'); another.type = 'button';
  another.addEventListener('click', () => navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 }));
  actions.append(open, another); card.append(actions); page.append(card);
}

async function cancelEpisodeJob() {
  const job = episodeJob;
  if (episodeJobTimer) clearTimeout(episodeJobTimer);
  episodeJobTimer = null;
  if (job?.jobId && !job.done && !job.failed) {
    try {
      await fetch(`/api/episodes/${encodeURIComponent(job.jobId)}/cancel`, { method: 'POST' });
    } catch { /* Leaving the making page remains safe if the job service is offline. */ }
  }
  episodeJob = null;
  navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 });
}

let restoringRoute = false;
let renderedRouteHash = null;
const historyViews = new Map();
let historyEntrySequence = 0;
history.scrollRestoration = 'manual';

function route() {
  if (!pack) return;
  episodeDeckCleanup?.(); episodeDeckCleanup = null;
  restoringRoute = true;
  renderedRouteHash = location.hash || '#today';
  invalidateMaterialCorrections();
  const nextHash = location.hash || '#today';
  finishStoryVisit(nextHash);
  readingObserver?.disconnect(); readingObserver = null;
  content.classList.remove('interests-page');
  content.classList.remove('first-run-describe', 'interest-build-screen');
  content.textContent = '';
  const routeName = decodeURIComponent((location.hash || '#today').slice(1));
  const routeHash = location.hash || '#today';
  const episodeRoute = routeName === 'episodes' || routeName.startsWith('episode/') || routeName.startsWith('episode-making/');
  if (interestInterpretController && !routeName.startsWith('interests/')) {
    interestInterpretController.abort();
    interestInterpretController = null;
    interestInterpretBusy = false;
  }
  const routeState = history.state || {};
  if (pendingRouteContext) {
    history.replaceState({ ...routeState, routeContext: pendingRouteContext }, '', location.href);
    pendingRouteContext = null;
  } else if (!routeState.routeContext) {
    const directRoot = rootForHash(routeHash, null);
    const directParent = routeName.startsWith('explanation/') ? '#topics' : routeName.startsWith('topic/') || routeName.startsWith('topic-detail/') ? '#topics' : null;
    history.replaceState({ ...routeState, routeContext: { root: directRoot, parentHash: directParent, parentScroll: 0, parentFocus: null, direct: Boolean(directParent) } }, '', location.href);
  }
  if (!history.state?.viewKey) history.replaceState({ ...history.state, viewKey: `${Date.now()}-${++historyEntrySequence}` }, '', location.href);
  trackRouteSignals(routeHash, routeName);
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
  const onDemandRoute = ['ask', 'ask-episode', 'episode-making', 'episodes'].includes(routeName) || routeName.startsWith('episode/');
  const group = onDemandRoute ? 'on-demand' : episodeRoute ? 'library' : rootForHash(routeHash, history.state?.routeContext);
  const editionKind = routeName.startsWith('edition/') ? routeName.slice(8).split('/')[0] : null;
  const isReader = routeName === 'weekly' || routeName === 'corrections' || routeName === 'episode-making' || routeName.startsWith('episode-making/') || routeName.startsWith('edition/') || routeName.startsWith('explanation/') || routeName.startsWith('topic/') || routeName.startsWith('topic-detail/') || routeName.startsWith('source/') || routeName.startsWith('interests/') || (episodeRoute && routeName.startsWith('episode/'));
  document.body.classList.toggle('is-reader', isReader);
  const pageTitle = episodeRoute ? (routeName === 'episodes' ? 'Episodes' : 'Episode') : routeName === 'corrections' ? 'Corrections' : routeName === 'episode-making' || routeName.startsWith('episode-making/') ? 'Making your episode' : onDemandRoute ? 'On demand' : routeName === 'interests' || routeName.startsWith('interests/') ? 'Interests' : routeName === 'preferences' ? 'Settings' : routeName.startsWith('source/') ? 'Source' : routeName.startsWith('explanation/') || routeName.startsWith('topic/') ? 'Explanation' : routeName.startsWith('topic-detail/') ? 'Topic' : group === 'topics' ? 'Topics' : group === 'library' ? 'Earlier' : (routeName === 'weekly' || editionKind === 'weekly') ? 'Weekly' : 'Today';
  document.querySelector('[data-page-title]').textContent = pageTitle;
  document.body.classList.toggle('is-today', routeName === 'today');
  document.body.classList.toggle('is-topics', routeName === 'topics');
  document.body.classList.toggle('is-episodes-list', routeName === 'episodes');
  document.title = `${pageTitle} · Personal briefing`;
  // Mica · pass 010 navigation: Settings is opened by the gear, so no reader tab is current there.
  for (const item of nav) {
    if (routeName === 'preferences') item.removeAttribute('aria-current');
    else if (item.dataset.nav === group) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
  if (episodeRoute) void renderEpisodeRoute(routeName);
  else if (routeName === 'today' || !routeName) renderTodayScreen({
    content, node, link, interestProfile, pendingTopicsUpdate, buildInProgress, todayListSkeleton,
    getState: () => state, setState: value => { state = value; },
    FIRST_EDITION_WELCOME_KEY, profileBuildKey, BUILD_GENERAL_FALLBACK_KEY,
    currentEdition, filterGeneralEditionForProfile, buildNotice, setBuildNotice: value => { buildNotice = value; },
    startTodayBuildAfterStatusCheck, startBuildGeneration, renderNoStoryExplanations,
    profileTopicNames, BUILD_GENERAL_FALLBACK_NOTE, navigate, writeFirstRunBuild,
    renderMissing, pack, localStorage, sessionStorage, editionJustUpdatedAt,
    buildChipLabel, topicIdFor, toggleSave, saveState, recordSignal, signalEvents,
    dailyTimeValue, openSource, player, getEditionMode, listeningAvailable, renderToday,
    listenEstimateWithUnit, listenEstimate, listenControlLabel, formatElapsed,
    invalidatedEditionAudio, startEditionListening, startRowListening, visibleSections,
    firstReadableSection, focusToken, withNarrationTopics, personalized, readableEdition, rememberEditionMode,
    cacheIdentity, writeEditionCache, showingCachedPack, getCachedPackIdentity: () => cachedPackIdentity,
    setCachedPackIdentity: value => { cachedPackIdentity = value; },
    refreshTodayRunSummary, markDailyEditionSeen,
  });
  else if (routeName === 'ask-episode' || routeName === 'ask') renderEpisodeRequestPage();
  else if (routeName === 'episode-making') renderEpisodeMakingPage();
  else if (routeName === 'corrections') void renderCorrectionsPage();
  else if (routeName === 'weekly') renderEditionPage('weekly');
  else if (routeName.startsWith('edition/')) {
    const parts = routeName.slice(8).split('/');
    const kind = parts.length > 1 ? parts.shift() : null;
    const id = parts.shift();
    const sectionId = parts.shift() || history.state?.routeContext?.sectionId || null;
    const edition = currentRevision(pack.editions, id, item => !kind || item.kind === kind);
    if (edition) {
      renderEditionPage(edition.kind, edition, sectionId);
    } else renderMissing();
  }
  else if (routeName.startsWith('explanation/')) {
    const dive = currentRevision(pack.deepDives, routeName.slice(12));
    if (dive) renderDeepDivePage(dive.topic_id, dive); else renderMissing();
  }
  else if (routeName.startsWith('topic-detail/')) renderTopicDetailPage(routeName.slice(13));
  else if (routeName === 'topics') renderTopicsPage();
  else if (routeName.startsWith('topic/')) renderDeepDivePage(routeName.slice(6));
  else if (routeName.startsWith('source/')) renderSourcePage(routeName.slice(7));
  else if (routeName === 'interests/describe') renderInterestDescribe();
  else if (routeName === 'interests/build') renderInterestBuild();
  else if (routeName === 'interests/confirm') renderInterestBuild();
  else if (routeName === 'preferences') renderSettingsScreen({
    content, node, link, dailyTimeValue, formatEasternTime, nextDailyRunTime,
    interestProfile, navigate, resetBehaviorWeighting, player, NARRATOR_KEY, refreshLastUpdated,
    getState: () => state, setState: value => { state = value; }, saveState,
    localStorage, sessionStorage, resetChoices: resetBrowserChoices,
  });
  else if (routeName === 'interests') renderInterestsPage();
  else if (routeName === 'library' || routeName === 'earlier') renderEarlierScreen({
    content, node, link, currentItems, visibleSections, editionDateRange, hasMaterialCorrection,
    getState: () => state, interestProfile, editionHistoryLoaded, pack, dailyBriefings, firstReadableSection,
    revisionDate, briefingDate, displayDate,
    getLibraryFilter: () => libraryFilter, setLibraryFilter: value => { libraryFilter = value; },
    getLibrarySearchQuery: () => librarySearchQuery, setLibrarySearchQuery: value => { librarySearchQuery = value; },
    loadEpisodeCards, canListen, listenEstimate, createPlayIcon, startListening, toggleSave,
    browserCanSpeak, navigate, loadEditionHistory,
  });
  else renderMissing();
  if (firstRunPending && firstRunRecoveryAvailable && ['today', 'interests/build'].includes(routeName)) {
    firstRunRecoveryAvailable = false;
    const pending = firstRunPending;
    const profile = interestProfile;
    queueMicrotask(() => { if (profile) void startBuildGeneration(profile, null, {
      firstRun: true, resumeJobId: pending.jobId, inline: routeName === 'today',
    }); });
  }
  const settingsNotice = sessionStorage.getItem('briefing:settings-saved');
  if (settingsNotice && ['today', 'topics', 'library'].includes(group) && !isReader) {
    showToast(settingsNotice); sessionStorage.removeItem('briefing:settings-saved');
  }
  player.refresh();
  const restoreScroll = pendingScrollRestore;
  pendingScrollRestore = null;
  const viewState = historyViews.get(history.state?.viewKey) || history.state?.viewState || history.state?.routeContext?.restore;
  if (restoreScroll != null) requestAnimationFrame(() => window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
  else if (viewState && routeName !== 'preferences') requestAnimationFrame(() => window.scrollTo({ top: viewState.scrollY || 0, behavior: 'instant' }));
  else if (routeName !== 'preferences') window.scrollTo({ top: 0, behavior: 'instant' });
  const sourceFocusID = pendingSourceFocusID;
  pendingSourceFocusID = null;
  if (sourceFocusID) requestAnimationFrame(() => {
    const source = [...content.querySelectorAll('[data-source]')].find(button => button.dataset.source === sourceFocusID);
    if (source?.closest('details')) source.closest('details').open = true;
    revealStoryMore(source);
    source?.focus({ preventScroll: true });
  });
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
    revealStoryMore(target);
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
    if (correction || !section) target?.focus({ preventScroll: true });
    else { const heading = section.querySelector('h2') || content.querySelector('h1'); if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); } }
    const spoken = paragraphAt(section, placement.paragraphIndex || 0); revealStoryMore(spoken);
    target?.scrollIntoView({ block: 'start', behavior: 'instant' });
    spoken?.scrollIntoView({ block: 'center', behavior: 'instant' });
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
  requestAnimationFrame(() => requestAnimationFrame(() => { restoringRoute = false; }));
}

// Hash routes create real browser entries. Keep the outgoing entry current
// even when the user presses browser Back instead of an in-app link.
window.addEventListener('scroll', () => {
  if (restoringRoute || renderedRouteHash !== (location.hash || '#today')) return;
  const viewState = { scrollY: window.scrollY, focus: focusToken() || history.state?.viewState?.focus || null };
  // Keep each history entry's latest position without rate-limited History API
  // writes on every scroll frame (notably on mobile Safari).
  historyViews.set(history.state?.viewKey, viewState);
}, { passive: true });
window.addEventListener('hashchange', route);
document.addEventListener('click', event => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const anchor = event.target.closest?.('a[href^="#"]');
  if (!anchor || anchor.target === '_blank') return;
  const hash = anchor.getAttribute('href');
  if (!hash || hash === '#content') return;
  event.preventDefault();
  const isRoot = ['#today', '#topics', '#ask', '#library', '#earlier'].includes(hash);
  const root = isRoot ? hash.slice(1) : rootForHash(location.hash);
  navigate(hash, { root, parentHash: isRoot ? null : (location.hash || '#today'), parentScroll: window.scrollY, parentFocus: focusToken() });
});
function cacheIdentity() {
  return JSON.stringify([interestProfile ? profileBuildKey(interestProfile) : 'sample', localStorage.getItem('briefing:v1:built-profile-key')]);
}

function keepReadingPosition() {
  const scrollY = window.scrollY;
  const focus = focusToken();
  const paragraphs = [...content.querySelectorAll('.edition-section p, .deep-dive-section p')];
  const anchor = paragraphs.find(paragraph => paragraph.getBoundingClientRect().bottom > 0);
  const section = anchor?.closest('.edition-section, .deep-dive-section');
  const index = section ? [...section.querySelectorAll('p')].indexOf(anchor) : -1;
  const offset = anchor?.getBoundingClientRect().top;
  history.replaceState({ ...history.state, viewState: { scrollY, focus } }, '', location.href);
  return () => requestAnimationFrame(() => requestAnimationFrame(() => {
    const replacement = section && document.getElementById(section.id)?.querySelectorAll('p')[index];
    if (replacement) window.scrollBy({ top: replacement.getBoundingClientRect().top - offset, behavior: 'instant' });
  }));
}

function cachedFailure() {
  showNetworkBanner(navigator.onLine === false ? 'offline' : 'failed', Boolean(pack || showingCachedPack));
}

function renderAppLoading() {
  const loading = node('section', null, 'app-loading');
  loading.setAttribute('role', 'status'); loading.setAttribute('aria-busy', 'true');
  if (location.hash.startsWith('#ask')) {
    loading.classList.add('app-loading-on-demand');
    loading.append(node('h1', 'Your on-demand briefings are loading'), node('p', 'Getting your saved list ready.'));
    loading.append(episodeCardsSkeleton());
  } else {
    loading.append(node('h1', 'Your briefing is loading'), node('p', 'Getting today’s stories ready.'), todayListSkeleton());
  }
  content.replaceChildren(loading);
}

async function refreshInitialPack({ background = false } = {}) {
  if (editionRefreshInFlight) return;
  editionRefreshInFlight = true;
  lastEditionCheckAt = Date.now();
  clearTimeout(slowNetworkTimer);
  slowNetworkTimer = setTimeout(() => {
    if (editionRefreshInFlight) showNetworkBanner('slow', Boolean(pack || showingCachedPack));
  }, 5000);
  if (!background && !pack) renderAppLoading();
  const initialAttempt = buildAttempt;
  try {
    // Ask the server for its active-profile edition on fresh browsers too. The
    // server profile and edition are authoritative; local storage is only a cache.
    let [data, serverProfile] = await Promise.all([loadPack(true), fetchSavedServerProfile()]);
    if (initialAttempt !== buildAttempt) return;
    const previousPack = pack;
    const previousDaily = previousPack ? currentEdition('daily') : null;
    const localProfile = getProfile();
    const serverProfileChanged = Boolean(!pendingTopicsUpdate() && serverProfile && (!localProfile || profileBuildKey(serverProfile) !== profileBuildKey(localProfile)));
    interestProfile = serverProfileChanged ? saveProfile(serverProfile, { server: false }) : localProfile;
    if (!editionCheckSucceeded && previousPack && !serverProfileChanged) {
      cachedFailure();
      return;
    }
    data = includeNewerCachedEdition(data, previousPack, serverProfileChanged);
    const latestDaily = latestGeneratedDaily(data.editions);
    const editionArrived = background && newerEdition(latestDaily, previousDaily);
    if (editionArrived) editionJustUpdatedAt = Date.now();
    const unchanged = Boolean(previousPack && !serverProfileChanged && JSON.stringify(previousPack) === JSON.stringify(data));
    const restoreScroll = background && location.hash === '#today' && !unchanged ? window.scrollY : null;
    const restorePosition = showingCachedPack && !unchanged ? keepReadingPosition() : null;
    pack = data;
    showingCachedPack = false;
    const activeGenerated = data.editions.find(item => item.kind === 'daily' && item.generated === true);
    if (serverProfile && activeGenerated && !pendingTopicsUpdate()) {
      localStorage.setItem('briefing:v1:built-profile-key', profileBuildKey(serverProfile));
    }
    if (serverProfileChanged) {
      buildNotice = '';
      sessionStorage.removeItem('briefing:build-error');
    }
    if (!editionCheckSucceeded) showNetworkBanner('failed', true);
    else if (navigator.onLine !== false) clearNetworkBanner();
    if (!location.hash && !interestProfile) {
      history.replaceState({ routeContext: { root: 'today', parentHash: null, interestReturnHash: '#today', firstRun: true } }, '', '#interests/describe');
    }
    // Corrections invalidate stored/current playback before the verification gate opens.
    invalidateMaterialCorrections();
    const saved = state.activeAudio;
    player.setVerified(true);
    if (saved) {
      const item = [...pack.editions, ...pack.deepDives].find(entry => entry.id === saved.id && entry.revision_id === saved.revisionId);
      if (item) player.restore(withNarrationTopics({ ...item, kind: item.kind || 'deep-dive' }));
      else {
        state.activeAudio = null;
        state = saveState(state);
        const corrected = pack.editions.find(entry => entry.id === saved.id && entry.corrections?.some(correction => correction.previous_revision_id === saved.revisionId));
        if (corrected) sessionStorage.setItem('briefing:correction-audio-notice', 'The prior recording is no longer available for this corrected edition.');
      }
    }
    if (unchanged) {
      // Keep every reading node, expanded source, focused control and scroll position.
      player.refresh(); syncListeningControls(); syncAudioVerification();
      writeEditionCache(cacheIdentity(), pack); cachedPackIdentity = pack;
    } else if (!background || location.hash === '#today') {
      route();
      restorePosition?.();
      if (restoreScroll !== null) requestAnimationFrame(() => window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
    } else {
      writeEditionCache(cacheIdentity(), pack); cachedPackIdentity = pack;
    }
  } catch (error) {
    if (showingCachedPack) { cachedFailure(); return; }
    showNetworkBanner(navigator.onLine === false ? 'offline' : 'failed', false);
    content.textContent = '';
    content.append(node('h1', 'Your briefing is unavailable'), node('p', 'Please try again in a moment.', 'error'));
  } finally {
    clearTimeout(slowNetworkTimer); slowNetworkTimer = null;
    editionRefreshInFlight = false;
  }
}

function revalidateLatestEdition({ timer = false } = {}) {
  if (document.visibilityState !== 'visible' || editionRefreshInFlight) return;
  if (!timer && Date.now() - lastEditionCheckAt < 5 * 60 * 1000) return;
  void refreshInitialPack({ background: true });
}

document.addEventListener('visibilitychange', () => revalidateLatestEdition());
window.addEventListener('pageshow', () => revalidateLatestEdition());
window.addEventListener('focus', () => revalidateLatestEdition());
window.setInterval(() => revalidateLatestEdition({ timer: true }), 30 * 60 * 1000);
window.addEventListener('offline', () => showNetworkBanner('offline'));
window.addEventListener('online', () => {
  showNetworkBanner('checking');
  if (!editionRefreshInFlight) void refreshInitialPack();
});

// A known local edition can paint before any request finishes. New browsers still
// resolve the saved profile before choosing onboarding or starting a build.
if (interestProfile || location.hash) {
  try {
    const cached = readEditionCache(cacheIdentity());
    if (cached) {
      pack = validatePack(cached);
      showingCachedPack = true;
      player.setVerified(false);
      invalidateMaterialCorrections();
      route();
    }
  } catch { pack = null; showingCachedPack = false; clearEditionCache(); player.setVerified(true); }
}
if (navigator.onLine === false) showNetworkBanner('offline');
void refreshInitialPack();
