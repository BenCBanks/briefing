export function describeRoute(routeHash, routeContext = null, rootForHash = (hash, context) => context?.root || hash.slice(1)) {
  const routeName = decodeURIComponent((routeHash || '#today').slice(1));
  const episodeRoute = routeName === 'episodes' || routeName.startsWith('episode/') || routeName.startsWith('episode-making/');
  const onDemandRoute = ['ask', 'ask-episode', 'episode-making', 'episodes'].includes(routeName) || routeName.startsWith('episode/');
  const group = onDemandRoute ? 'on-demand' : episodeRoute ? 'library' : rootForHash(routeHash, routeContext);
  const editionKind = routeName.startsWith('edition/') ? routeName.slice(8).split('/')[0] : null;
  const isReader = routeName === 'weekly' || routeName === 'corrections' || routeName === 'episode-making' ||
    routeName.startsWith('episode-making/') || routeName.startsWith('edition/') || routeName.startsWith('explanation/') ||
    routeName.startsWith('topic/') || routeName.startsWith('topic-detail/') || routeName.startsWith('source/') ||
    routeName.startsWith('interests/') || (episodeRoute && routeName.startsWith('episode/'));
  const pageTitle = episodeRoute ? (routeName === 'episodes' ? 'Episodes' : 'Episode') :
    routeName === 'corrections' ? 'Corrections' :
    routeName === 'episode-making' || routeName.startsWith('episode-making/') ? 'Making your episode' :
    onDemandRoute ? 'On demand' :
    routeName === 'interests' || routeName.startsWith('interests/') ? 'Interests' :
    routeName === 'preferences' ? 'Settings' : routeName.startsWith('source/') ? 'Source' :
    routeName.startsWith('explanation/') || routeName.startsWith('topic/') ? 'Explanation' :
    routeName.startsWith('topic-detail/') ? 'Topic' : group === 'topics' ? 'Topics' :
    group === 'library' ? 'Earlier' : (routeName === 'weekly' || editionKind === 'weekly') ? 'Weekly' : 'Today';
  return { routeName, episodeRoute, onDemandRoute, group, editionKind, isReader, pageTitle };
}

export function restoreRouteView(app, routeName, viewState) {
  const restoreScroll = app.pendingScrollRestore;
  app.pendingScrollRestore = null;
  if (restoreScroll != null) app.window.requestAnimationFrame(() => app.window.scrollTo({ top: restoreScroll, behavior: 'instant' }));
  else if (viewState && routeName !== 'preferences') app.window.requestAnimationFrame(() => app.window.scrollTo({ top: viewState.scrollY || 0, behavior: 'instant' }));
  else if (routeName !== 'preferences') app.window.scrollTo({ top: 0, behavior: 'instant' });
  const sourceFocusID = app.pendingSourceFocusID;
  app.pendingSourceFocusID = null;
  if (sourceFocusID) app.window.requestAnimationFrame(() => {
    const source = [...app.content.querySelectorAll('[data-source]')].find(button => button.dataset.source === sourceFocusID);
    if (source?.closest('details')) source.closest('details').open = true;
    app.revealStoryMore(source);
    source?.focus({ preventScroll: true });
  });
  else if (routeName.startsWith('source/')) app.window.requestAnimationFrame(() => app.content.querySelector('h1')?.focus({ preventScroll: true }));
  else if (viewState?.focus) app.window.requestAnimationFrame(() => {
    const selector = viewState.focus.type === 'source' ? `[data-source="${app.CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'topic' ? `[data-topic="${app.CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'read-start' ? '[data-read-start]' : viewState.focus.type === 'continue' ? '[data-continue]' :
      viewState.focus.type === 'read-section' ? `[data-read-section="${app.CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'nav' ? `[data-nav="${app.CSS.escape(viewState.focus.id)}"]` :
      viewState.focus.type === 'settings' ? '.settings-link' : viewState.focus.type === 'href' ? null : `#${app.CSS.escape(viewState.focus.id)}`;
    const target = viewState.focus.type === 'nav' || viewState.focus.type === 'settings' ? app.document.querySelector(selector) :
      viewState.focus.type === 'href' ? [...app.content.querySelectorAll('a[href]')].find(anchor => anchor.getAttribute('href') === viewState.focus.id) : app.content.querySelector(selector);
    if (target?.closest('details')) target.closest('details').open = true;
    app.revealStoryMore(target);
    target?.focus({ preventScroll: true });
  });
  const focusReturnHeading = app.pendingReturnHeadingFocus;
  app.pendingReturnHeadingFocus = false;
  if (focusReturnHeading && !sourceFocusID) app.window.requestAnimationFrame(() => (app.content.querySelector('h1') || app.document.querySelector('[data-page-title]'))?.focus({ preventScroll: true }));
  const placement = app.pendingTranscriptPlacement;
  app.pendingTranscriptPlacement = null;
  if (placement) app.window.requestAnimationFrame(() => app.window.requestAnimationFrame(() => {
    const correction = placement.correction && app.content.querySelector('.correction-notice');
    const section = placement.sectionId && app.content.querySelector(`#${app.CSS.escape(placement.sectionId)}`);
    const target = correction || section || app.content.querySelector('h1');
    if (correction || target?.tagName === 'H1') target.tabIndex = -1;
    if (correction || !section) target?.focus({ preventScroll: true });
    else { const heading = section.querySelector('h2') || app.content.querySelector('h1'); if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); } }
    const spoken = app.paragraphAt(section, placement.paragraphIndex || 0); app.revealStoryMore(spoken);
    target?.scrollIntoView({ block: 'start', behavior: 'instant' });
    spoken?.scrollIntoView({ block: 'center', behavior: 'instant' });
    const editionParts = routeName.startsWith('edition/') ? routeName.slice(8).split('/') : [];
    const editionKind = editionParts.length > 1 ? editionParts[0] : null;
    const edition = editionParts.length ? app.currentRevision(app.pack.editions, placement.id, item => !editionKind || item.kind === editionKind) : null;
    const dive = routeName.startsWith('explanation/') ? app.currentRevision(app.pack.deepDives, placement.id) : null;
    if (edition && !placement.correction) app.trackReadingPlace(edition.id, edition.revision_id, '.edition-section');
    else if (dive && !placement.correction) app.trackReadingPlace(dive.id, dive.revision_id, '.deep-dive-section');
  }));
  if (app.pendingPlayerReturnFocus) app.window.requestAnimationFrame(() => app.window.requestAnimationFrame(() => {
    app.pendingPlayerReturnFocus = false;
    (app.document.querySelector('.mini-player-open') || app.content.querySelector('h1') || app.document.querySelector('[data-page-title]'))?.focus({ preventScroll: true });
  }));
  app.window.requestAnimationFrame(() => app.window.requestAnimationFrame(() => { app.setRestoringRoute(false); }));
}
