export function createDetailRouteController(app) {
  function renderDeepDivePage(id, exactDive = null) {
    const dive = exactDive || app.currentItems(app.pack.deepDives, item => item.topic_id === id)[0];
    const topic = app.pack.topics.find(item => item.id === (dive?.topic_id || id));
    if (!topic || !dive) return app.renderMissing();
    const body = app.node('div'); app.content.append(body);
    body.className = 'deep-dive-page';
    const isTranscriptDestination = app.transcriptDestination(dive.id, 'deep-dive');
    const routeContext = app.history.state?.routeContext;
    const origin = isTranscriptDestination ? app.transcriptReturnTo :
      (routeContext?.parentHash ? { hash: routeContext.parentHash, scrollY: routeContext.parentScroll || 0,
        focus: routeContext.parentFocus } : (app.deepDiveReturnTo || app.returnTo));
    const root = app.rootForHash(app.location.hash, routeContext);
    const backLabel = app.immediateParentLabel(origin?.hash, routeContext);
    const sticky = app.node('div', null, 'reader-sticky');
    const back = app.node('button', backLabel, 'reader-back'); back.type = 'button';
    if (isTranscriptDestination) back.classList.add('transcript-return');
    back.addEventListener('click', () => {
      if (isTranscriptDestination) { app.returnFromTranscript(); return; }
      if (routeContext?.parentHash && !routeContext.direct && app.history.length > 1) app.history.back();
      else app.navigate(root === 'library' ? '#library' : root === 'today' ? '#today' : '#topics', { root, parentHash: null });
    });
    sticky.append(back); app.content.insertBefore(sticky, body);
    app.renderDeepDive(body, topic, dive, app.pack.sources, {
      play: item => app.startListening(item),
      listen: item => app.startListening(item),
      canListen: app.canListen,
      estimateListenMinutes: item => app.listenEstimate(item),
      openSource: app.openSource,
      save: (itemId, revision) => app.toggleSave(itemId, revision, false),
      isSaved: itemId => app.state.savedIds.includes(itemId),
    });
    const sections = [...body.querySelectorAll('.deep-dive-section')];
    sections.forEach((section, index) => {
      section.id = dive.sections[index]?.id || `dive-section-${index}`;
      section.querySelector('h2')?.setAttribute('tabindex', '-1');
    });
    if (isTranscriptDestination) {
      const backLink = body.querySelector('.back-link');
      if (backLink) backLink.textContent = app.transcriptReturnLabel();
    } else app.trackReadingPlace(dive.id, dive.revision_id, '.deep-dive-section');
  }

  function renderTopicDetailPage(id) {
    const topic = app.pack.topics.find(item => item.id === id) || app.interestProfile?.topics.find(item => item.id === id);
    if (!topic) return app.renderMissing();
    const body = app.node('div', null, 'topic-detail-page'); app.content.append(body);
    const context = app.history.state?.routeContext || {};
    const sticky = app.node('div', null, 'reader-sticky topic-detail-sticky');
    const back = app.node('button', '‹ Topics', 'reader-back'); back.type = 'button';
    back.addEventListener('click', () => {
      if (context.parentHash === '#topics' && !context.direct && app.history.length > 1) app.history.back();
      else app.navigate('#topics', { root: 'topics', parentHash: null });
    });
    sticky.append(back); app.content.insertBefore(sticky, body);
    const profileTopic = app.interestProfile?.topics.find(item => item.id === id);
    const isPaused = Boolean(profileTopic && app.interestProfile?.muted.some(item => item.label.toLocaleLowerCase() === profileTopic.label.toLocaleLowerCase()));
    const editions = app.currentItems(app.pack.editions).filter(item => item.kind === 'daily' && item.generated === true)
      .sort((a, b) => String(b.local_date || '').localeCompare(String(a.local_date || '')));
    const items = editions.flatMap(edition => (edition.sections || [])
      .filter(section => (section.topic_ids || []).includes(id) || section.topic_id === id)
      .map(section => ({ section, edition, href: `#edition/daily/${encodeURIComponent(edition.id)}/${encodeURIComponent(section.id)}` })))
      .slice(0, 12);
    app.renderTopicDetail(body, {
      topic, items, sources: app.pack.sources, followed: Boolean(profileTopic), paused: isPaused,
      actions: {
        setPaused: paused => {
          const profile = { ...app.interestProfile, muted: (app.interestProfile.muted || []).filter(item => item.label.toLocaleLowerCase() !== profileTopic.label.toLocaleLowerCase()) };
          if (paused) profile.muted.push({ label: profileTopic.label });
          void app.updateTopicsBriefing(profile.words, profile);
        },
        follow: () => {
          const profile = app.interestProfile || app.defaultProfile(app.pack.topics);
          const nextTopic = { id: topic.id, label: topic.label, scope: topic.scope || topic.label,
            include: [topic.sourcePhrase || topic.label], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false,
            sourcePhrase: topic.sourcePhrase || topic.label };
          void app.updateTopicsBriefing(profile.words || nextTopic.label,
            { ...profile, topics: [...profile.topics.filter(item => item.id !== topic.id), nextTopic] });
        },
        editInterests: () => app.navigate('#interests', { root: 'topics', parentHash: app.location.hash, parentScroll: app.window.scrollY }),
        askEpisode: () => {
          app.episodeDraft.prompt = `Tell me about ${topic.label}`;
          app.navigate('#ask-episode', { root: 'topics', parentHash: app.location.hash, parentScroll: app.window.scrollY });
        },
        openStory: item => app.navigate(item.href, { root: 'topics', parentHash: app.location.hash,
          parentScroll: app.window.scrollY, sectionId: item.section.id }),
        openSource: sourceId => app.openSource(sourceId),
      },
    });
    if (app.interestProfile && !app.editionHistoryLoaded) void app.loadEditionHistory();
  }

  function renderSourcePage(id) {
    const source = app.pack.sources.find(item => item.id === id);
    if (!source) return app.renderMissing();
    const routeContext = app.history.state?.routeContext || {};
    const sticky = app.node('div', null, 'reader-sticky');
    const backButton = app.node('button', routeContext.parentLabel || app.immediateParentLabel(routeContext.parentHash, routeContext), 'reader-back');
    backButton.type = 'button'; backButton.addEventListener('click', app.back);
    sticky.append(backButton); app.content.append(sticky);
    const safeURL = (() => { try { const url = new URL(source.url); return url.protocol === 'https:' && url.hostname ? url : null; } catch { return null; } })();
    const domain = safeURL?.hostname.replace(/^www\./, '') || '';
    const publisherNames = { 'federalreserve.gov': 'Federal Reserve', 'fdic.gov': 'FDIC', 'consumerfinance.gov': 'Consumer Financial Protection Bureau', 'pages.nist.gov': 'NIST', 'content.naic.org': 'National Association of Insurance Commissioners', 'theifab.com': 'The IFAB' };
    const publisher = source.publisher || publisherNames[domain] || domain || 'Reference';
    const parentHash = routeContext.parentHash || '';
    const associatedDive = parentHash.startsWith('#topic/')
      ? app.currentItems(app.pack.deepDives, item => item.topic_id === decodeURIComponent(parentHash.slice(7)))[0]
      : parentHash.startsWith('#explanation/')
        ? app.currentRevision(app.pack.deepDives, decodeURIComponent(parentHash.slice(13)))
        : null;
    const editionMatch = parentHash.startsWith('#edition/') && /^#edition\/[^/]+\/[^/]+/.exec(parentHash);
    const associatedEdition = editionMatch
      ? app.currentRevision(app.pack.editions, decodeURIComponent(editionMatch[0].split('/')[2]))
      : null;
    const sourcePublication = source.date || source.published_at || '';
    const sourceUpdate = source.updated_at || '';
    const sourceDateValue = sourcePublication || sourceUpdate;
    const sourceDate = app.plainDate(sourceDateValue) || 'Date not listed';
    const sourceDateLabel = sourcePublication ? `Document date · ${sourceDate}`
      : sourceUpdate ? `Updated · ${sourceDate}` : 'Document date · Date not listed';
    const reviewedDate = source.reviewed_at || associatedDive?.reviewed_at || associatedEdition?.reviewed_at || associatedEdition?.published_at || associatedEdition?.local_date || '2026-11-16';
    const heading = app.node('h1', source.title); heading.tabIndex = -1;
    heading.className = 'source-title';
    app.content.append(app.node('p', publisher, 'source-publisher'), app.node('p', 'Document', 'source-document'), heading,
      app.node('p', sourceDateLabel, 'source-published'));
    app.content.append(app.node('h2', 'What this supports', 'source-section-heading'));
    const sourceNote = (source.note || 'This reference provides background for the explanation.')
      .replace(/\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b/g, value => app.plainDate(value) || value);
    app.content.append(app.node('p', sourceNote));
    app.content.append(app.node('p', `Reviewed · ${app.displayDate(reviewedDate)}`, 'source-reviewed'));
    if (source.kind === 'Internal fictional evidence') app.content.append(app.node('p', 'Sample · fictional', 'source-sample-tag sample-label'));
    const row = app.node('div', null, 'button-row source-actions');
    if (safeURL && source.kind !== 'Internal fictional evidence') {
      const external = app.link('Open original ↗', safeURL.href, 'button-link');
      external.setAttribute('aria-label', `Open original: ${publisher}, ${source.title || 'current source'}`);
      external.target = '_blank'; external.rel = 'noopener noreferrer';
      row.append(external);
    }
    if (safeURL && source.kind !== 'Internal fictional evidence') row.append(app.node('span', 'Opens in a new tab', 'source-new-tab'));
    app.content.append(row);
  }

  function renderMissing() {
    const page = app.node('section', null, 'unavailable-page');
    page.setAttribute('aria-labelledby', 'unavailable-title');
    const heading = app.node('h1', 'This page is unavailable');
    heading.id = 'unavailable-title';
    page.append(heading, app.node('p', 'The link may have moved or expired. You can head back to Today and keep browsing.', 'unavailable-copy'));
    page.append(app.link('Return to today', '#today', 'button-link primary unavailable-action'));
    app.content.append(page);
  }

  return { renderDeepDivePage, renderTopicDetailPage, renderSourcePage, renderMissing };
}
