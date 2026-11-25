export function renderTodayScreen(deps) {
  const {
    content, node, link, interestProfile, pendingTopicsUpdate, buildInProgress, todayListSkeleton,
    getState, setState, FIRST_EDITION_WELCOME_KEY, profileBuildKey, BUILD_GENERAL_FALLBACK_KEY,
    currentEdition, filterGeneralEditionForProfile, buildNotice, setBuildNotice,
    startTodayBuildAfterStatusCheck, startBuildGeneration, renderNoStoryExplanations,
    profileTopicNames, BUILD_GENERAL_FALLBACK_NOTE, navigate, writeFirstRunBuild,
    renderMissing, pack, localStorage, sessionStorage, editionJustUpdatedAt,
    buildChipLabel, topicIdFor, toggleSave, saveState, recordSignal, signalEvents,
    dailyTimeValue, openSource, player, getEditionMode, listeningAvailable, renderToday,
    listenEstimateWithUnit, listenEstimate, listenControlLabel, formatElapsed,
    invalidatedEditionAudio, startEditionListening, startRowListening, visibleSections,
    firstReadableSection, focusToken, withNarrationTopics, personalized, readableEdition, rememberEditionMode,
    cacheIdentity, writeEditionCache, showingCachedPack, getCachedPackIdentity,
    setCachedPackIdentity, refreshTodayRunSummary, markDailyEditionSeen,
  } = deps;
  const expectedProfile = interestProfile;
  const builtFor = localStorage.getItem('briefing:v1:built-profile-key');
  const expectedProfileKey = expectedProfile ? profileBuildKey(expectedProfile) : null;
  const welcomeProfileKey = sessionStorage.getItem(FIRST_EDITION_WELCOME_KEY);
  const profileMatches = expectedProfile && builtFor === expectedProfileKey;
  const isGeneralFallback = Boolean(expectedProfile && localStorage.getItem(BUILD_GENERAL_FALLBACK_KEY) === expectedProfileKey);
  const candidateEdition = currentEdition('daily');
  let edition = expectedProfile ? (candidateEdition?.generated === true ? candidateEdition : null) : candidateEdition;
  const showingPreviousEdition = Boolean(expectedProfile && edition?.generated === true && !profileMatches);
  const firstEditionWelcome = Boolean(welcomeProfileKey && welcomeProfileKey === expectedProfileKey && edition?.generated === true);
  let showUnfilteredGeneralFallback = false;
  if (isGeneralFallback && edition?.generated === true) {
    const matchedEdition = filterGeneralEditionForProfile(edition, expectedProfile);
    if (matchedEdition.sections.length) edition = matchedEdition;
    else showUnfilteredGeneralFallback = true;
  }
  if (!edition && pendingTopicsUpdate()) {
    const panel = node('section', null, 'today-build-progress');
    panel.append(node('h1', buildInProgress ? 'Updating… about a minute' : 'Your briefing needs an update'));
    if (buildInProgress) panel.append(todayListSkeleton());
    else panel.append(link('Update my briefing', '#topics', 'button-link'));
    content.append(panel); return;
  }
  if (!edition) {
    if (expectedProfile && !buildNotice) {
      if (buildInProgress) {
        const panel = node('section', null, 'today-build-progress');
        panel.setAttribute('role', 'status');
        panel.append(node('h1', 'Your first briefing is being written'), node('p', 'It’s usually ready in a few minutes. You can leave this page; it will appear when ready.'));
        const steps = node('ol', null, 'today-preparing-steps');
        for (const label of ['Finding stories', 'Reading sources', 'Writing your briefing']) steps.append(node('li', label));
        panel.append(steps);
        content.append(panel);
        return;
      }
      const panel = node('section', null, 'today-service-check');
      panel.setAttribute('role', 'status');
      panel.append(node('p', 'Checking today’s briefing status…'));
      content.append(panel);
      startTodayBuildAfterStatusCheck(expectedProfile, panel);
      return;
    }
    if (buildNotice && expectedProfile) {
      const panel = node('section', null, 'today-build-error');
      panel.append(node('h1', 'Today’s stories did not load'));
      const retry = node('button', 'Retry', 'primary'); retry.type = 'button';
      retry.addEventListener('click', () => {
        setBuildNotice(''); sessionStorage.removeItem('briefing:build-error');
        const firstRun = localStorage.getItem('briefing:v1:built-profile-key') !== profileBuildKey(expectedProfile);
        navigate('#interests/build', { root: 'today', parentHash: '#today', firstRun: true });
        if (firstRun) writeFirstRunBuild(expectedProfile);
        void startBuildGeneration(expectedProfile, null, { firstRun });
      });
      panel.append(retry); content.append(panel);
      renderNoStoryExplanations(panel, expectedProfile);
      return;
    }
    if (expectedProfile) {
      const panel = node('section', null, 'today-no-stories');
      panel.append(node('h1', 'Nothing new today'), node('p', `Nothing new today on ${profileTopicNames(expectedProfile)}. Your next briefing is tomorrow morning.`));
      const edit = link('Edit', '#interests', 'button-link'); panel.append(edit);
      renderNoStoryExplanations(panel, expectedProfile); content.append(panel); return;
    }
    return renderMissing();
  }
  if (isGeneralFallback && edition && !edition.sections.length) {
    const empty = node('section', null, 'today-no-stories');
    empty.append(node('h1', 'Nothing new today'));
    const note = node('p', BUILD_GENERAL_FALLBACK_NOTE, 'today-general-fallback-note'); note.setAttribute('role', 'status'); empty.append(note);
    empty.append(node('p', `Nothing new today on ${profileTopicNames(expectedProfile)}. Your next briefing is tomorrow morning.`));
    const built = node('p', null, 'built-from-words'); built.append(document.createTextNode('Built from your words · '), link('Edit', '#interests', 'built-from-edit')); empty.append(built);
    renderNoStoryExplanations(empty, expectedProfile); content.append(empty); return;
  }
  const weekly = currentEdition('weekly');
  const weeklyForToday = edition.generated === true ? (weekly?.generated === true ? weekly : null) : (interestProfile ? null : weekly);
  const nextWeekly = weekly?.week_start ? (() => {
    const date = new Date(`${weekly.week_start}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + 13);
    return new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'UTC' }).format(date);
  })() : 'Sunday';
  const body = node('div'); content.append(body);
  const todayTopics = [...pack.topics.map(topic => {
    const preference = interestProfile?.topics?.find(item => item.id === topic.id);
    return preference ? { ...topic, label: buildChipLabel(preference), scope: preference.scope } : topic;
  }), ...(interestProfile?.topics || []).filter(preference => !pack.topics.some(topic => topic.id === preference.id))
    .map(preference => ({ ...preference, label: buildChipLabel(preference) }))];
  const feedbackTopicId = section => interestProfile?.topics.find(topic => section.topic_ids?.includes(topic.id))?.id || topicIdFor(edition, section.id);
  renderToday(body, edition, todayTopics, {
    timezone: getState().timezone,
    justUpdated: editionJustUpdatedAt > 0 && Date.now() - editionJustUpdatedAt < 60_000,
    firstEditionWelcome,
    followedTopicCount: interestProfile?.topics?.length ?? getState().selectedIds.length,
    save: (id, revision) => toggleSave(id, revision, false),
    isSaved: id => getState().savedIds.includes(id),
    feedback: (id, value) => { getState().feedback[id] = value; setState(saveState(getState())); },
    getFeedback: id => getState().feedback[id],
    interestFeedback: (type, section) => recordSignal(type, section.id, feedbackTopicId(section)),
    getInterestFeedback: section => signalEvents().filter(event => event.storyId === section.id && event.topicId === feedbackTopicId(section) && ['more', 'less'].includes(event.type)).at(-1)?.type || null,
    weekly: weeklyForToday,
    nextWeekly,
    nextBriefingTime: dailyTimeValue(),
    sources: pack.sources,
    openSource,
    openEpisodeRequest: () => navigate('#ask-episode', { root: 'today', parentHash: '#today', parentScroll: window.scrollY,
      parentFocus: { type: 'href', id: '#ask-episode' } }),
    // A generated edition has already been matched to the exact profile key above.
    // Its sections are the build result, so don't narrow them a second time using
    // today's topic preferences (which may have come from an older browser save).
    profile: (edition.generated === true && (profileMatches || showingPreviousEdition)) || showUnfilteredGeneralFallback ? null : interestProfile,
    lastMode: getEditionMode(edition.id),
    listeningAvailable: listeningAvailable(),
    listeningComplete: player.item?.kind === 'daily' && player.item.id === edition.id
      && player.item.revision_id === edition.revision_id && ['complete', 'finished'].includes(player.status),
    listenEstimate: listenEstimateWithUnit,
    listenState: item => {
      const label = listenControlLabel(item);
      const estimate = listenEstimate(visibleSections(item).filter(section => section.kind !== 'link').map(section => `${section.heading} ${section.body || ''}`).join(' '));
      const resume = label === 'Pause' ? formatElapsed(getState().progress[item.id]?.seconds)
        : label.startsWith('Resume') ? label.replace(/^Resume\s*[·]?\s*/, '').replace(/\s+in$/, '') : '';
      const detail = label === 'Audio paused' ? 'Audio paused' : resume ? `Resume ${resume}` : `${estimate} min`;
      return { label, detail, ariaLabel: `${label} today's briefing${label === 'Listen' ? `, ${estimate} minutes` : ''}` };
    },
    rememberMode: rememberEditionMode,
    todayIsWeekend: /^(Sat|Sun)$/.test(new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: getState().timezone }).format(new Date())),
    listenEdition: item => { if (!invalidatedEditionAudio.has(item.id)) startEditionListening(item); },
    listenStory: sectionId => { if (!invalidatedEditionAudio.has(edition.id)) startRowListening(edition, sectionId); },
    isRead: (id, sectionId, revision) => {
      const place = getState().readingProgress[id];
      if (place?.revisionId !== revision) return false;
      const sections = visibleSections(edition).filter(section => section.kind !== 'link');
      const currentIndex = sections.findIndex(item => item.id === place.storyId);
      const rowIndex = sections.findIndex(item => item.id === sectionId);
      return getState().finishedIds.includes(`${id}:${revision}`) || (currentIndex >= 0 && rowIndex >= 0 && rowIndex < currentIndex);
    },
    readingPlace: (id, revision) => {
      const place = getState().readingProgress[id];
      return place?.revisionId === revision ? place.storyId : null;
    },
    openReader: sectionId => {
      const hash = `#edition/daily/${encodeURIComponent(edition.id)}/${encodeURIComponent(sectionId || firstReadableSection(edition)?.id || '')}`;
      navigate(hash, { root: 'today', parentHash: '#today', parentScroll: window.scrollY, parentFocus: focusToken(), sectionId });
    },
    openBriefing: (editionId, sectionId, opener) => {
      const hash = `#edition/daily/${encodeURIComponent(editionId)}/${encodeURIComponent(sectionId)}`;
      navigate(hash, { root: 'today', parentHash: '#today', parentScroll: window.scrollY,
        parentFocus: { type: 'href', id: opener?.getAttribute('href') || hash }, sectionId });
    },
    openWeekly: opener => navigate('#weekly', { root: 'today', parentHash: '#today', parentScroll: window.scrollY,
      parentFocus: focusToken() || { type: 'href', id: opener?.getAttribute('href') || '#weekly' } }),
  });
  player.offer(withNarrationTopics(readableEdition(personalized(edition))));
  if (!showingCachedPack && getCachedPackIdentity() !== pack) {
    writeEditionCache(cacheIdentity(), pack);
    setCachedPackIdentity(pack);
  }
  const article = body.querySelector('.today-page');
  if (article) {
    void refreshTodayRunSummary(edition, article);
    if (article.querySelector('.today-contents li')) void markDailyEditionSeen(edition);
    if (isGeneralFallback) {
      const note = node('p', BUILD_GENERAL_FALLBACK_NOTE, 'today-general-fallback-note'); note.setAttribute('role', 'status');
      const title = article.querySelector('h1');
      if (title) title.after(note); else article.prepend(note);
    } else if (showingPreviousEdition) {
      const notice = node('p', 'Your updated topics are still being prepared.', 'today-previous-edition-note');
      notice.setAttribute('role', 'status');
      const playback = article.querySelector('.today-primary');
      if (playback) playback.after(notice); else article.prepend(notice);
    } else if (buildNotice) {
      const notice = node('p', null, 'today-build-error-line'); notice.setAttribute('role', 'status');
      notice.append(document.createTextNode('Today’s stories did not load · '));
      const retry = node('button', 'Retry', 'today-build-retry'); retry.type = 'button';
      retry.addEventListener('click', () => { setBuildNotice(''); sessionStorage.removeItem('briefing:build-error'); navigate('#interests/build', { root: 'today', parentHash: '#today', firstRun: true }); void startBuildGeneration(expectedProfile); });
      notice.append(retry); article.prepend(notice);
    }
    if (!article.querySelector('.today-contents li')) {
      body.replaceChildren();
      const empty = node('section', null, 'today-no-stories');
      empty.append(node('h1', 'Nothing new today'), node('p', `Nothing new today on ${profileTopicNames(expectedProfile)}. Your next briefing is tomorrow morning.`));
      if (isGeneralFallback) { const title = empty.querySelector('h1'); const note = node('p', BUILD_GENERAL_FALLBACK_NOTE, 'today-general-fallback-note'); note.setAttribute('role', 'status'); title?.after(note); }
      renderNoStoryExplanations(empty, expectedProfile); body.append(empty);
    }
  }
  if (body.querySelector('.today-welcome-note')) sessionStorage.removeItem(FIRST_EDITION_WELCOME_KEY);
}

