export function createNavigationController(app) {
  function focusToken() {
    const active = app.document.activeElement;
    if (!active) return null;
    if (active.dataset?.nav) return { type: 'nav', id: active.dataset.nav };
    if (active.classList?.contains('settings-link')) return { type: 'settings' };
    if (!app.content.contains(active)) return null;
    if (active.dataset.source) return { type: 'source', id: active.dataset.source };
    if (active.dataset.topic) return { type: 'topic', id: active.dataset.topic };
    if (active.hasAttribute('data-read-start')) return { type: 'read-start' };
    if (active.hasAttribute('data-continue')) return { type: 'continue' };
    if (active.dataset.readSection) return { type: 'read-section', id: active.dataset.readSection };
    if (active instanceof app.HTMLAnchorElement && active.getAttribute('href')?.startsWith('#')) return { type: 'href', id: active.getAttribute('href') };
    if (active.id) return { type: 'id', id: active.id };
    return null;
  }

  function remember(sectionId = null) {
    app.returnTo = { hash: app.location.hash || '#today', sectionId, scrollY: app.window.scrollY,
      focus: focusToken(), sourceID: app.document.activeElement?.dataset?.source || null };
    return app.returnTo;
  }

  function rootForHash(hash = app.location.hash || '#today', context = app.history.state?.routeContext) {
    if (hash === '#today') return 'today';
    if (hash === '#topics') return 'topics';
    if (['#library', '#earlier', '#episodes'].includes(hash)) return 'library';
    if (['#ask', '#ask-episode'].includes(hash)) return 'today';
    if (context?.root && ['today', 'topics', 'library'].includes(context.root)) return context.root;
    if (['#weekly'].includes(hash) || hash.startsWith('#edition/')) return 'today';
    if (hash.startsWith('#topic/') || hash.startsWith('#explanation/') || hash.startsWith('#topic-detail/')) return 'topics';
    return 'today';
  }

  function navigate(hash, context = null) {
    const currentHash = app.location.hash || '#today';
    if (['#today', '#topics', '#ask', '#library', '#earlier'].includes(currentHash)) app.rootScroll.set(currentHash, app.window.scrollY);
    if (currentHash !== hash && ['#today', '#topics', '#ask', '#library', '#earlier'].includes(hash)) {
      app.pendingScrollRestore = app.rootScroll.get(hash) ?? 0;
    }
    const currentState = app.history.state || {};
    app.historyViews.delete(currentState.viewKey);
    app.history.replaceState({ ...currentState, viewState: { scrollY: app.window.scrollY,
      focus: context?.returnFocus || focusToken() || context?.parentFocus || null } }, '', app.location.href);
    app.pendingRouteContext = context || { root: rootForHash(currentHash), parentHash: currentHash,
      parentScroll: app.window.scrollY, parentFocus: focusToken() };
    if (context?.returnFocus) {
      const focusReturn = () => {
        const apply = () => {
          if (app.location.hash !== hash) return;
          const target = app.document.querySelector(`[data-nav="${app.CSS.escape(context.returnFocus.id)}"]`);
          if (target && target.getClientRects().length) target.focus({ preventScroll: true });
        };
        app.window.setTimeout(() => app.window.requestAnimationFrame(apply), 80);
      };
      if (currentHash === hash) focusReturn();
      else app.window.addEventListener('hashchange', focusReturn, { once: true });
    }
    if (hash.startsWith('#interests/')) {
      app.pendingRouteContext.interestReturnHash ||= app.history.state?.routeContext?.interestReturnHash ||
        (currentHash === '#preferences' ? '#preferences' : '#today');
    }
    if (currentHash === hash) app.route(); else app.location.hash = hash;
  }

  function navigateTranscript(hash, placement) {
    app.pendingPlayerReturnFocus = false;
    app.pendingTranscriptPlacement = placement;
    if (app.location.hash === hash) app.route();
    else {
      app.historyViews.set(app.history.state?.viewKey, { scrollY: app.transcriptReturnTo.scrollY, focus: app.transcriptReturnTo.focus });
      app.history.pushState({ ...(app.history.state || {}), viewKey: null, viewState: { scrollY: 0, focus: null },
        transcriptOrigin: { ...app.transcriptReturnTo }, transcriptHash: hash,
        routeContext: { root: rootForHash(app.transcriptReturnTo.hash), parentHash: app.transcriptReturnTo.hash,
          parentScroll: app.transcriptReturnTo.scrollY, parentFocus: app.transcriptReturnTo.focus,
          sectionId: placement.sectionId, transcript: true } }, '', hash);
      app.route();
    }
  }

  function openSource(id) {
    const parent = remember(app.document.activeElement?.closest('.edition-section')?.id || null);
    parent.focus = { type: 'source', id }; parent.sourceID = id;
    navigate(`#source/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash,
      parentLabel: app.immediateParentLabel(parent.hash, app.history.state?.routeContext || {}),
      parentScroll: parent.scrollY, parentFocus: parent.focus, sectionId: parent.sectionId,
      parentType: parent.hash.startsWith('#explanation/') || parent.hash.startsWith('#topic/') ||
        parent.hash.startsWith('#topic-detail/') ? 'explanation' : 'briefing' });
  }

  function openDeepDive(id, sectionId = null) {
    const parent = remember(sectionId);
    parent.focus ||= { type: 'topic', id };
    app.deepDiveReturnTo = app.returnTo;
    navigate(`#topic/${encodeURIComponent(id)}`, { root: rootForHash(parent.hash), parentHash: parent.hash,
      parentScroll: parent.scrollY, parentFocus: parent.focus, sectionId });
  }

  function backTo(target) {
    if (app.history.state?.routeContext?.parentHash === target.hash && app.history.length > 1) {
      app.history.back();
    } else {
      app.pendingScrollRestore = target.scrollY;
      app.pendingSourceFocusID = target.sourceID || null;
      app.pendingReturnHeadingFocus = !target.focus && !target.sourceID;
      navigate(target.hash, { root: rootForHash(target.hash), parentHash: null,
        restore: { scrollY: target.scrollY, focus: target.focus || (target.sourceID ? { type: 'source', id: target.sourceID } : null) } });
    }
  }

  function back() {
    if (app.transcriptReturnTo && app.transcriptReturnHash && app.location.hash.startsWith('#source/') &&
        app.returnTo.hash === app.transcriptReturnHash) {
      app.pendingScrollRestore = app.returnTo.scrollY;
      app.pendingSourceFocusID = app.returnTo.sourceID || null;
      app.history.back();
      return;
    }
    const parent = app.history.state?.routeContext?.parentHash;
    if (parent && app.history.length > 1) app.history.back();
    else backTo(app.returnTo);
  }

  return { focusToken, remember, rootForHash, navigate, navigateTranscript, openSource, openDeepDive, backTo, back };
}
