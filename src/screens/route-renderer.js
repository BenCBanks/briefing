// Dispatch the active hash route to its screen while the app owns shared state.
import { renderAccountAccess } from './account.js';

export function renderRouteContent(routeName, app) {
  const { episodeRoute, content, node, link, history, renderTodayScreen, interestProfile, pendingTopicsUpdate, buildInProgress,
    todayListSkeleton, getState, setState, FIRST_EDITION_WELCOME_KEY, profileBuildKey,
    BUILD_GENERAL_FALLBACK_KEY, currentEdition, filterGeneralEditionForProfile, pack,
    buildNotice, setBuildNotice, startTodayBuildAfterStatusCheck, startBuildGeneration,
    renderNoStoryExplanations, profileTopicNames, BUILD_GENERAL_FALLBACK_NOTE, navigate,
    writeFirstRunBuild, renderMissing, localStorage, sessionStorage, editionJustUpdatedAt,
    buildChipLabel, topicIdFor, toggleSave, saveState, recordSignal, signalEvents,
    dailyTimeValue, openSource, player, getEditionMode, listeningAvailable, renderToday,
    listenEstimateWithUnit, listenEstimate, listenControlLabel, formatElapsed,
    invalidatedEditionAudio, startEditionListening, startRowListening, visibleSections,
    firstReadableSection, focusToken, withNarrationTopics, personalized, readableEdition,
    rememberEditionMode, cacheIdentity, writeEditionCache, showingCachedPack,
    getCachedPackIdentity, setCachedPackIdentity, refreshTodayRunSummary, markDailyEditionSeen,
    renderEpisodeRoute, renderEpisodeRequestPage, renderEpisodeMakingPage, renderTopicsPage,
    renderCorrectionsPage, renderEditionPage, currentRevision, renderDeepDivePage,
    renderTopicDetailPage, renderSourcePage, renderInterestDescribe, renderInterestBuild,
    renderSettingsScreen, dailyTimeValue: getDailyTimeValue, formatEasternTime,
    nextDailyRunTime, resetBehaviorWeighting, NARRATOR_KEY, refreshLastUpdated,
    resetBrowserChoices, saveState: persistState, sessionStorage: sessionStore,
    renderInterestsPage, renderEarlierScreen, currentItems, visibleSections: getVisibleSections,
    editionDateRange, hasMaterialCorrection, editionHistoryLoaded,
    dailyBriefings, firstReadableSection: getFirstReadableSection, revisionDate,
    briefingDate, displayDate, getLibraryFilter, setLibraryFilter,
    getLibrarySearchQuery, setLibrarySearchQuery, loadEpisodeCards, canListen,
    listenEstimate: getListenEstimate, createPlayIcon, startListening, browserCanSpeak,
    loadEditionHistory,
    accountsEnabled, accountReader, accountApi, onAccountChange, accountModePending,
  } = app;

  if (accountModePending) {
    content.replaceChildren(node('p', 'Preparing'));
    return;
  }

  if (accountsEnabled && !accountReader && ['today', 'ask', 'ask-episode'].includes(routeName)) {
    renderAccountAccess({ content, node, api: accountApi, gateRoute: routeName, onAccountChange });
    return;
  }

  if (episodeRoute) void renderEpisodeRoute(routeName);
  else if (routeName === 'today' || !routeName) renderTodayScreen({
    content, node, link, interestProfile, pendingTopicsUpdate, buildInProgress, todayListSkeleton,
    getState, setState, FIRST_EDITION_WELCOME_KEY, profileBuildKey, BUILD_GENERAL_FALLBACK_KEY,
    currentEdition, filterGeneralEditionForProfile, buildNotice, setBuildNotice,
    startTodayBuildAfterStatusCheck, startBuildGeneration, renderNoStoryExplanations,
    profileTopicNames, BUILD_GENERAL_FALLBACK_NOTE, navigate, writeFirstRunBuild,
    renderMissing, pack, localStorage, sessionStorage, editionJustUpdatedAt,
    buildChipLabel, topicIdFor, toggleSave, saveState, recordSignal, signalEvents,
    dailyTimeValue: getDailyTimeValue, openSource, player, getEditionMode, listeningAvailable,
    renderToday, listenEstimateWithUnit, listenEstimate, listenControlLabel, formatElapsed,
    invalidatedEditionAudio, startEditionListening, startRowListening, visibleSections,
    firstReadableSection, focusToken, withNarrationTopics, personalized, readableEdition,
    rememberEditionMode, cacheIdentity, writeEditionCache, showingCachedPack,
    getCachedPackIdentity, setCachedPackIdentity, refreshTodayRunSummary, markDailyEditionSeen,
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
    if (edition) renderEditionPage(edition.kind, edition, sectionId);
    else renderMissing();
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
    content, node, link, dailyTimeValue: getDailyTimeValue, formatEasternTime, nextDailyRunTime,
    interestProfile, navigate, resetBehaviorWeighting, player, NARRATOR_KEY, refreshLastUpdated,
    getState, setState, saveState: persistState, localStorage, sessionStorage: sessionStore,
    resetChoices: resetBrowserChoices,
    accountApi,
    onAccountChange,
    accountsEnabled,
  });
  else if (routeName === 'interests') renderInterestsPage();
  else if (routeName === 'library' || routeName === 'earlier') renderEarlierScreen({
    content, node, link, currentItems, visibleSections: getVisibleSections, editionDateRange,
    hasMaterialCorrection, getState, interestProfile, editionHistoryLoaded, pack,
    dailyBriefings: () => dailyBriefings(pack?.editions || []), firstReadableSection: getFirstReadableSection,
    revisionDate, briefingDate, displayDate,
    getLibraryFilter, setLibraryFilter, getLibrarySearchQuery, setLibrarySearchQuery,
    loadEpisodeCards, canListen, listenEstimate: getListenEstimate, createPlayIcon,
    startListening, toggleSave, browserCanSpeak, navigate, loadEditionHistory,
  });
  else renderMissing();
}
