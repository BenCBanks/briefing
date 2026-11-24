// Own the reader-managed interests page; profile mutation and generation
// remain app-owned and enter through the supplied state accessors/actions.
export function renderInterestsManagementScreen({
  content, node, history, pack, getProfile, setProfile, setDraft,
  getPageFlags, setPageFlags, getPageNotice, setPageNotice,
  saveProfile, defaultProfile, immediateParentLabel, navigate, rootForHash,
  generateSuggestions, renderInterests, interpretProfile,
  invalidateTopicsEdition, startBuildGeneration, route,
  applySuggestion, snoozeSuggestion, resetBehaviorWeighting, clearSuggestionSnoozes,
}) {
  let profile = getProfile();
  if (!profile) {
    profile = saveProfile(defaultProfile(pack.topics), { server: false });
    setProfile(profile);
  }
  const context = history.state?.routeContext || {};
  const parentHash = context.parentHash || '#preferences';
  const sticky = node('div', null, 'reader-sticky interests-back');
  const backButton = node('button', parentHash === '#preferences' ? '‹ Settings' : immediateParentLabel(parentHash, context), 'reader-back');
  backButton.type = 'button'; backButton.addEventListener('click', () => {
    if (context.parentHash && !context.direct && history.length > 1) history.back();
    else navigate(parentHash, { root: context.root || rootForHash(parentHash), parentHash: null });
  });
  sticky.append(backButton); content.append(sticky);
  const suggestions = generateSuggestions({ profile, catalog: pack.topics });
  renderInterests(content, profile, suggestions, pack.topics, {
    interpret: async words => {
      setPageFlags([]); setPageNotice('');
      const result = await interpretProfile(words, { topics: pack.topics, minutes: getProfile().minutes });
      setPageFlags(result.flags || []); setPageNotice(result.fallbackError || '');
      const updated = saveProfile(result.profile);
      setProfile(updated); setDraft(null);
      invalidateTopicsEdition();
      void startBuildGeneration(updated, null, { inline: true });
      route();
    },
    updateTopic: (id, changes) => {
      const current = getProfile();
      const updated = saveProfile({ ...current, topics: current.topics.map(topic => topic.id === id ? { ...topic, ...changes } : topic) });
      setProfile(updated); invalidateTopicsEdition();
      void startBuildGeneration(updated, null, { inline: true }); route();
    },
    removeTopic: id => {
      const current = getProfile();
      const topic = current.topics.find(item => item.id === id);
      if (!topic) return;
      const updated = saveProfile({ ...current, topics: current.topics.filter(item => item.id !== id), muted: [...current.muted, { label: topic.label }] });
      setProfile(updated); invalidateTopicsEdition();
      void startBuildGeneration(updated, null, { inline: true }); route();
    },
    unmute: label => {
      const current = getProfile();
      const topic = pack.topics.find(item => item.label === label);
      const updated = saveProfile({ ...current, muted: current.muted.filter(item => item.label !== label),
        topics: current.topics.map(item => item.label === label ? { ...item, exclude: [] } : item).concat(topic && !current.topics.some(item => item.label === topic.label) ? [{ id: topic.id, label: topic.label, scope: topic.scope, include: [], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: topic.label }] : []) });
      setProfile(updated); invalidateTopicsEdition();
      void startBuildGeneration(updated, null, { inline: true }); route();
    },
    accept: suggestion => {
      const updated = saveProfile(applySuggestion(getProfile(), suggestion, pack.topics));
      setProfile(updated); invalidateTopicsEdition();
      void startBuildGeneration(updated, null, { inline: true }); route();
    },
    snooze: suggestion => { snoozeSuggestion(suggestion.id); route(); },
    clearSignals: () => {
      if (!window.confirm('Clear the local reading signals and suggestions? Your written interests will stay.')) return;
      resetBehaviorWeighting(); clearSuggestionSnoozes(); route();
    },
  });
  const wordsForm = content.querySelector('.interest-words-form');
  const flags = getPageFlags();
  if (flags.length) {
    const notice = node('aside', null, 'interest-interpreter-flags');
    notice.setAttribute('role', 'status'); notice.append(node('p', `Interpretation note: ${flags.join('; ')}`));
    wordsForm?.after(notice);
  }
  const error = getPageNotice();
  if (error) {
    const notice = node('aside', null, 'interest-interpreter-error');
    notice.setAttribute('role', 'status'); notice.append(node('p', error));
    wordsForm?.after(notice);
  }
}
