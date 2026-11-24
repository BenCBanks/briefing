export function createEditionRefreshController(app) {
  function clearNetworkBanner() {
    const scrollY = app.window.scrollY;
    app.networkBanner?.remove();
    app.networkBanner = null;
    if (app.window.scrollY !== scrollY) app.window.scrollTo({ top: scrollY, behavior: 'instant' });
  }

  function showNetworkBanner(kind, hasSavedBriefing = Boolean(app.pack || app.showingCachedPack)) {
    const copy = {
      offline: hasSavedBriefing ? 'You’re offline. Your saved briefing is still here.' : 'You’re offline. Connect to load your briefing.',
      slow: hasSavedBriefing ? 'This is taking longer than usual. Your saved briefing is still here.' : 'This is taking longer than usual. You can retry when ready.',
      failed: hasSavedBriefing ? 'Updates could not be checked. Your saved briefing is still here.' : 'Your briefing could not be loaded just now. You can try again.',
      checking: 'Connection restored. Checking for updates.',
    }[kind] || 'Your connection needs attention. You can try again.';
    if (!app.networkBanner?.isConnected) {
      app.networkBanner = app.node('aside', null, 'network-banner cache-notice');
      app.networkBanner.setAttribute('role', 'status');
      app.networkBanner.setAttribute('aria-live', 'polite');
      const message = app.node('p', copy, 'network-banner-copy');
      const retry = app.node('button', hasSavedBriefing ? 'Retry updates' : 'Retry', 'network-banner-retry'); retry.type = 'button';
      retry.addEventListener('click', () => {
        if (app.editionRefreshInFlight) return;
        retry.disabled = true; retry.textContent = 'Checking…';
        void refreshInitialPack().finally(() => {
          if (retry.isConnected) { retry.disabled = false; retry.textContent = hasSavedBriefing ? 'Retry updates' : 'Retry'; }
        });
      });
      app.networkBanner.append(message, retry);
      app.app.insertBefore(app.networkBanner, app.content);
    } else {
      app.networkBanner.querySelector('.network-banner-copy').textContent = copy;
    }
  }

  function cacheIdentity() {
    return JSON.stringify([app.interestProfile ? app.profileBuildKey(app.interestProfile) : 'sample',
      app.localStorage.getItem('briefing:v1:built-profile-key')]);
  }

  function keepReadingPosition() {
    const scrollY = app.window.scrollY;
    const focus = app.focusToken();
    const paragraphs = [...app.content.querySelectorAll('.edition-section p, .deep-dive-section p')];
    const anchor = paragraphs.find(paragraph => paragraph.getBoundingClientRect().bottom > 0);
    const section = anchor?.closest('.edition-section, .deep-dive-section');
    const index = section ? [...section.querySelectorAll('p')].indexOf(anchor) : -1;
    const offset = anchor?.getBoundingClientRect().top;
    app.history.replaceState({ ...app.history.state, viewState: { scrollY, focus } }, '', app.location.href);
    return () => app.window.requestAnimationFrame(() => app.window.requestAnimationFrame(() => {
      const replacement = section && app.document.getElementById(section.id)?.querySelectorAll('p')[index];
      if (replacement) app.window.scrollBy({ top: replacement.getBoundingClientRect().top - offset, behavior: 'instant' });
    }));
  }

  function cachedFailure() {
    showNetworkBanner(app.navigator.onLine === false ? 'offline' : 'failed', Boolean(app.pack || app.showingCachedPack));
  }

  function renderAppLoading() {
    const loading = app.node('section', null, 'app-loading');
    loading.setAttribute('role', 'status'); loading.setAttribute('aria-busy', 'true');
    if (app.location.hash.startsWith('#ask')) {
      loading.classList.add('app-loading-on-demand');
      loading.append(app.node('h1', 'Your on-demand briefings are loading'), app.node('p', 'Getting your saved list ready.'));
      loading.append(app.episodeCardsSkeleton());
    } else {
      loading.append(app.node('h1', 'Your briefing is loading'), app.node('p', 'Getting today’s stories ready.'), app.todayListSkeleton());
    }
    app.content.replaceChildren(loading);
  }

  async function refreshInitialPack({ background = false } = {}) {
    if (app.editionRefreshInFlight) return;
    app.editionRefreshInFlight = true;
    app.lastEditionCheckAt = Date.now();
    clearTimeout(app.slowNetworkTimer);
    app.slowNetworkTimer = setTimeout(() => {
      if (app.editionRefreshInFlight) showNetworkBanner('slow', Boolean(app.pack || app.showingCachedPack));
    }, 5000);
    if (!background && !app.pack) renderAppLoading();
    const initialAttempt = app.buildAttempt;
    try {
      let [data, serverProfile] = await Promise.all([app.loadPack(true), app.fetchSavedServerProfile()]);
      if (initialAttempt !== app.buildAttempt) return;
      const previousPack = app.pack;
      const previousDaily = previousPack ? app.currentEdition('daily') : null;
      const localProfile = app.getProfile();
      const serverProfileChanged = Boolean(!app.pendingTopicsUpdate() && serverProfile &&
        (!localProfile || app.profileBuildKey(serverProfile) !== app.profileBuildKey(localProfile)));
      app.interestProfile = serverProfileChanged ? app.saveProfile(serverProfile, { server: false }) : localProfile;
      if (!app.editionCheckSucceeded && previousPack && !serverProfileChanged) {
        cachedFailure();
        return;
      }
      data = app.includeNewerCachedEdition(data, previousPack, serverProfileChanged);
      const latestDaily = app.latestGeneratedDaily(data.editions);
      const editionArrived = background && app.newerEdition(latestDaily, previousDaily);
      if (editionArrived) app.editionJustUpdatedAt = Date.now();
      const unchanged = Boolean(previousPack && !serverProfileChanged && JSON.stringify(previousPack) === JSON.stringify(data));
      const restoreScroll = background && app.location.hash === '#today' && !unchanged ? app.window.scrollY : null;
      const restorePosition = app.showingCachedPack && !unchanged ? keepReadingPosition() : null;
      app.pack = data;
      app.showingCachedPack = false;
      const activeGenerated = data.editions.find(item => item.kind === 'daily' && item.generated === true);
      if (serverProfile && activeGenerated && !app.pendingTopicsUpdate()) {
        app.localStorage.setItem('briefing:v1:built-profile-key', app.profileBuildKey(serverProfile));
      }
      if (serverProfileChanged) {
        app.buildNotice = '';
        app.sessionStorage.removeItem('briefing:build-error');
      }
      if (!app.editionCheckSucceeded) showNetworkBanner('failed', true);
      else if (app.navigator.onLine !== false) clearNetworkBanner();
      if (!app.location.hash && !app.interestProfile) {
        app.history.replaceState({ routeContext: { root: 'today', parentHash: null, interestReturnHash: '#today', firstRun: true } }, '', '#interests/describe');
      }
      app.invalidateMaterialCorrections();
      const saved = app.state.activeAudio;
      app.player.setVerified(true);
      if (saved) {
        const item = [...app.pack.editions, ...app.pack.deepDives].find(entry => entry.id === saved.id && entry.revision_id === saved.revisionId);
        if (item) app.player.restore(app.withNarrationTopics({ ...item, kind: item.kind || 'deep-dive' }));
        else {
          app.state.activeAudio = null;
          app.state = app.saveState(app.state);
          const corrected = app.pack.editions.find(entry => entry.id === saved.id && entry.corrections?.some(correction => correction.previous_revision_id === saved.revisionId));
          if (corrected) app.sessionStorage.setItem('briefing:correction-audio-notice', 'The prior recording is no longer available for this corrected edition.');
        }
      }
      if (unchanged) {
        app.player.refresh(); app.syncListeningControls(); app.syncAudioVerification();
        app.writeEditionCache(cacheIdentity(), app.pack); app.cachedPackIdentity = app.pack;
      } else if (!background || app.location.hash === '#today') {
        app.route();
        restorePosition?.();
        if (restoreScroll !== null) app.window.requestAnimationFrame(() => app.window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
      } else {
        app.writeEditionCache(cacheIdentity(), app.pack); app.cachedPackIdentity = app.pack;
      }
    } catch {
      if (app.showingCachedPack) { cachedFailure(); return; }
      showNetworkBanner(app.navigator.onLine === false ? 'offline' : 'failed', false);
      app.content.textContent = '';
      app.content.append(app.node('h1', 'Your briefing is unavailable'), app.node('p', 'Please try again in a moment.', 'error'));
    } finally {
      clearTimeout(app.slowNetworkTimer); app.slowNetworkTimer = null;
      app.editionRefreshInFlight = false;
    }
  }

  function revalidateLatestEdition({ timer = false } = {}) {
    if (app.document.visibilityState !== 'visible' || app.editionRefreshInFlight) return;
    if (!timer && Date.now() - app.lastEditionCheckAt < 5 * 60 * 1000) return;
    void refreshInitialPack({ background: true });
  }

  return { clearNetworkBanner, showNetworkBanner, cacheIdentity, keepReadingPosition,
    cachedFailure, renderAppLoading, refreshInitialPack, revalidateLatestEdition };
}
