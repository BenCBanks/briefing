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
import { accountApi as createAccountApi } from './screens/account.js';
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
import { createRouteLifecycleController } from './screens/route-lifecycle.js';
import { createNavigationController } from './screens/navigation-controller.js';
import { createDetailRouteController } from './screens/detail-routes.js';
import { createEditionDataLoader } from './edition/data-loader.js';
import { createEditionRefreshController } from './edition/refresh-controller.js';
import { renderRouteContent } from './screens/route-renderer.js';
import { createTopicsManagementController } from './screens/topics-management-controller.js';
import { createStoryActionsController } from './screens/story-actions-controller.js';
import { createTodayDataController } from './screens/today-data.js';
import { createEpisodeStateController } from './screens/episode-state.js';
import { createCorrectionsScreenController } from './screens/corrections-screen.js';

const app = document.querySelector('#app');
const content = document.querySelector('#content');
const correctionsScreenController = createCorrectionsScreenController({ content, location, renderCorrections });
let storyActionsController = null;
let todayDataController = null;
let episodeDraft = { prompt: '', minutes: 5 };
let episodeJob = null;
let episodeJobTimer = null;
let episodeHistorySnapshot = null;
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
const accountApi = createAccountApi();
let accountsEnabled = false;
let accountReader = null;
let accountModePending = true;
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
const EPISODE_LENGTH_KEY = 'briefing:v1:last-episode-length';
const EPISODE_UNHEARD_KEY = 'briefing:v1:episode-unheard';
const editionDataController = createEditionDataLoader({
  get pack() { return pack; }, set pack(value) { pack = value; },
  get interestProfile() { return interestProfile; },
  get editionCheckSucceeded() { return editionCheckSucceeded; }, set editionCheckSucceeded(value) { editionCheckSucceeded = value; },
  get editionHistoryLoaded() { return editionHistoryLoaded; }, set editionHistoryLoaded(value) { editionHistoryLoaded = value; },
  get editionHistoryPromise() { return editionHistoryPromise; }, set editionHistoryPromise(value) { editionHistoryPromise = value; },
  localStorage, profileBuildKey, location, route, document, node,
});
const episodeStateController = createEpisodeStateController({
  localStorage, profileBuildKey, FIRST_RUN_BUILD_KEY, DAILY_TIME_KEY, EPISODE_LENGTH_KEY, EPISODE_UNHEARD_KEY,
  nav, episodeHistoryState,
  get episodeDraft() { return episodeDraft; },
  get episodeJob() { return episodeJob; },
  get episodeHistorySnapshot() { return episodeHistorySnapshot; },
});
function readFirstRunBuild(...args) { return episodeStateController.readFirstRunBuild(...args); }
function writeFirstRunBuild(...args) { firstRunPending = episodeStateController.writeFirstRunBuild(...args); return firstRunPending; }
function clearFirstRunBuild(...args) { firstRunPending = null; return episodeStateController.clearFirstRunBuild(...args); }
function dailyTimeValue(...args) { return episodeStateController.dailyTimeValue(...args); }
function formatEasternTime(...args) { return episodeStateController.formatEasternTime(...args); }
function nextDailyRunTime(...args) { return episodeStateController.nextDailyRunTime(...args); }
function readLastEpisodeLength(...args) { return episodeStateController.readLastEpisodeLength(...args); }
function rememberEpisodeLength(...args) { return episodeStateController.rememberEpisodeLength(...args); }
function episodeUnheardIds(...args) { return episodeStateController.episodeUnheardIds(...args); }
function saveEpisodeUnheardIds(...args) { return episodeStateController.saveEpisodeUnheardIds(...args); }
function syncOnDemandBadge(...args) { return episodeStateController.syncOnDemandBadge(...args); }
function markEpisodeUnheard(...args) { return episodeStateController.markEpisodeUnheard(...args); }
function markEpisodeHeard(...args) { return episodeStateController.markEpisodeHeard(...args); }
let firstRunPending = readFirstRunBuild();
let firstRunRecoveryAvailable = false;
if (firstRunPending && (!interestProfile || firstRunPending.profileKey !== profileBuildKey(interestProfile))) clearFirstRunBuild();
if (firstRunPending) { buildInProgress = true; interestDraft = { profile: interestProfile }; firstRunRecoveryAvailable = true; }

const EPISODE_MODES = [
  { id: 'brief', label: 'Brief', detail: 'The key points, quickly.' },
  { id: 'deep-dive', label: 'Deep dive', detail: 'More context and connections.' },
  { id: 'simple', label: 'Explain it simply', detail: 'Plain language, step by step.' },
];
episodeDraft.minutes = readLastEpisodeLength();
let episodeRequestError = '';
let episodeRequestBusy = false;
let episodeRequestBlocked = false;
let episodeHistoryFilter = 'all';
let episodeHistorySearch = '';
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

function onAccountChange(reader, data = {}) {
  const priorID = accountReader ? accountReader.id || accountReader.name || 'authenticated' : null;
  const priorProfileKey = interestProfile ? profileBuildKey(interestProfile) : null;
  accountReader = reader || null;
  if (reader && data.topics) interestProfile = saveProfile(data.topics, { server: false });
  else if (!reader || !data.topics) { clearProfile(); interestProfile = null; }
  const nextProfileKey = interestProfile ? profileBuildKey(interestProfile) : null;
  const gateRoute = ['#today', '#ask', '#ask-episode'].includes(location.hash);
  const nextID = accountReader ? accountReader.id || accountReader.name || 'authenticated' : null;
  if (gateRoute && (priorID !== nextID || priorProfileKey !== nextProfileKey)) {
    window.dispatchEvent(new Event('hashchange'));
  }
}

async function initializeAccountFeature() {
  try {
    const session = await accountApi.sessionState();
    if (!session.enabled) return;
    accountsEnabled = true;
    if (session.unavailable) {
      accountReader = null; clearProfile(); interestProfile = null;
      window.dispatchEvent(new Event('hashchange'));
      return;
    }
    accountReader = session.reader || null;
    if (accountReader) {
      const [topics] = await Promise.all([accountApi.loadTopics(), accountApi.loadHistory()]);
      if (topics) interestProfile = saveProfile(topics, { server: false });
      else { clearProfile(); interestProfile = null; }
    } else {
      clearProfile(); interestProfile = null;
    }
  } catch {
    // If account mode is on but session lookup fails, fail closed to the sign-in view.
    if (!accountsEnabled) return;
    accountReader = null; clearProfile(); interestProfile = null;
  } finally {
    accountModePending = false;
    window.dispatchEvent(new Event('hashchange'));
  }
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
  return storyActionsController.showWhySheet(section, edition);
}

function showStoryActionsSheet(section, edition, trigger) {
  return storyActionsController.showStoryActionsSheet(section, edition, trigger);
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

function todayListSkeleton(...args) { return todayDataController.todayListSkeleton(...args); }
function startTodayBuildAfterStatusCheck(...args) { return todayDataController.startTodayBuildAfterStatusCheck(...args); }
function markDailyEditionSeen(...args) { return todayDataController.markDailyEditionSeen(...args); }
async function fetchSavedServerProfile(...args) { return todayDataController.fetchSavedServerProfile(...args); }
async function showGeneralEditionFallback(...args) { return todayDataController.showGeneralEditionFallback(...args); }
function renderNoStoryExplanations(...args) { return todayDataController.renderNoStoryExplanations(...args); }
async function refreshTodayRunSummary(...args) { return todayDataController.refreshTodayRunSummary(...args); }
async function refreshLastUpdated(...args) { return todayDataController.refreshLastUpdated(...args); }
function formatLastUpdated(...args) { return todayDataController.formatLastUpdated(...args); }

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

todayDataController = createTodayDataController({
  node, link, content, navigate, startBuildGeneration, seenEditionMarksInFlight,
  validateProfile, loadPack, currentEdition, profileBuildKey,
  localStorage, sessionStorage, BUILD_GENERAL_FALLBACK_KEY,
  get buildAttempt() { return buildAttempt; },
  get pack() { return pack; }, set pack(value) { pack = value; },
  get buildNotice() { return buildNotice; }, set buildNotice(value) { buildNotice = value; },
  get buildTimeoutHandle() { return buildTimeoutHandle; }, set buildTimeoutHandle(value) { buildTimeoutHandle = value; },
});

storyActionsController = createStoryActionsController({
  node, document, window, location, navigator, recordSignal, saveProfile, invalidateTopicsEdition,
  startBuildGeneration, focusToken, navigate, rootForHash, showToast,
  get pack() { return pack; },
  get interestProfile() { return interestProfile; }, set interestProfile(value) { interestProfile = value; },
});

function renderCorrectionsPage(...args) { return correctionsScreenController.renderCorrectionsPage(...args); }

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

function route(...args) { return routeLifecycleController.route(...args); }

const routeLifecycleController = createRouteLifecycleController({
  window, content, document, CSS, nav, location, history, historyViews, player, sessionStorage,
  invalidateMaterialCorrections, finishStoryVisit, trackRouteSignals, focusToken, navigate,
  startBuildGeneration, showToast, rootForHash,
  renderRouteContent: (routeName, descriptor) => renderRouteContent(routeName, {
    accountsEnabled, accountReader, accountApi, onAccountChange, accountModePending,
    episodeRoute: descriptor.episodeRoute, content, node, link, history, interestProfile, pendingTopicsUpdate, buildInProgress,
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
  }),
  get pack() { return pack; },
  get episodeDeckCleanup() { return episodeDeckCleanup; }, set episodeDeckCleanup(value) { episodeDeckCleanup = value; },
  get readingObserver() { return readingObserver; }, set readingObserver(value) { readingObserver = value; },
  get interestInterpretController() { return interestInterpretController; }, set interestInterpretController(value) { interestInterpretController = value; },
  set interestInterpretBusy(value) { interestInterpretBusy = value; },
  get pendingRouteContext() { return pendingRouteContext; }, set pendingRouteContext(value) { pendingRouteContext = value; },
  get transcriptReturnTo() { return transcriptReturnTo; }, set transcriptReturnTo(value) { transcriptReturnTo = value; },
  get transcriptReturnHash() { return transcriptReturnHash; }, set transcriptReturnHash(value) { transcriptReturnHash = value; },
  get pendingScrollRestore() { return pendingScrollRestore; }, set pendingScrollRestore(value) { pendingScrollRestore = value; },
  get pendingPlayerReturnFocus() { return pendingPlayerReturnFocus; }, set pendingPlayerReturnFocus(value) { pendingPlayerReturnFocus = value; },
  get deepDiveReturnTo() { return deepDiveReturnTo; }, set deepDiveReturnTo(value) { deepDiveReturnTo = value; },
  get pendingSourceFocusID() { return pendingSourceFocusID; }, set pendingSourceFocusID(value) { pendingSourceFocusID = value; },
  get pendingReturnHeadingFocus() { return pendingReturnHeadingFocus; }, set pendingReturnHeadingFocus(value) { pendingReturnHeadingFocus = value; },
  get pendingTranscriptPlacement() { return pendingTranscriptPlacement; }, set pendingTranscriptPlacement(value) { pendingTranscriptPlacement = value; },
  get firstRunPending() { return firstRunPending; }, set firstRunPending(value) { firstRunPending = value; },
  get firstRunRecoveryAvailable() { return firstRunRecoveryAvailable; }, set firstRunRecoveryAvailable(value) { firstRunRecoveryAvailable = value; },
  get interestProfile() { return interestProfile; },
  getRouteRestorationHelpers: () => ({ revealStoryMore, paragraphAt, currentRevision, trackReadingPlace }),
});
routeLifecycleController.install();
void initializeAccountFeature();

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
