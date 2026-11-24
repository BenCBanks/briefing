export function buildChipLabel(topic) {
  const excluded = topic.exclude?.length && !topic.mustFollow;
  const suppliedLabel = String(topic.label || '').trim();
  const raw = String((excluded && topic.exclude?.[0]) || topic.sourcePhrase || topic.include?.[0] || suppliedLabel).replace(/[.!?].*$/, '');
  const concise = raw.replace(/^(?:i\s+)?(?:follow|care about|work in|want updates (?:on|about)|want to know about|want to skip|keep me posted on|track|skip|exclude)\s+/i, '')
    .replace(/\b(?:every day|daily|weekly|on weekends|weekends|in detail|headlines?)\b.*$/i, '')
    .replace(/[,:;]+$/, '').trim();
  const phraseLabel = concise.replace(/^(?:and|or)\s+/i, '').split(/\s+/).slice(0, 6).join(' ') || suppliedLabel;
  const label = topic.cadence === 'weekends' || !suppliedLabel || /^new topic$/i.test(suppliedLabel) || phraseLabel.toLocaleLowerCase() !== suppliedLabel.toLocaleLowerCase()
    ? phraseLabel : suppliedLabel;
  const readable = label ? label[0].toLocaleUpperCase() + label.slice(1) : 'Interest';
  if (excluded) return readable;
  return topic.cadence === 'weekends' && !/\bweekends\b/i.test(readable) ? `${readable} · weekends` : readable;
}

export function profileTopicNames(profile) {
  const names = (profile?.topics || []).filter(topic => !topic.exclude?.length).map(buildChipLabel);
  return names.length ? names.slice(0, 3).join(' or ') : 'your interests';
}

export function profileBuildKey(profile) {
  return JSON.stringify({ words: profile?.words || '', minutes: profile?.minutes || 12,
    topics: (profile?.topics || []).map(topic => ({ id: topic.id, include: topic.include, exclude: topic.exclude,
      depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow, sourcePhrase: topic.sourcePhrase })),
    muted: profile?.muted || [] });
}

export function filterGeneralEditionForProfile(edition, profile, catalog = []) {
  const preferences = (profile?.topics || []).filter(topic => (topic.include || []).length || topic.mustFollow === true)
    .filter(topic => !topic.exclude?.length || topic.mustFollow === true);
  const sourceSections = edition.sections || [];
  const coverageById = new Map((edition.coverage || []).map(row => [row.topic_id, row]));
  const chosenSections = [];
  const coverage = [];
  for (const preference of preferences) {
    const known = catalog.find(topic => topic.id === preference.id || topic.label.toLocaleLowerCase() === String(preference.label || '').toLocaleLowerCase());
    const topicId = known?.id || preference.id;
    const matched = sourceSections.filter(section => (section.topic_ids || []).includes(topicId));
    const fallbackPhrases = [preference.scope, preference.label, ...(preference.include || []), preference.sourcePhrase]
      .map(value => String(value || '').trim().toLocaleLowerCase()).filter(value => value.length >= 4);
    const phraseMatches = matched.length ? [] : sourceSections.filter(section => {
      const text = `${section.heading || ''} ${section.body || ''}`.toLocaleLowerCase();
      return fallbackPhrases.some(phrase => text.includes(phrase));
    });
    const stories = matched.length ? matched : phraseMatches;
    for (const section of stories) {
      if (!chosenSections.some(item => item.id === section.id)) {
        chosenSections.push({ ...section, topic_ids: [...new Set([...(section.topic_ids || []), preference.id])] });
      } else {
        const existing = chosenSections.find(item => item.id === section.id);
        existing.topic_ids = [...new Set([...(existing.topic_ids || []), preference.id])];
      }
    }
    if (stories.length) {
      const original = coverageById.get(topicId) || coverageById.get(preference.id);
      coverage.push(original
        ? { ...original, topic_id: preference.id, label: preference.label, state: ['quiet', 'limited'].includes(original.state) ? 'included' : original.state }
        : { topic_id: preference.id, label: preference.label, state: 'included' });
    }
  }
  return { ...edition, sections: chosenSections, coverage };
}
