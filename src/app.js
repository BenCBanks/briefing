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
import { createNavigationController } from './screens/navigation-controller.js';
import { createDetailRouteController } from './screens/detail-routes.js';
import { createEditionDataLoader } from './edition/data-loader.js';
import { createEditionRefreshController } from './edition/refresh-controller.js';
import { renderRouteContent } from './screens/route-renderer.js';
import { createTopicsManagementController } from './screens/topics-management-controller.js';

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
const editionDataController = createEditionDataLoader({
  get pack() { return pack; }, set pack(value) { pack = value; },
  get interestProfile() { return interestProfile; },
  get editionCheckSucceeded() { return editionCheckSucceeded; }, set editionCheckSucceeded(value) { editionCheckSucceeded = value; },
  get editionHistoryLoaded() { return editionHistoryLoaded; }, set editionHistoryLoaded(value) { editionHistoryLoaded = value; },
  get editionHistoryPromise() { return editionHistoryPromise; }, set editionHistoryPromise(value) { editionHistoryPromise = value; },
  localStorage, profileBuildKey, location, route, document, node,
});
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
const historyViews = new Map();
let navigationController = null;
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

let editionRefreshController;
function clearNetworkBanner(...args) { return editionRefreshController.clearNetworkBanner(...args); }
function showNetworkBanner(...args) { return editionRefreshController.showNetworkBanner(...args); }

editionRefreshController = createEditionRefreshController({
  app, content, node, window, location, document, navigator, history, player,
  localStorage, sessionStorage, focusToken, profileBuildKey, episodeCardsSkeleton, todayListSkeleton,
  loadPack, fetchSavedServerProfile, currentEdition, getProfile, pendingTopicsUpdate, saveProfile,
  includeNewerCachedEdition, latestGeneratedDaily, newerEdition, invalidateMaterialCorrections,
  withNarrationTopics, saveState, syncListeningControls, syncAudioVerification, route, writeEditionCache,
  get pack() { return pack; }, set pack(value) { pack = value; },
  get showingCachedPack() { return showingCachedPack; }, set showingCachedPack(value) { showingCachedPack = value; },
  get editionCheckSucceeded() { return editionCheckSucceeded; },
  get editionRefreshInFlight() { return editionRefreshInFlight; }, set editionRefreshInFlight(value) { editionRefreshInFlight = value; },
  get lastEditionCheckAt() { return lastEditionCheckAt; }, set lastEditionCheckAt(value) { lastEditionCheckAt = value; },
  get slowNetworkTimer() { return slowNetworkTimer; }, set slowNetworkTimer(value) { slowNetworkTimer = value; },
  get networkBanner() { return networkBanner; }, set networkBanner(value) { networkBanner = value; },
  get interestProfile() { return interestProfile; }, set interestProfile(value) { interestProfile = value; },
  get editionJustUpdatedAt() { return editionJustUpdatedAt; }, set editionJustUpdatedAt(value) { editionJustUpdatedAt = value; },
  get buildAttempt() { return buildAttempt; },
  get buildNotice() { return buildNotice; }, set buildNotice(value) { buildNotice = value; },
  get state() { return state; }, set state(value) { state = value; },
  get cachedPackIdentity() { return cachedPackIdentity; }, set cachedPackIdentity(value) { cachedPackIdentity = value; },
});

function validArray(...args) { return editionDataController.validArray(...args); }
async function readJson(...args) { return editionDataController.readJson(...args); }
async function readGeneratedEdition(...args) { return editionDataController.readGeneratedEdition(...args); }
async function loadEditionHistory(...args) { return editionDataController.loadEditionHistory(...args); }
async function loadPack(...args) { return editionDataController.loadPack(...args); }
function validatePack(...args) { return editionDataController.validatePack(...args); }

function personalized(edition) {
  if (!edition || (!invalidatedEditionAudio.has(edition.id) &&
      !hasMaterialCorrection(edition))) return edition;
  return { ...edition, audio: { ...(edition.audio || {}), state: 'superseded', superseded: true } };
}

navigationController = createNavigationController({
  content, document, window, location, history, rootScroll, historyViews, CSS, HTMLAnchorElement,
  route, immediateParentLabel,
  get returnTo() { return returnTo; }, set returnTo(value) { returnTo = value; },
  get deepDiveReturnTo() { return deepDiveReturnTo; }, set deepDiveReturnTo(value) { deepDiveReturnTo = value; },
  get pendingScrollRestore() { return pendingScrollRestore; }, set pendingScrollRestore(value) { pendingScrollRestore = value; },
  get pendingSourceFocusID() { return pendingSourceFocusID; }, set pendingSourceFocusID(value) { pendingSourceFocusID = value; },
  get pendingReturnHeadingFocus() { return pendingReturnHeadingFocus; }, set pendingReturnHeadingFocus(value) { pendingReturnHeadingFocus = value; },
  get pendingTranscriptPlacement() { return pendingTranscriptPlacement; }, set pendingTranscriptPlacement(value) { pendingTranscriptPlacement = value; },
  get transcriptReturnTo() { return transcriptReturnTo; }, set transcriptReturnTo(value) { transcriptReturnTo = value; },
  get transcriptReturnHash() { return transcriptReturnHash; }, set transcriptReturnHash(value) { transcriptReturnHash = value; },
  get pendingPlayerReturnFocus() { return pendingPlayerReturnFocus; }, set pendingPlayerReturnFocus(value) { pendingPlayerReturnFocus = value; },
  get pendingRouteContext() { return pendingRouteContext; }, set pendingRouteContext(value) { pendingRouteContext = value; },
});
function focusToken(...args) { return navigationController.focusToken(...args); }
function remember(...args) { return navigationController.remember(...args); }
function rootForHash(...args) { return navigationController.rootForHash(...args); }
function navigate(...args) { return navigationController.navigate(...args); }
function navigateTranscript(...args) { return navigationController.navigateTranscript(...args); }
function openSource(...args) { return navigationController.openSource(...args); }
function openDeepDive(...args) { return navigationController.openDeepDive(...args); }

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
function backTo(...args) { return navigationController.backTo(...args); }
function back(...args) { return navigationController.back(...args); }
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

let topicsManagementController;
function invalidateTopicsEdition(...args) { return topicsManagementController.invalidateTopicsEdition(...args); }
function setTopicsProgress(...args) { return topicsManagementController.setTopicsProgress(...args); }
function topicsProgressFor(...args) { return topicsManagementController.topicsProgressFor(...args); }
async function updateTopicsBriefing(...args) { return topicsManagementController.updateTopicsBriefing(...args); }
function renderTopicsPage(...args) { return topicsManagementController.renderTopicsPage(...args); }
function renderInterestsPage(...args) { return topicsManagementController.renderInterestsPage(...args); }

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

let detailRouteController;
detailRouteController = createDetailRouteController({
  node, content, document, window, history, location, CSS, link,
  renderDeepDive, renderTopicDetail, currentItems, currentRevision, transcriptDestination,
  returnFromTranscript, transcriptReturnLabel, rootForHash, immediateParentLabel,
  startListening, canListen, listenEstimate, openSource, toggleSave, trackReadingPlace,
  navigate, updateTopicsBriefing, defaultProfile, loadEditionHistory, back,
  plainDate, displayDate,
  renderMissing,
  get pack() { return pack; },
  get interestProfile() { return interestProfile; },
  get transcriptReturnTo() { return transcriptReturnTo; },
  get deepDiveReturnTo() { return deepDiveReturnTo; },
  get returnTo() { return returnTo; },
  get state() { return state; },
  get episodeDraft() { return episodeDraft; },
  get editionHistoryLoaded() { return editionHistoryLoaded; },
});
function renderDeepDivePage(...args) { return detailRouteController.renderDeepDivePage(...args); }
function renderTopicDetailPage(...args) { return detailRouteController.renderTopicDetailPage(...args); }

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

function renderSourcePage(...args) { return detailRouteController.renderSourcePage(...args); }

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

topicsManagementController = createTopicsManagementController({
  content, document, window, location, node, link,
  get pack() { return pack; },
  get state() { return state; }, set state(value) { state = value; },
  get interestProfile() { return interestProfile; }, set interestProfile(value) { interestProfile = value; },
  get interestDraft() { return interestDraft; }, set interestDraft(value) { interestDraft = value; },
  get interestPageFlags() { return interestPageFlags; }, set interestPageFlags(value) { interestPageFlags = value; },
  get interestPageNotice() { return interestPageNotice; }, set interestPageNotice(value) { interestPageNotice = value; },
  get topicsDraft() { return topicsDraft; }, set topicsDraft(value) { topicsDraft = value; },
  get topicsUpdating() { return topicsUpdating; }, set topicsUpdating(value) { topicsUpdating = value; },
  get topicsError() { return topicsError; }, set topicsError(value) { topicsError = value; },
  get topicsProgress() { return topicsProgress; }, set topicsProgress(value) { topicsProgress = value; },
  get buildInProgress() { return buildInProgress; }, set buildInProgress(value) { buildInProgress = value; },
  get buildAttempt() { return buildAttempt; }, set buildAttempt(value) { buildAttempt = value; },
  history,
  TOPICS_PENDING_KEY, BUILD_GENERAL_FALLBACK_KEY, TOPICS_DRAFT_KEY,
  localStorage, sessionStorage,
  clearEditionCache, player, saveState, saveProfile, defaultProfile,
  currentEdition, currentItems, profileBuildKey, pendingTopicsUpdate,
  renderTopicsScreen, renderInterestsManagementScreen, renderTopics, renderInterests,
  deriveInterestBrief, immediateParentLabel, navigate, rootForHash, route,
  openDeepDive, interpretProfile, startBuildGeneration,
  generateSuggestions, applySuggestion, snoozeSuggestion, resetBehaviorWeighting, clearSuggestionSnoozes,
});

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

function renderMissing(...args) { return detailRouteController.renderMissing(...args); }

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
  renderRouteContent(routeName, {
    episodeRoute, content, node, link, history, interestProfile, pendingTopicsUpdate, buildInProgress,
    todayListSkeleton, renderTodayScreen, getState: () => state, setState: value => { state = value; },
    FIRST_EDITION_WELCOME_KEY, profileBuildKey, BUILD_GENERAL_FALLBACK_KEY, currentEdition,
    filterGeneralEditionForProfile: (edition, profile) => filterGeneralEditionForProfile(edition, profile, pack.topics),
    pack, buildNotice, setBuildNotice: value => { buildNotice = value; }, startTodayBuildAfterStatusCheck,
    startBuildGeneration, renderNoStoryExplanations, profileTopicNames, BUILD_GENERAL_FALLBACK_NOTE,
    navigate, writeFirstRunBuild, renderMissing, localStorage, sessionStorage, editionJustUpdatedAt,
    buildChipLabel, topicIdFor, toggleSave, saveState, recordSignal, signalEvents, dailyTimeValue,
    openSource, player, getEditionMode, listeningAvailable, renderToday, listenEstimateWithUnit,
    listenEstimate, listenControlLabel, formatElapsed, invalidatedEditionAudio, startEditionListening,
    startRowListening, visibleSections, firstReadableSection, focusToken, withNarrationTopics,
    personalized, readableEdition, rememberEditionMode, cacheIdentity, writeEditionCache,
    showingCachedPack, getCachedPackIdentity: () => cachedPackIdentity,
    setCachedPackIdentity: value => { cachedPackIdentity = value; }, refreshTodayRunSummary, markDailyEditionSeen,
    renderEpisodeRoute, renderEpisodeRequestPage, renderEpisodeMakingPage, renderCorrectionsPage,
    renderEditionPage, currentRevision, renderDeepDivePage, renderTopicDetailPage,
    renderSourcePage, renderTopicsPage, renderInterestDescribe, renderInterestBuild,
    renderSettingsScreen, formatEasternTime, nextDailyRunTime, resetBehaviorWeighting, NARRATOR_KEY,
    refreshLastUpdated, resetBrowserChoices, renderInterestsPage, renderEarlierScreen, currentItems,
    editionDateRange, hasMaterialCorrection, editionHistoryLoaded, dailyBriefings,
    revisionDate, briefingDate, displayDate,
    getLibraryFilter: () => libraryFilter, setLibraryFilter: value => { libraryFilter = value; },
    getLibrarySearchQuery: () => librarySearchQuery, setLibrarySearchQuery: value => { librarySearchQuery = value; },
    loadEpisodeCards, canListen, createPlayIcon, startListening, browserCanSpeak, loadEditionHistory,
  });
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
function cacheIdentity(...args) { return editionRefreshController.cacheIdentity(...args); }
function keepReadingPosition(...args) { return editionRefreshController.keepReadingPosition(...args); }
function cachedFailure(...args) { return editionRefreshController.cachedFailure(...args); }
function renderAppLoading(...args) { return editionRefreshController.renderAppLoading(...args); }
async function refreshInitialPack(...args) { return editionRefreshController.refreshInitialPack(...args); }
function revalidateLatestEdition(...args) { return editionRefreshController.revalidateLatestEdition(...args); }

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
