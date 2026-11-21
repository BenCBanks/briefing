const PROFILE_KEY = 'briefing:v1:interest-profile';
const PROFILE_VERSION = 1;

const defaults = {
  payments: { include: ['payment rails', 'settlement', 'cards and cash'], exclude: ['personal account advice'] },
  banking: { include: ['deposits', 'lending', 'financial stability'], exclude: ['individual bank recommendations'] },
  digital: { include: ['privacy', 'identity', 'practical cybersecurity'], exclude: ['product promotion'] },
  insurance: { include: ['coverage', 'exclusions', 'claims'], exclude: ['personal coverage advice'] },
  sports: { include: ['rules', 'tactics', 'competition formats'], exclude: ['transfer rumors'] },
};

function phraseFor(words, topic) {
  const aliases = [topic.label, topic.id, ...(defaults[topic.id]?.include || [])].map(value => value.toLocaleLowerCase());
  for (const sentence of String(words).split(/[.!?]+/)) {
    const parts = sentence.split(/,|\b(?:and|but|while)\b/i).map(part => part.trim()).filter(Boolean);
    let active = false;
    const phrase = [];
    for (const part of parts) {
      const namesAnotherTopic = Object.keys(defaults).some(id => id !== topic.id &&
        [id, ...(defaults[id]?.include || [])].some(term => part.toLocaleLowerCase().includes(term.toLocaleLowerCase())));
      const matches = aliases.some(alias => part.toLocaleLowerCase().includes(alias));
      if (matches) { active = true; phrase.push(part); }
      else if (active && !namesAnotherTopic && phrase.length < 3) phrase.push(part);
      else if (namesAnotherTopic) active = false;
    }
    if (phrase.length) return phrase.join(', ');
  }
  return topic.label;
}

export function defaultProfile(topics) {
  return {
    version: PROFILE_VERSION,
    words: 'I follow payments, banking, digital privacy, insurance, and sports. Give me the practical context, and keep each daily edition to about 12 minutes.',
    minutes: 12,
    background: null,
    topics: topics.map(topic => ({
      id: topic.id, label: topic.label, scope: topic.scope,
      include: [...(defaults[topic.id]?.include || [])], exclude: [...(defaults[topic.id]?.exclude || [])],
      depth: 'standard', cadence: 'daily', mustFollow: false,
      sourcePhrase: phraseFor('I follow payments, banking, digital privacy, insurance, and sports. Give me the practical context, and keep each daily edition to about 12 minutes.', topic),
    })),
    muted: [],
  };
}

function validTopic(topic) {
  return topic && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(topic.id || '') && typeof topic.label === 'string' && topic.label.length <= 60 && typeof topic.scope === 'string' && topic.scope.length <= 180 &&
    ['headline', 'standard', 'deeper'].includes(topic.depth) &&
    ['daily', 'change', 'weekly', 'weekends'].includes(topic.cadence) &&
    Array.isArray(topic.include) && topic.include.length <= 8 && topic.include.every(value => typeof value === 'string' && value.length <= 240) &&
    Array.isArray(topic.exclude) && topic.exclude.length <= 8 && topic.exclude.every(value => typeof value === 'string' && value.length <= 240) &&
    typeof topic.mustFollow === 'boolean' && typeof topic.sourcePhrase === 'string' && topic.sourcePhrase.length <= 500;
}

export function loadProfile(topics) {
  const fallback = defaultProfile(topics);
  try {
    const raw = JSON.parse(localStorage.getItem(PROFILE_KEY));
    if (raw?.version !== PROFILE_VERSION || typeof raw.words !== 'string' || raw.words.length > 2000 || !Array.isArray(raw.topics) || raw.topics.length > 12) return fallback;
    const valid = raw.topics.filter(validTopic);
    if (new Set(valid.map(topic => topic.id)).size !== valid.length || valid.length !== raw.topics.length) return fallback;
    if (![10, 12, 15].includes(raw.minutes) || (raw.background !== null && (typeof raw.background !== 'string' || raw.background.length > 120)) || !Array.isArray(raw.muted) || raw.muted.length > 12 ||
        !raw.muted.every(item => item && typeof item.label === 'string' && item.label.trim() && item.label.length <= 60)) return fallback;
    return {
      ...fallback, ...raw,
      topics: valid.map(topic => ({ ...topic })),
      muted: raw.muted,
    };
  } catch { return fallback; }
}

export function saveProfile(profile) {
  const safe = { ...profile, version: PROFILE_VERSION, words: String(profile.words || '').slice(0, 2000) };
  if (![10, 12, 15].includes(safe.minutes) || (safe.background !== null && (typeof safe.background !== 'string' || safe.background.length > 120)) || !Array.isArray(safe.topics) || safe.topics.length > 12 ||
      !safe.topics.every(validTopic) || new Set(safe.topics.map(topic => topic.id)).size !== safe.topics.length ||
      !Array.isArray(safe.muted) || safe.muted.length > 12 || !safe.muted.every(item => item && typeof item.label === 'string' && item.label.trim() && item.label.length <= 60)) {
    throw new TypeError('The interpreted interests do not match the supported profile shape.');
  }
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(safe)); } catch { /* local-only state may be unavailable */ }
  return safe;
}

// Deterministic Stage A interpreter seam. the shared interpret() replaces this adapter when available.
export function interpretLocally(words, catalog, previous = null) {
  const text = String(words || '').trim();
  const normalized = text.toLocaleLowerCase();
  const current = previous || defaultProfile(catalog);
  const topics = catalog.filter(topic => {
    const terms = [topic.label, topic.id, ...(defaults[topic.id]?.include || [])];
    return terms.some(term => normalized.includes(term.toLocaleLowerCase()));
  }).map(topic => {
    const prior = current.topics.find(item => item.id === topic.id);
    const phrase = phraseFor(text, topic);
    const cadence = /\bweekly\b/i.test(phrase) ? 'weekly' : /\bweekend|weekends\b/i.test(phrase) ? 'weekends' : /\bwhen it changes|only when\b/i.test(phrase) ? 'change' : prior?.cadence || 'daily';
    const depth = /\b(headlines? only|brief)\b/i.test(phrase) ? 'headline' : /\b(deep|deeper|in depth)\b/i.test(phrase) ? 'deeper' : prior?.depth || 'standard';
    return { ...(prior || {}), id: topic.id, label: topic.label, scope: topic.scope,
      include: prior?.include || [...(defaults[topic.id]?.include || [])], exclude: prior?.exclude || [], depth, cadence,
      mustFollow: prior?.mustFollow || false, sourcePhrase: phrase };
  });
  const requestedMinutes = Number(text.match(/\b(10|12|15)\s*(?:minutes?|mins?)\b/i)?.[1] || current.minutes || 12);
  const minutes = [10, 12, 15].includes(requestedMinutes) ? requestedMinutes : current.minutes || 12;
  return { profile: { ...current, words: text, minutes, topics }, question: topics.length ? null : 'Which subjects would you like to follow?' };
}
