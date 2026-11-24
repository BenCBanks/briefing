import { readEditionCache, writeEditionCache, clearEditionCache } from './edition/cache.js';
import { renderEdition, renderToday, renderWeeklyRecap, editionDateRange, visibleSections, hasMaterialCorrection } from './edition/edition.js';
import { revisionNumber, revisionDate, revisionGroup, compareRevision, currentRevision, currentItems, latestGeneratedDaily, newerEdition, includeNewerCachedEdition, displayDate, plainDate, briefingDate, firstReadableSection, dailyBriefings } from './edition/selectors.js';
// Narration · generated editions: only corrections tied to the current revision affect playback and Corrected labels.
import { renderTopics, renderDeepDive } from './topics/topics.js';
import { renderTopicDetail } from './topics/topic-detail.js';
// Interface · episode pages and the API-backed request flow.
import { renderEpisodeDeck, renderEpisodePage } from './episodes/episodes.js';
import { createEpisodeListScreen, episodeCardsSkeleton as createEpisodeCardsSkeleton } from './screens/episode-list.js';
import { loadState, saveState, resetState, invalidateAudioProgress } from './storage.js';
import { NARRATOR_KEY } from './player.js';
import { createAppPlayer } from './player/integration.js';
import { audioScriptText } from './audio-script.js';
import { createPlayIcon } from './ui-icons.js';
import { interpretProfile } from './interests/provider.js';
import { defaultProfile } from './interests/profile.js';
import { deriveInterestBrief } from './interests/brief.js';
import { behaviorWeights, clear as clearSignals, events as signalEvents, record as recordSignal } from './interests/signals.js';
import { applySuggestion, clearSuggestionSnoozes, generateSuggestions, snoozeSuggestion } from './interests/suggest.js';
import { renderInterests } from './interests/interests.js';
import { interpret, validateProfile } from './interests/interpret.js';
import { buildChipLabel, filterGeneralEditionForProfile, profileBuildKey, profileTopicNames } from './interests/profile-display.js';
import { clearProfile, getProfile, saveProfile, syncProfile } from './interests/store.js';
import { renderCorrections } from './corrections/corrections.js';
import { renderTopicsScreen } from './screens/topics.js';
import { renderOnDemandScreen, episodeTopicSuggestions } from './screens/on-demand.js';
import { renderSettingsScreen } from './screens/settings.js';
import { renderTodayScreen } from './screens/today.js';
import { renderEarlierScreen } from './screens/earlier.js';
import { renderInterestDescribe as renderInterestDescribeScreen, renderInterestConfirm as renderInterestConfirmScreen, renderInterestBuild as renderInterestBuildScreen } from './screens/interests.js';
import { createInterestFlowController } from './screens/interest-flow-controller.js';
import { createEpisodeJobController } from './screens/episode-job-controller.js';
import { createStoryInteractionController } from './screens/story-interactions.js';
import { renderInterestsManagementScreen } from './screens/interests-management.js';
import { episodeHistoryState, episodeHistoryDismissed, saveEpisodeHistoryDismissed, renderEpisodeHistory as renderEpisodeHistoryScreen, renderEpisodeMakingFromHistory as renderEpisodeMakingFromHistoryScreen } from './screens/episode-history.js';
import { EPISODE_STAGE_FIXTURES, announceEpisodeStage as announceEpisodeStageView, episodeStageIndex, episodeStageTitle, episodeHasReadyAudio, episodeLengthCopy, renderEpisodeMakingPage as renderEpisodeMakingScreen, updateEpisodeMakingView as updateEpisodeMakingScreen } from './screens/episode-making.js';
import { renderEpisodeRoute as renderEpisodeRouteScreen, renderEpisodeRouteFailure as renderEpisodeRouteFailureScreen } from './screens/episode-route.js';
import { createEditionReaderScreen } from './screens/edition-reader.js';
import { describeRoute, restoreRouteView } from './screens/route-controller.js';

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
const BUILD_GENERAL_FALLBACK_NOTE = 'Your daily briefing will switch to your topics as soon as they are ready.';
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
const EPISODE_UNHEARD_KEY = 'briefing:v1:episode-unheard';
function episodeUnheardIds() {
  try {
    const ids = JSON.parse(localStorage.getItem(EPISODE_UNHEARD_KEY) || '[]');
    return Array.isArray(ids) ? ids.filter(id => typeof id === 'string').slice(-100) : [];
  } catch { return []; }
}
function saveEpisodeUnheardIds(ids) {
  try { localStorage.setItem(EPISODE_UNHEARD_KEY, JSON.stringify([...new Set(ids)].slice(-100))); }
  catch { /* The badge is a convenience; episode access still works without storage. */ }
}
function syncOnDemandBadge(jobs = episodeHistorySnapshot?.jobs || []) {
  const tab = nav.find(item => item.dataset.nav === 'on-demand');
  const badge = tab?.querySelector('[data-on-demand-badge]');
  if (!tab || !badge) return;
  const activeId = episodeJob?.jobId;
  const making = Boolean(episodeJob && !episodeJob.done && !episodeJob.failed) || jobs.some(job => {
    const id = job.job_id || job.id;
    return id !== activeId && episodeHistoryState(job).key === 'making';
  });
  const unheard = episodeUnheardIds();
  const ready = Boolean(episodeJob?.done && unheard.includes(episodeJob.jobId)) ||
    jobs.some(job => unheard.includes(job.job_id || job.id) && episodeHistoryState(job).key === 'ready');
  const state = making ? 'making' : ready ? 'ready' : '';
  badge.hidden = !state;
  badge.dataset.state = state;
  badge.textContent = state === 'ready' ? '✓' : '';
  tab.setAttribute('aria-label', state === 'making' ? 'On demand, episode in progress' : state === 'ready' ? 'On demand, episode ready to play' : 'On demand');
}
function markEpisodeUnheard(id) {
  if (!id) return;
  saveEpisodeUnheardIds([...episodeUnheardIds(), id]);
  syncOnDemandBadge();
}
function markEpisodeHeard(id) {
  if (!id) return;
  saveEpisodeUnheardIds(episodeUnheardIds().filter(unheard => unheard !== id));
  syncOnDemandBadge();
}
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
let pendingReturnHeadingFocus = false;
let readingObserver = null;
let pendingRouteContext = null;
let lastSignalRoute = null;
const rootScroll = new Map();
let libraryFilter = ['saved', 'episodes'].includes(sessionStorage.getItem('briefing:library-filter')) ? sessionStorage.getItem('briefing:library-filter') : 'editions';
let librarySearchQuery = '';
const EDITION_MODE_KEY = 'briefing:v1:edition-mode';

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

const storyInteractionController = createStoryInteractionController({
  content, node, recordSignal, behaviorWeights, saveProfile, clearSignals, saveState,
  invalidateAudioProgress, audioScriptText, hasMaterialCorrection, currentRevision, showToast,
  compareRevision, personalized, visibleSections, focusToken, navigateTranscript,
  navigate, route, invalidatedEditionAudio,
  get player() { return player; },
  get syncListeningControls() { return syncListeningControls; },
  get refreshListenControls() { return refreshListenControls; },
  get withNarrationTopics() { return withNarrationTopics; },
  get interestProfile() { return interestProfile; },
  set interestProfile(value) { interestProfile = value; },
  get pack() { return pack; },
  set pack(value) { pack = value; },
  get readingObserver() { return readingObserver; },
  set readingObserver(value) { readingObserver = value; },
  get lastSignalRoute() { return lastSignalRoute; },
  set lastSignalRoute(value) { lastSignalRoute = value; },
  get transcriptReturnTo() { return transcriptReturnTo; },
  set transcriptReturnTo(value) { transcriptReturnTo = value; },
  get transcriptReturnHash() { return transcriptReturnHash; },
  set transcriptReturnHash(value) { transcriptReturnHash = value; },
  get state() { return state; },
  set state(value) { state = value; },
});
const {
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
} = storyInteractionController;

const playerIntegration = createAppPlayer({
  root: document.querySelector('#player-root'), content, node,
  getState: () => state, setState: value => { state = value; }, saveState,
  getPack: () => pack, getShowingCachedPack: () => showingCachedPack,
  openTranscript, transcriptLabel, signalForItem, hasMaterialCorrection,
  invalidatedEditionAudio, visibleSections, paragraphAt, revealStoryMore,
});
const {
  player, syncAudioVerification, listeningAvailable, formatElapsed, listenControlLabel,
  syncListeningControls, startEditionListening, startStoryListening, startRowListening,
  withNarrationTopics, readableEdition, refreshListenControls,
} = playerIntegration;

const { renderEditionPage: renderEditionPageScreen, renderWeeklyRecapPage: renderWeeklyRecapScreen } = createEditionReaderScreen({
  content, node, currentEdition, currentRevision, renderEdition, renderWeeklyRecap,
  visibleSections, personalized, listenEstimateWithUnit, listenControlLabel,
  listeningAvailable, rememberEditionMode, startStoryListening, startEditionListening,
  withNarrationTopics, readableEdition, openDeepDive, currentItems, openSource,
  topicIdFor, recordSignal, signalEvents, setFeedback, toggleSave, saveState,
  signalCurrent, showWhySheet, showStoryActionsSheet, beginStoryVisit,
  trackReadingPlace, transcriptDestination, returnFromTranscript, navigate,
  rootForHash, dailyBriefings, route,
  getInterestProfile: () => interestProfile, getPack: () => pack,
  getState: () => state, setState: value => { state = value; },
  player, invalidatedEditionAudio,
  get pendingTranscriptPlacement() { return pendingTranscriptPlacement; },
});

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

let loadEpisodeCards;
loadEpisodeCards = createEpisodeListScreen({
  node, link, episodeHistoryState, episodeLengthCopy, episodeHasReadyAudio,
  getEpisodeJob: () => episodeJob,
  episodeUnheardIds,
  syncOnDemandBadge,
  onSnapshot: snapshot => { episodeHistorySnapshot = snapshot; },
  onRetry: (...args) => loadEpisodeCards(...args),
});
function episodeCardsSkeleton() { return createEpisodeCardsSkeleton(node); }

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
      // Narration · generated editions can contain profile-defined topic IDs beyond the sample catalog.
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
  // Narration · preserve the source page's actual immediate parent for its Back label.
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
function renderEditionPage(kind, exactEdition = null, sectionId = null) {
  return renderEditionPageScreen(kind, exactEdition, sectionId);
}

function renderWeeklyRecapPage(edition) {
  return renderWeeklyRecapScreen(edition);
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
  renderInterestsManagementScreen({
    content, node, history, pack,
    getProfile: () => interestProfile, setProfile: value => { interestProfile = value; },
    setDraft: value => { interestDraft = value; },
    getPageFlags: () => interestPageFlags, setPageFlags: value => { interestPageFlags = value; },
    getPageNotice: () => interestPageNotice, setPageNotice: value => { interestPageNotice = value; },
    saveProfile, defaultProfile, immediateParentLabel, navigate, rootForHash,
    generateSuggestions, renderInterests, interpretProfile,
    invalidateTopicsEdition, startBuildGeneration, route,
    applySuggestion, snoozeSuggestion, resetBehaviorWeighting, clearSuggestionSnoozes,
  });
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

const interestFlowController = createInterestFlowController({
  content, node, navigate, rootForHash, focusToken, back,
  renderInterestDescribeScreen, dailyTimeValue, DAILY_TIME_KEY, interpret, buildChipLabel,
  clearFirstRunBuild, interpretProfile, defaultProfile, saveProfile, writeFirstRunBuild,
  profileBuildKey, FIRST_EDITION_WELCOME_PENDING_KEY, FIRST_EDITION_WELCOME_KEY, syncProfile, pendingTopicsUpdate,
  setTopicsProgress, topicsProgressFor, loadPack, validatePack, currentEdition,
  TOPICS_DRAFT_KEY, TOPICS_PENDING_KEY, BUILD_GENERAL_FALLBACK_KEY, showGeneralEditionFallback,
  renderInterestBuildScreen, renderInterestConfirmScreen, route, showToast,
  get interestDraft() { return interestDraft; },
  set interestDraft(value) { interestDraft = value; },
  get interestProfile() { return interestProfile; },
  set interestProfile(value) { interestProfile = value; },
  get interestInterpretController() { return interestInterpretController; },
  set interestInterpretController(value) { interestInterpretController = value; },
  get interestInterpretBusy() { return interestInterpretBusy; },
  set interestInterpretBusy(value) { interestInterpretBusy = value; },
  get interestInterpretError() { return interestInterpretError; },
  set interestInterpretError(value) { interestInterpretError = value; },
  get interestInterpretStatus() { return interestInterpretStatus; },
  set interestInterpretStatus(value) { interestInterpretStatus = value; },
  get buildAttempt() { return buildAttempt; },
  set buildAttempt(value) { buildAttempt = value; },
  get buildStartedAt() { return buildStartedAt; },
  set buildStartedAt(value) { buildStartedAt = value; },
  get buildTimeoutHandle() { return buildTimeoutHandle; },
  set buildTimeoutHandle(value) { buildTimeoutHandle = value; },
  get buildInProgress() { return buildInProgress; },
  set buildInProgress(value) { buildInProgress = value; },
  get buildNotice() { return buildNotice; },
  set buildNotice(value) { buildNotice = value; },
  get buildQuestion() { return buildQuestion; },
  set buildQuestion(value) { buildQuestion = value; },
  get removedBuildTopic() { return removedBuildTopic; },
  set removedBuildTopic(value) { removedBuildTopic = value; },
  get topicsError() { return topicsError; },
  set topicsError(value) { topicsError = value; },
  get topicsUpdating() { return topicsUpdating; },
  set topicsUpdating(value) { topicsUpdating = value; },
  get topicsProgress() { return topicsProgress; },
  set topicsProgress(value) { topicsProgress = value; },
  get topicsDraft() { return topicsDraft; },
  set topicsDraft(value) { topicsDraft = value; },
  get pack() { return pack; },
  set pack(value) { pack = value; },
  get state() { return state; },
  set state(value) { state = value; },
});
const {
  interestOriginHash,
  navigateInterest,
  renderInterestTopbar,
  renderInterestDescribe,
  renderInterestBuild,
  setBuildStep,
  runFirstBuild,
  startBuildGeneration,
  removeBuildTopic,
  updateDescribeInterpretationState,
  cancelInterestInterpretation,
  runInterestInterpretation,
  updateInterestMuted,
  persistInterestDraft,
  renderInterestConfirm,
} = interestFlowController;

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
    const summary = article.querySelector('.today-run-meta');
    if (summary) {
      const storyCount = (Array.isArray(edition.sections) ? edition.sections : []).filter(section => section.kind !== 'link').length;
      summary.textContent = `${storyCount} ${storyCount === 1 ? 'story' : 'stories'} · about ${minutes} min`;
    }
    const listen = article.querySelector('.today-listen-pill');
    if (listen) {
      listen.dataset.listenEstimate = `${minutes} min`;
      listen.dataset.durationLabel = '';
      const label = listen.querySelector('.mode-label');
      if (label && (label.textContent === 'Play' || label.textContent.startsWith('Play ·') || label.textContent.startsWith('Play about'))) label.textContent = 'Play briefing';
      listen.setAttribute('aria-label', 'Play briefing');
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

const episodeJobController = createEpisodeJobController({
  node, document, content, renderOnDemandScreen, rememberEpisodeLength, loadEpisodeCards, EPISODE_MODES,
  interpret, defaultProfile, syncProfile, route, syncOnDemandBadge, navigate, announceEpisodeStageView,
  EPISODE_STAGE_FIXTURES, episodeStageIndex, episodeStageTitle, markEpisodeUnheard,
  updateEpisodeMakingScreen, renderEpisodeMakingScreen, link, cancelEpisodeJob, player, validateProfile, saveProfile,
  get episodeDraft() { return episodeDraft; },
  set episodeDraft(value) { episodeDraft = value; },
  get episodeJob() { return episodeJob; },
  set episodeJob(value) { episodeJob = value; },
  get episodeJobTimer() { return episodeJobTimer; },
  set episodeJobTimer(value) { episodeJobTimer = value; },
  get episodeRequestBusy() { return episodeRequestBusy; },
  set episodeRequestBusy(value) { episodeRequestBusy = value; },
  get episodeRequestBlocked() { return episodeRequestBlocked; },
  set episodeRequestBlocked(value) { episodeRequestBlocked = value; },
  get episodeRequestError() { return episodeRequestError; },
  set episodeRequestError(value) { episodeRequestError = value; },
  get interestProfile() { return interestProfile; },
  set interestProfile(value) { interestProfile = value; },
  get pack() { return pack; },
  set pack(value) { pack = value; },
});
const {
  renderEpisodeRequestPage,
  episodeProfileTopic,
  isEpisodeInDailyBriefing,
  setEpisodeDailyBriefing,
  openEpisodeStyleSheet,
  episodeModeStyle,
  startEpisodeJob,
  announceEpisodeStage,
  pollEpisodeJob,
  episodeMakingRetry,
  updateEpisodeMakingView,
  playStreamingChapter,
  renderEpisodeMakingPage,
} = episodeJobController;

async function renderEpisodeRoute(routeName) {
  await renderEpisodeRouteScreen(routeName, {
    content, node, location, episodeHistorySnapshot, renderEpisodeHistory, renderEpisodeMakingFromHistory,
    renderRouteFailure: (title, message) => renderEpisodeRouteFailureScreen({ content, node, link, title, message, onRetry: route }),
    renderMissing, navigate, player, isEpisodeInDailyBriefing, setEpisodeDailyBriefing,
    onEpisodeHeard: markEpisodeHeard,
    setEpisodeDeckCleanup: cleanup => { episodeDeckCleanup = cleanup; },
  });
}

function renderEpisodeMakingFromHistory(job) {
  renderEpisodeMakingFromHistoryScreen({ content, node, link, job });
}

function renderEpisodeHistory(result, available, corrections = []) {
  const jobs = Array.isArray(result?.jobs) ? result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string') : [];
  episodeHistorySnapshot = { result: result && typeof result === 'object' ? result : {}, corrections, jobs };
  renderEpisodeHistoryScreen({
    content, node, link, result: episodeHistorySnapshot.result, available, corrections,
    filter: episodeHistoryFilter, searchQuery: episodeHistorySearch, savedIds: state.savedIds,
    onRetry: () => void renderEpisodeRoute('episodes'),
    onFilter: key => { episodeHistoryFilter = key; renderEpisodeHistory(result, available, corrections); },
    onSearch: value => { episodeHistorySearch = value; renderEpisodeHistory(result, available, corrections); },
    onSave: (id, revisionId) => { toggleSave(id, revisionId, false); renderEpisodeHistory(result, available, corrections); },
    onDelete: (id, title) => {
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
    },
  });
}

function resetBrowserChoices() {
  player.close(); clearEditionCache(); state = resetState(); clearProfile(); clearFirstRunBuild();
  localStorage.removeItem(DAILY_TIME_KEY); localStorage.removeItem(TOPICS_PENDING_KEY);
  sessionStorage.removeItem(TOPICS_DRAFT_KEY);
  topicsDraft = null; topicsProgress = ''; topicsError = ''; topicsUpdating = false;
  buildInProgress = false; buildAttempt++; interestProfile = null; navigate('#preferences');
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
const routeRestoration = {
  window, content, document, CSS,
  revealStoryMore, paragraphAt, currentRevision, trackReadingPlace,
  get pack() { return pack; },
  get pendingScrollRestore() { return pendingScrollRestore; },
  set pendingScrollRestore(value) { pendingScrollRestore = value; },
  get pendingSourceFocusID() { return pendingSourceFocusID; },
  set pendingSourceFocusID(value) { pendingSourceFocusID = value; },
  get pendingReturnHeadingFocus() { return pendingReturnHeadingFocus; },
  set pendingReturnHeadingFocus(value) { pendingReturnHeadingFocus = value; },
  get pendingTranscriptPlacement() { return pendingTranscriptPlacement; },
  set pendingTranscriptPlacement(value) { pendingTranscriptPlacement = value; },
  get pendingPlayerReturnFocus() { return pendingPlayerReturnFocus; },
  set pendingPlayerReturnFocus(value) { pendingPlayerReturnFocus = value; },
  setRestoringRoute(value) { restoringRoute = value; },
};

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
  const routeHash = location.hash || '#today';
  const routeName = decodeURIComponent(routeHash.slice(1));
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
  const routeDescriptor = describeRoute(routeHash, history.state?.routeContext, rootForHash);
  const { episodeRoute, onDemandRoute, group, editionKind, isReader, pageTitle } = routeDescriptor;
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
  document.body.classList.toggle('is-reader', isReader);
  document.querySelector('[data-page-title]').textContent = pageTitle;
  document.body.classList.toggle('is-today', routeName === 'today');
  document.body.classList.toggle('is-topics', routeName === 'topics');
  document.body.classList.toggle('is-episodes-list', routeName === 'episodes');
  document.title = `${pageTitle} · Personal briefing`;
  // Interface · pass 010 navigation: Settings is opened by the gear, so no reader tab is current there.
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
    currentEdition, filterGeneralEditionForProfile: (edition, profile) => filterGeneralEditionForProfile(edition, profile, pack.topics),
    buildNotice, setBuildNotice: value => { buildNotice = value; },
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
    getState: () => state, interestProfile, editionHistoryLoaded, pack, dailyBriefings: () => dailyBriefings(pack?.editions || []), firstReadableSection,
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
  const viewState = historyViews.get(history.state?.viewKey) || history.state?.viewState || history.state?.routeContext?.restore;
  restoreRouteView(routeRestoration, routeName, viewState);
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
