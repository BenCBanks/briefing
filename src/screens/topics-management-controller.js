export function createTopicsManagementController(app) {
  function invalidateTopicsEdition() {
    // Persist the gate before interpretation/network work so reload cannot expose an old edition.
    if (!app.pendingTopicsUpdate()) app.localStorage.setItem(app.TOPICS_PENDING_KEY, JSON.stringify({
      revisions: (app.pack?.editions || []).map(item => item.id + ':' + item.revision_id),
    }));
    app.localStorage.removeItem('briefing:v1:built-profile-key');
    app.localStorage.removeItem(app.BUILD_GENERAL_FALLBACK_KEY);
    app.clearEditionCache();
    app.player.close(); app.state.activeAudio = null; app.state = app.saveState(app.state);
  }

  function setTopicsProgress(message) {
    app.topicsProgress = message;
    const status = app.content.querySelector('.topics-update-stage');
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
    if (app.topicsUpdating) return;
    const attempt = ++app.buildAttempt;
    app.topicsDraft = words; app.sessionStorage.setItem(app.TOPICS_DRAFT_KEY, words);
    app.topicsUpdating = true; app.topicsError = '';
    app.topicsProgress = profile ? 'Saving your interests…' : 'Understanding your words…'; app.buildInProgress = true;
    invalidateTopicsEdition();
    app.route();
    try {
      if (!profile) {
        const result = await app.interpretProfile(words, { topics: app.pack.topics,
          minutes: app.interestProfile?.minutes || app.state.durationMinutes });
        if (attempt !== app.buildAttempt) return;
        if (result.fallbackError || !result.profile?.topics?.length) throw new Error('interpret');
        // Preserve the exact reader-authored sentence and existing pause choices.
        profile = { ...result.profile, words, muted: [...new Map([
          ...(result.profile.muted || []), ...(app.interestProfile?.muted || []).filter(item =>
            app.interestProfile.topics.some(topic => topic.label.toLowerCase() === item.label.toLowerCase())),
        ].map(item => [item.label.toLowerCase(), item])).values()].slice(0, 12) };
      }
      app.interestProfile = app.saveProfile(profile, { server: false });
      setTopicsProgress('Saving your interests…');
      await app.startBuildGeneration(app.interestProfile, attempt, { inline: true, returnToday: true });
    } catch {
      if (attempt !== app.buildAttempt) return;
      app.topicsUpdating = false; app.buildInProgress = false; app.topicsProgress = '';
      app.topicsError = "We couldn't build it right now. Your last briefing is still here.";
      if (app.location.hash === '#topics' || app.location.hash === '#today') app.route();
    }
  }

  function renderTopicsPage() {
    const profileTopics = (app.interestProfile?.topics || app.pack.topics.filter(topic => app.state.selectedIds.includes(topic.id)))
      .filter(topic => (topic.include || []).length || !topic.exclude?.length || topic.mustFollow);
    const daily = app.currentEdition('daily');
    const matched = !app.interestProfile || app.localStorage.getItem('briefing:v1:built-profile-key') === app.profileBuildKey(app.interestProfile);
    app.renderTopicsScreen({
      content: app.content, document: app.document, node: app.node, link: app.link,
      renderTopics: app.renderTopics, profile: app.interestProfile, profileTopics,
      deepDives: app.currentItems(app.pack.deepDives), selectedIds: profileTopics.map(topic => topic.id),
      dailyCoverage: matched && !app.pendingTopicsUpdate() ? daily?.coverage || [] : [],
      actions: {
        openDeepDive: app.openDeepDive,
        words: app.topicsDraft ?? app.interestProfile?.words ?? '',
        savedWords: app.interestProfile?.words || '',
        muted: app.interestProfile?.muted || [],
        updating: app.topicsUpdating || Boolean(app.pendingTopicsUpdate() && app.buildInProgress),
        progress: app.topicsProgress,
        error: app.topicsError || (app.pendingTopicsUpdate() && !app.topicsUpdating ? 'Your changes are waiting. Update your briefing to try again.' : ''),
        draftWords: words => { app.topicsDraft = words; app.sessionStorage.setItem(app.TOPICS_DRAFT_KEY, words); },
        updateBriefing: words => void updateTopicsBriefing(words),
        retryUpdate: words => void updateTopicsBriefing(words),
        pauseTopic: (id, paused) => {
          const profile = app.interestProfile || app.defaultProfile(app.pack.topics);
          const topic = profile.topics.find(item => item.id === id);
          if (!topic) return;
          const muted = profile.muted.filter(item => item.label.toLowerCase() !== topic.label.toLowerCase());
          if (paused) muted.push({ label: topic.label });
          void updateTopicsBriefing(profile.words, { ...profile, muted });
        },
        interestBrief: app.deriveInterestBrief(app.interestProfile || { topics: profileTopics, muted: [] }),
        openEpisodeRequest: () => app.navigate('#ask-episode', { root: 'topics', parentHash: '#topics',
          parentScroll: app.window.scrollY, parentFocus: { type: 'href', id: '#ask-episode' } }),
      },
    });
  }

  function renderInterestsPage() {
    app.renderInterestsManagementScreen({
      content: app.content, node: app.node, history: app.history, pack: app.pack,
      getProfile: () => app.interestProfile, setProfile: value => { app.interestProfile = value; },
      setDraft: value => { app.interestDraft = value; },
      getPageFlags: () => app.interestPageFlags, setPageFlags: value => { app.interestPageFlags = value; },
      getPageNotice: () => app.interestPageNotice, setPageNotice: value => { app.interestPageNotice = value; },
      saveProfile: app.saveProfile, defaultProfile: app.defaultProfile, immediateParentLabel: app.immediateParentLabel,
      navigate: app.navigate, rootForHash: app.rootForHash,
      generateSuggestions: app.generateSuggestions, renderInterests: app.renderInterests,
      interpretProfile: app.interpretProfile, invalidateTopicsEdition, startBuildGeneration: app.startBuildGeneration,
      route: app.route, applySuggestion: app.applySuggestion, snoozeSuggestion: app.snoozeSuggestion,
      resetBehaviorWeighting: app.resetBehaviorWeighting, clearSuggestionSnoozes: app.clearSuggestionSnoozes,
    });
  }

  return { invalidateTopicsEdition, setTopicsProgress, topicsProgressFor, updateTopicsBriefing, renderTopicsPage, renderInterestsPage };
}
