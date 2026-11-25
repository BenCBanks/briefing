// Own the long-form edition and weekly recap screens; route and app state stay app-owned.
export function createEditionReaderScreen(services) {
  const {
    content, node, currentEdition, currentRevision, renderEdition, renderWeeklyRecap,
    visibleSections, personalized, listenEstimateWithUnit, listenControlLabel,
    listeningAvailable, rememberEditionMode, startStoryListening, startEditionListening,
    withNarrationTopics, readableEdition, openDeepDive, currentItems, openSource,
    topicIdFor, recordSignal, signalEvents, setFeedback, toggleSave, saveState,
    signalCurrent, showWhySheet, showStoryActionsSheet, beginStoryVisit,
    trackReadingPlace, transcriptDestination, returnFromTranscript, navigate,
    rootForHash, dailyBriefings, route,
  } = services;

  function renderEditionPage(kind, exactEdition = null, sectionId = null) {
    const profile = services.getInterestProfile();
    const pack = services.getPack();
    const edition = exactEdition || currentEdition(kind);
    if (!edition || (kind === 'weekly' && profile && edition.generated !== true)) {
      content.append(node('h1', kind === 'weekly' && profile ? 'No weekly recap yet' : `${kind === 'weekly' ? 'The weekly recap' : 'Today’s briefing'} is unavailable`));
      content.append(node('p', kind === 'weekly' && profile ? 'A weekly recap will appear here when one is ready.' : 'There is no unambiguous current version available right now.'));
      return;
    }
    if (edition.kind === 'weekly' && !sectionId) return renderWeeklyRecapPage(edition);
    const routeContext = history.state?.routeContext || {};
    const parentHash = routeContext.parentHash;
    const returnHash = parentHash?.startsWith('#edition/') || parentHash?.startsWith('#topic-detail/') || parentHash === '#weekly' ? parentHash : parentHash === '#library' ? '#library' : '#today';
    const returnLabel = routeContext.parentLabel || (returnHash === '#library' ? 'Library' : returnHash === '#today' ? 'Today' : returnHash === '#weekly' ? 'Weekly recap' : parentHash?.startsWith('#edition/') ? 'Today' : 'Topics');
    const readerHeader = node('div', null, 'reader-sticky');
    const backButton = node('button', `‹ ${returnLabel}`, 'reader-back');
    if (transcriptDestination(edition.id, edition.kind)) backButton.classList.add('transcript-return');
    backButton.type = 'button';
    backButton.addEventListener('click', () => {
      if (transcriptDestination(edition.id, edition.kind)) { returnFromTranscript(); return; }
      if (routeContext.parentHash && history.length > 1) history.back();
      else navigate(returnHash, { root: rootForHash(returnHash), parentHash: null });
    });
    const sections = visibleSections(edition).filter(item => item.kind !== 'link');
    const activeSection = sections.find(item => item.id === sectionId) || sections[0];
    const matchedInterestTopic = section => services.getInterestProfile()?.topics.find(topic => section.topic_ids?.includes(topic.id)) || null;
    const feedbackTopicId = section => matchedInterestTopic(section)?.id || topicIdFor(edition, section.id);
    const storyIndex = Math.max(0, sections.findIndex(item => item.id === activeSection?.id));
    rememberEditionMode(edition.id, 'read');
    readerHeader.append(backButton);
    const progress = node('div', `Story ${storyIndex + 1} of ${sections.length}`, 'story-progress');
    progress.setAttribute('aria-label', `Story ${storyIndex + 1} of ${sections.length}`);
    const dots = node('span', null, 'story-dots');
    dots.setAttribute('aria-hidden', 'true');
    sections.forEach((_, index) => { const dot = node('i', null, index <= storyIndex ? 'current' : ''); dots.append(dot); });
    progress.append(dots);
    readerHeader.append(progress);
    content.append(readerHeader);
    const body = node('div'); content.append(body);
    renderEdition(body, personalized(edition), pack.topics, {
      profile,
      sectionId: sectionId || history.state?.routeContext?.sectionId || sections[0]?.id,
      backLabel: returnLabel,
      transcriptView: Boolean(history.state?.routeContext?.transcript || transcriptDestination(edition.id, edition.kind)),
      listeningAvailable: listeningAvailable(),
      listenEstimate: listenEstimateWithUnit,
      listenState: item => ({ label: listenControlLabel(item) === 'Listen' ? 'Listen from here' : listenControlLabel(item) }),
      visibleParagraphIndex: sectionElement => {
        const saved = services.getState().readingProgress[edition.id];
        if (saved?.revisionId === edition.revision_id && saved.storyId === sectionElement?.id && Number.isInteger(saved.paragraphIndex)) return saved.paragraphIndex;
        const paragraphs = [...(sectionElement?.querySelectorAll('p') || [])];
        const index = paragraphs.findIndex(paragraph => { const rect = paragraph.getBoundingClientRect(); return rect.bottom > 0 && rect.top < window.innerHeight; });
        return Math.max(0, index);
      },
      listenFromStory: (item, options) => { rememberEditionMode(edition.id, 'listen'); startStoryListening(item, options); },
      sources: pack.sources,
      play: item => {
        if (services.invalidatedEditionAudio.has(item.id)) return;
        const paragraphs = [...body.querySelectorAll('.edition-section p')];
        const index = paragraphs.findIndex(paragraph => paragraph.getBoundingClientRect().bottom > 0);
        const saved = services.getState().readingProgress[item.id];
        const fromParagraph = saved?.revisionId === item.revision_id && saved.storyId === item.start_section_id
          ? saved.paragraphIndex : Math.max(0, index);
        services.player.listen(withNarrationTopics(readableEdition(item)), { fromParagraph });
      },
      openTopic: openDeepDive,
      hasDeepDive: id => services.getPack().deepDives.some(item => item.topic_id === id),
      deepDives: currentItems(pack.deepDives),
      openSource,
      whyLine: section => {
        const topic = matchedInterestTopic(section);
        if (!topic) return '';
        const subject = topic.sourcePhrase?.trim() || topic.label;
        return `Why you're seeing this: it matches “${subject}” in your interest brief.`;
      },
      interestFeedback: (type, section) => recordSignal(type, section.id, feedbackTopicId(section)),
      getInterestFeedback: section => signalEvents().filter(event => event.storyId === section.id && event.topicId === feedbackTopicId(section) && ['more', 'less'].includes(event.type)).at(-1)?.type || null,
      correctionStopped: sessionStorage.getItem('briefing:correction-audio-notice'),
      save: (id, revisionId) => toggleSave(id, revisionId, false),
      finish: (id, revision) => {
        const state = services.getState();
        const key = `${id}:${revision}`;
        const fresh = !state.finishedIds.includes(key);
        state.finishedIds = [...new Set([...state.finishedIds, key])];
        services.setState(saveState(state));
        if (fresh) signalCurrent('finish');
      },
      isFinished: (id, revision) => services.getState().finishedIds.includes(`${id}:${revision}`),
      feedback: setFeedback,
      isSaved: id => services.getState().savedIds.includes(id),
      getFeedback: id => services.getState().feedback[id],
      readingPlace: () => {
        const place = services.getState().readingProgress[edition.id];
        return place?.revisionId === edition.revision_id ? place.storyId : null;
      },
      continueReading: sectionId => {
        document.getElementById(sectionId)?.scrollIntoView({ block: 'start' });
        document.getElementById(`heading-${sectionId}`)?.focus({ preventScroll: true });
      },
      nextStory: nextSectionId => {
        const hash = `#edition/${encodeURIComponent(edition.kind)}/${encodeURIComponent(edition.id)}/${encodeURIComponent(nextSectionId)}`;
        history.replaceState({ ...(history.state || {}), routeContext: { ...(history.state?.routeContext || {}), sectionId: nextSectionId } }, '', hash);
        route();
      },
    });
    for (const section of body.querySelectorAll('.edition-section')) {
      const story = edition.sections.find(item => item.id === section.id);
      if (!story) continue;
      const tools = node('div', null, 'story-tools-row');
      const why = node('button', 'Why this story', 'why-story-trigger');
      why.type = 'button'; why.addEventListener('click', () => showWhySheet(story, edition));
      const storyActions = node('button', 'Share or report', 'story-actions-trigger'); storyActions.type = 'button';
      storyActions.addEventListener('click', () => showStoryActionsSheet(story, edition, storyActions));
      tools.append(why, storyActions); section.append(tools);
    }
    if (edition.kind === 'weekly') body.querySelector('.story-listen')?.classList.add('weekly-listen');
    beginStoryVisit(edition, sectionId || sections[0]?.id, location.hash || `#edition/${kind}/${edition.id}`);
    trackReadingPlace(edition.id, edition.revision_id, '.edition-section');
    sessionStorage.removeItem('briefing:correction-audio-notice');
    if (!services.pendingTranscriptPlacement) {
      const selected = sections.find(item => item.id === (sectionId || history.state?.routeContext?.sectionId)) || sections[0];
      const state = services.getState();
      const prior = state.readingProgress[edition.id];
      if (selected && (prior?.revisionId !== edition.revision_id || prior.storyId !== selected.id)) {
        state.readingProgress[edition.id] = { storyId: selected.id, paragraphIndex: 0, mode: 'read', updatedAt: new Date().toISOString(), revisionId: edition.revision_id };
        services.setState(saveState(state));
      }
    }
  }

  function renderWeeklyRecapPage(edition) {
    const routeContext = history.state?.routeContext || {};
    const returnHash = routeContext.parentHash === '#library' ? '#library' : '#today';
    const returnLabel = returnHash === '#library' ? 'Library' : 'Today';
    const sticky = node('div', null, 'reader-sticky');
    const backButton = node('button', `‹ ${returnLabel}`, 'reader-back'); backButton.type = 'button';
    backButton.addEventListener('click', () => {
      if (routeContext.parentHash && history.length > 1) history.back();
      else navigate(returnHash, { root: rootForHash(returnHash), parentHash: null });
    });
    sticky.append(backButton); content.append(sticky);
    const body = node('div'); content.append(body);
    const pack = services.getPack();
    renderWeeklyRecap(body, edition, pack.sources, {
      listeningAvailable: listeningAvailable(),
      listenEstimate: listenEstimateWithUnit,
      listen: item => { rememberEditionMode(item.id, 'listen'); startEditionListening(item); },
      save: (id, revisionId) => toggleSave(id, revisionId, false),
      isSaved: id => services.getState().savedIds.includes(id),
      openSource: id => {
        const hash = `#source/${encodeURIComponent(id)}`;
        navigate(hash, { root: routeContext.root || rootForHash(location.hash), parentHash: location.hash || '#weekly', parentLabel: 'Weekly recap',
          parentScroll: window.scrollY, parentFocus: { type: 'source', id } });
      },
      originalStoryFor: weeklySection => {
        const topicIds = new Set([...(weeklySection?.topic_ids || []), weeklySection?.topic_id].filter(Boolean));
        if (!topicIds.size) return null;
        const weekStart = String(edition.week_start || '').slice(0, 10);
        const weekEnd = String(edition.week_end || '').slice(0, 10);
        const candidates = dailyBriefings(services.getPack()?.editions || []);
        const inWeek = candidates.filter(item => {
          const date = String(item.local_date || '').slice(0, 10);
          return date && (!weekStart || date >= weekStart) && (!weekEnd || date <= weekEnd);
        });
        for (const daily of (inWeek.length ? inWeek : candidates)) {
          const section = visibleSections(daily).find(item => item.kind !== 'link' && ((item.topic_ids || []).some(id => topicIds.has(id)) || topicIds.has(item.topic_id)));
          if (section) return { editionId: daily.id, sectionId: section.id };
        }
        return null;
      },
      openOriginalStory: (editionId, sectionId, opener) => {
        const daily = currentRevision(services.getPack().editions, editionId, item => item.kind === 'daily');
        const section = daily?.sections?.find(item => item.id === sectionId);
        if (!daily || !section) return;
        const hash = `#edition/daily/${encodeURIComponent(daily.id)}/${encodeURIComponent(section.id)}`;
        if (opener && !opener.id) opener.id = `weekly-development-${section.id}`;
        navigate(hash, { root: routeContext.root || rootForHash(location.hash), parentHash: location.hash || '#weekly', parentLabel: 'Weekly recap',
          parentScroll: window.scrollY, parentFocus: opener?.id ? { type: 'id', id: opener.id } : null, sectionId: section.id });
      },
    });
  }

  return { renderEditionPage, renderWeeklyRecapPage };
}
