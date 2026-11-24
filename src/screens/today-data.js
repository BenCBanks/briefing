export function createTodayDataController(app) {
  function todayListSkeleton() {
    const list = app.node('div', null, 'today-list-skeleton');
    list.setAttribute('aria-hidden', 'true');
    for (let index = 0; index < 3; index++) {
      const story = app.node('div', null, 'today-story-skeleton');
      story.append(app.node('span', null, 'loading-skeleton-block today-skeleton-topic'));
      story.append(app.node('span', null, 'loading-skeleton-block today-skeleton-title'));
      story.append(app.node('span', null, 'loading-skeleton-block today-skeleton-title today-skeleton-title-short'));
      story.append(app.node('span', null, 'loading-skeleton-block today-skeleton-summary'));
      story.append(app.node('span', null, 'loading-skeleton-block today-skeleton-summary today-skeleton-summary-short'));
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
    const panel = app.node('aside', null, 'today-service-preparing');
    panel.setAttribute('role', 'status');
    panel.append(
      app.node('h2', "Today's briefing is still being prepared"),
      app.node('p', 'The scheduled run did not finish. Please check back soon.'),
      app.link('View earlier briefings', '#library', 'button-link'),
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
      app.content.append(app.node('h1', 'Building your briefing'), app.node('p', 'Finding stories for the interests you described.', 'build-today-status'));
      app.navigate('#interests/build', { root: 'today', parentHash: '#today', firstRun: true });
      void app.startBuildGeneration(profile);
    })();
  }

  function markDailyEditionSeen(edition) {
    if (edition?.kind !== 'daily' || edition.generated !== true ||
        typeof edition.profile_id !== 'string' || typeof edition.id !== 'string' ||
        typeof edition.revision_id !== 'string') return;
    const key = `${edition.profile_id}:${edition.revision_id}`;
    if (app.seenEditionMarksInFlight.has(key)) return;
    app.seenEditionMarksInFlight.add(key);
    void fetch('/api/edition/seen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ profile_id: edition.profile_id, edition_id: edition.id, revision_id: edition.revision_id }),
    }).then(response => {
      if (!response.ok) app.seenEditionMarksInFlight.delete(key);
    }).catch(() => app.seenEditionMarksInFlight.delete(key));
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
      return result?.profile ? app.validateProfile(result.profile) : null;
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function showGeneralEditionFallback(profile, attempt) {
    try {
      const loaded = await app.loadPack(true);
      if (attempt !== app.buildAttempt) return false;
      app.pack = loaded;
      const general = app.currentEdition('daily');
      if (!general?.generated) return false;
      const profileKey = app.profileBuildKey(profile);
      app.localStorage.setItem('briefing:v1:built-profile-key', profileKey);
      app.localStorage.setItem(app.BUILD_GENERAL_FALLBACK_KEY, profileKey);
      app.buildNotice = ''; app.sessionStorage.removeItem('briefing:build-error');
      clearTimeout(app.buildTimeoutHandle); app.buildTimeoutHandle = null;
      app.navigate('#today', { root: 'today', parentHash: null });
      return true;
    } catch {
      return false;
    }
  }

  function renderNoStoryExplanations(container, profile) {
    const list = app.node('section', null, 'today-topic-explanations');
    list.append(app.node('h2', 'Your topics'));
    (profile?.topics || []).filter(topic => !topic.exclude?.length).forEach(topic => {
      const dive = app.pack.deepDives.find(item => item.topic_id === topic.id);
      if (dive) list.append(app.link(dive.title, `#explanation/${encodeURIComponent(dive.id)}`, 'topic-explanation-link'));
      else list.append(app.node('p', topic.label, 'topic-explanation-link'));
    });
    if (list.children.length > 1) container.append(list);
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

  return {
    todayListSkeleton, startTodayBuildAfterStatusCheck, markDailyEditionSeen,
    fetchSavedServerProfile, showGeneralEditionFallback, renderNoStoryExplanations,
    formatLastUpdated, refreshTodayRunSummary, refreshLastUpdated,
  };
}
