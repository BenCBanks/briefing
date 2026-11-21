const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
const unique = values => [...new Map(values.map(value => [clean(value).toLocaleLowerCase(), clean(value)])
  .filter(([key]) => key)).values()];
const slug = value => clean(value).toLocaleLowerCase().normalize('NFKD')
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'topic';
const depthLabels = { headline: 'Headlines only', standard: 'Standard context', deeper: 'More depth and background' };

function exclusionsFromWords(words) {
  const found = [];
  const re = /\b(?:skip|avoid|exclude|leave out|not interested in)\s+([^.!?;\n]+)/gi;
  let match;
  while ((match = re.exec(String(words || '')))) {
    found.push(...match[1].split(/,|\band\b|\bor\b/i).map(value => value.trim()));
  }
  return unique(found);
}

export function deriveInterestBrief(profile = {}) {
  const topics = Array.isArray(profile.topics) ? profile.topics : [];
  const muted = new Set((profile.muted || []).map(item => clean(item.label).toLocaleLowerCase()));
  const selected = topics.filter(topic => !muted.has(clean(topic.label).toLocaleLowerCase()));
  const saved = profile.brief && typeof profile.brief === 'object' ? profile.brief : {};
  const subjects = Array.isArray(saved.subjects) && saved.subjects.length
    ? unique(saved.subjects) : unique(selected.map(topic => topic.label));
  const exclusions = Array.isArray(saved.exclusions)
    ? unique(saved.exclusions)
    : unique([...selected.flatMap(topic => topic.exclude || []), ...exclusionsFromWords(profile.words)]);
  const geography = clean(saved.geography);
  const depths = unique(selected.map(topic => depthLabels[topic.depth] || 'Standard context'));
  const depth = ['headline', 'standard', 'deeper'].includes(saved.depth)
    ? saved.depth : depths.length === 1
      ? selected[0]?.depth || 'standard' : 'mixed';
  const depthSummary = depth === 'mixed'
    ? selected.map(topic => `${topic.label}: ${depthLabels[topic.depth] || 'Standard context'}`).join(' · ')
    : depthLabels[depth] || 'Standard context';
  return {
    geography: geography || 'No location specified',
    subjects,
    exclusions,
    depth,
    depthSummary: depthSummary || 'Standard context',
  };
}

function list(value) {
  return unique(String(value || '').split(/[,;\n]+/));
}

export function applyInterestBrief(profile, changes = {}) {
  const current = profile || { version: 1, words: '', minutes: 12, background: null, topics: [], muted: [] };
  const prior = deriveInterestBrief(current);
  const geography = clean(changes.geography ?? prior.geography).replace(/^No location specified$/i, '').slice(0, 180);
  const subjects = list(changes.subjects ?? prior.subjects.join(', ')).map(value => value.slice(0, 60));
  const exclusions = list(changes.exclusions ?? prior.exclusions.join(', ')).map(value => value.slice(0, 240));
  const depth = ['headline', 'standard', 'deeper', 'mixed'].includes(changes.depth) ? changes.depth : prior.depth;
  if (!subjects.length) throw new TypeError('Add at least one subject to your interest brief.');
  if (subjects.length > 12) throw new TypeError('Keep the interest brief to 12 subjects or fewer.');
  if (exclusions.length > 8) throw new TypeError('Keep the interest brief to 8 exclusions or fewer.');

  const previous = current.topics || [];
  const byLabel = new Map(previous.map(topic => [clean(topic.label).toLocaleLowerCase(), topic]));
  const used = new Set();
  const topics = subjects.map((rawLabel, index) => {
    const label = rawLabel.slice(0, 60);
    const priorTopic = byLabel.get(label.toLocaleLowerCase());
    let id = priorTopic?.id || `custom-${slug(label)}`;
    let suffix = index + 1;
    while (used.has(id)) id = `${slug(label)}-${suffix++}`;
    used.add(id);
    const topicDepth = depth === 'mixed' ? priorTopic?.depth || 'standard' : depth;
    const scope = geography ? `${label} in ${geography}.` : priorTopic?.scope || label;
    return {
      ...(priorTopic || {}),
      id,
      label: label.slice(0, 60),
      scope: scope.slice(0, 180),
      include: priorTopic?.include?.length ? priorTopic.include : [label],
      exclude: exclusions.slice(0, 8),
      depth: topicDepth,
      cadence: priorTopic?.cadence || 'daily',
      mustFollow: priorTopic?.mustFollow === true,
      sourcePhrase: label.slice(0, 500),
    };
  });
  const words = [
    `Follow ${subjects.join(', ')}${geography ? ` in ${geography}` : ''}.`,
    exclusions.length ? `Skip ${exclusions.join(', ')}.` : '',
    depth === 'mixed' ? '' : `Use ${depthLabels[depth].toLocaleLowerCase()} for these subjects.`,
  ].filter(Boolean).join(' ');
  const subjectKeys = new Set(subjects.map(value => value.toLocaleLowerCase()));
  return {
    ...current,
    words: words.slice(0, 2000),
    topics,
    muted: (current.muted || []).filter(item => !subjectKeys.has(clean(item.label).toLocaleLowerCase())),
    brief: { geography, subjects, exclusions, depth },
  };
}
