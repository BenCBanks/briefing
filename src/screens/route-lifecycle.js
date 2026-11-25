import { describeRoute, restoreRouteView } from './route-controller.js';

export function createRouteLifecycleController(app) {
  let restoringRoute = false;
  let renderedRouteHash = null;
  let historyEntrySequence = 0;
  const restorationHelpers = app.getRouteRestorationHelpers();

  const routeRestoration = {
    window: app.window, content: app.content, document: app.document, CSS: app.CSS,
    revealStoryMore: restorationHelpers.revealStoryMore, paragraphAt: restorationHelpers.paragraphAt,
    currentRevision: restorationHelpers.currentRevision, trackReadingPlace: restorationHelpers.trackReadingPlace,
    get pack() { return app.pack; },
    get pendingScrollRestore() { return app.pendingScrollRestore; },
    set pendingScrollRestore(value) { app.pendingScrollRestore = value; },
    get pendingSourceFocusID() { return app.pendingSourceFocusID; },
    set pendingSourceFocusID(value) { app.pendingSourceFocusID = value; },
    get pendingReturnHeadingFocus() { return app.pendingReturnHeadingFocus; },
    set pendingReturnHeadingFocus(value) { app.pendingReturnHeadingFocus = value; },
    get pendingTranscriptPlacement() { return app.pendingTranscriptPlacement; },
    set pendingTranscriptPlacement(value) { app.pendingTranscriptPlacement = value; },
    get pendingPlayerReturnFocus() { return app.pendingPlayerReturnFocus; },
    set pendingPlayerReturnFocus(value) { app.pendingPlayerReturnFocus = value; },
    setRestoringRoute(value) { restoringRoute = value; },
  };

  function route() {
    if (!app.pack) return;
    app.episodeDeckCleanup?.(); app.episodeDeckCleanup = null;
    restoringRoute = true;
    renderedRouteHash = app.location.hash || '#today';
    app.invalidateMaterialCorrections();
    const nextHash = app.location.hash || '#today';
    app.finishStoryVisit(nextHash);
    app.readingObserver?.disconnect(); app.readingObserver = null;
    app.content.classList.remove('interests-page');
    app.content.classList.remove('first-run-describe', 'interest-build-screen');
    app.content.textContent = '';
    const routeHash = app.location.hash || '#today';
    const routeName = decodeURIComponent(routeHash.slice(1));
    if (app.interestInterpretController && !routeName.startsWith('interests/')) {
      app.interestInterpretController.abort();
      app.interestInterpretController = null;
      app.interestInterpretBusy = false;
    }
    const routeState = app.history.state || {};
    if (app.pendingRouteContext) {
      app.history.replaceState({ ...routeState, routeContext: app.pendingRouteContext }, '', app.location.href);
      app.pendingRouteContext = null;
    } else if (!routeState.routeContext) {
      const directRoot = app.rootForHash(routeHash, null);
      const directParent = routeName.startsWith('explanation/') ? '#topics' : routeName.startsWith('topic/') || routeName.startsWith('topic-detail/') ? '#topics' : null;
      app.history.replaceState({ ...routeState, routeContext: { root: directRoot, parentHash: directParent, parentScroll: 0, parentFocus: null, direct: Boolean(directParent) } }, '', app.location.href);
    }
    if (!app.history.state?.viewKey) app.history.replaceState({ ...app.history.state, viewKey: `${Date.now()}-${++historyEntrySequence}` }, '', app.location.href);
    const descriptor = describeRoute(routeHash, app.history.state?.routeContext, app.rootForHash);
    const { routeName: describedName, episodeRoute, group, isReader, pageTitle } = descriptor;
    app.trackRouteSignals(routeHash, routeName);
    if (app.history.state?.transcriptHash === routeHash && app.history.state?.transcriptOrigin) {
      app.transcriptReturnTo = app.history.state.transcriptOrigin;
      app.transcriptReturnHash = app.history.state.transcriptHash;
    } else if (app.transcriptReturnHash && routeHash !== app.transcriptReturnHash && !routeName.startsWith('source/')) {
      if (routeHash === app.transcriptReturnTo?.hash) {
        app.pendingScrollRestore = app.transcriptReturnTo.scrollY;
        app.pendingPlayerReturnFocus = true;
      }
      app.transcriptReturnTo = null; app.transcriptReturnHash = null;
    }
    if (!routeName.startsWith('topic/') && !routeName.startsWith('explanation/') && !routeName.startsWith('source/')) app.deepDiveReturnTo = null;
    if (routeName.startsWith('topic/') && !app.deepDiveReturnTo) {
      app.deepDiveReturnTo = { hash: '#topics', sectionId: null, scrollY: 0 };
    }
    app.document.body.classList.toggle('is-reader', isReader);
    app.document.querySelector('[data-page-title]').textContent = pageTitle;
    app.document.body.classList.toggle('is-today', routeName === 'today');
    app.document.body.classList.toggle('is-topics', routeName === 'topics');
    app.document.body.classList.toggle('is-episodes-list', routeName === 'episodes');
    app.document.title = `${pageTitle} · Personal briefing`;
    for (const item of app.nav) {
      if (item.dataset.nav === group) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    }
    app.renderRouteContent(routeName, descriptor);
    if (app.firstRunPending && app.firstRunRecoveryAvailable && ['today', 'interests/build'].includes(routeName)) {
      app.firstRunRecoveryAvailable = false;
      const pending = app.firstRunPending;
      const profile = app.interestProfile;
      queueMicrotask(() => { if (profile) void app.startBuildGeneration(profile, null, {
        firstRun: true, resumeJobId: pending.jobId, inline: routeName === 'today',
      }); });
    }
    const settingsNotice = app.sessionStorage.getItem('briefing:settings-saved');
    if (settingsNotice && ['today', 'topics', 'library'].includes(group) && !isReader) {
      app.showToast(settingsNotice); app.sessionStorage.removeItem('briefing:settings-saved');
    }
    app.player.refresh();
    const viewState = app.historyViews.get(app.history.state?.viewKey) || app.history.state?.viewState || app.history.state?.routeContext?.restore;
    restoreRouteView(routeRestoration, routeName || describedName, viewState);
  }

  function install() {
    app.history.scrollRestoration = 'manual';
    app.window.addEventListener('scroll', () => {
      if (restoringRoute || renderedRouteHash !== (app.location.hash || '#today')) return;
      const viewState = { scrollY: app.window.scrollY, focus: app.focusToken() || app.history.state?.viewState?.focus || null };
      app.historyViews.set(app.history.state?.viewKey, viewState);
    }, { passive: true });
    app.window.addEventListener('hashchange', route);
    app.document.addEventListener('click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = event.target.closest?.('a[href^="#"]');
      if (!anchor || anchor.target === '_blank') return;
      const hash = anchor.getAttribute('href');
      if (!hash || hash === '#content') return;
      event.preventDefault();
      const isRoot = ['#today', '#topics', '#ask', '#library', '#earlier'].includes(hash);
      const root = isRoot ? hash.slice(1) : app.rootForHash(app.location.hash);
      app.navigate(hash, { root, parentHash: isRoot ? null : (app.location.hash || '#today'), parentScroll: app.window.scrollY, parentFocus: app.focusToken() });
    });
  }

  return { route, install };
}
