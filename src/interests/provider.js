import { interpret, validateProfile } from './interpret.js';
import { interpret as interpretWithGemini } from './gemini.js';

const BASIC_ERROR = 'The interpreter could not connect. You can retry or use basic matching.';
const slug = value => String(value || '').toLocaleLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'topic';
const key = value => String(value || '').toLocaleLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

function normalizeRemote(result, words, options) {
  if (!result || !result.profile || !Array.isArray(result.profile.topics) ||
      !(result.question === null || typeof result.question === 'string')) throw new TypeError('Invalid interpreter response');
  const local = interpret(words, options).profile;
  const flags = Array.isArray(result.flags) ? result.flags.filter(flag => typeof flag === 'string').slice(0, 8) : [];
  const instructionRemoved = flags.some(flag => /instruction-like text removed/i.test(flag));
  const catalog = Array.isArray(options.topics) ? options.topics : [];
  const fixedById = new Map(catalog.map(topic => [topic.id, topic]));
  const fixedByLabel = new Map(catalog.map(topic => [key(topic.label), topic]));
  const localById = new Map(local.topics.map(topic => [topic.id, topic]));
  const comboFor = topic => {
    const text = key(`${topic.id || ''} ${topic.label || ''} ${topic.sourcePhrase || ''} ${(topic.include || []).join(' ')}`);
    if (/\bpayments?\s+and\s+banking\s+news\b/.test(text)) return { id: 'custom-payments-banking', label: 'Payments and banking' };
    if (/\bhealth insurance\b/.test(text) && /\bfinance policy\b/.test(text)) return { id: 'custom-health-insurance-finance-policy', label: 'Health insurance and finance policy' };
    if (/\bai in finance\b|\bartificial intelligence in finance\b/.test(text)) return { id: 'custom-ai-in-finance', label: 'AI in finance' };
    return null;
  };
  const mutedByKey = new Map();
  for (const item of Array.isArray(result.profile.muted) ? result.profile.muted : []) {
    const label = typeof item?.label === 'string' ? item.label.trim() : '';
    if (label && !mutedByKey.has(key(label))) mutedByKey.set(key(label), label);
  }
  const mutedLabels = [...mutedByKey.values()];
  const mutedKeys = new Set(mutedLabels.map(key));
  const usedIds = new Set();
  const seenTopics = new Set();
  const uniqueTopics = result.profile.topics.filter(topic => {
    if (!topic || typeof topic !== 'object') return true;
    const excludedTopic = mutedKeys.has(key(topic.label)) && topic.exclude?.length && !topic.mustFollow;
    const negativeSource = /\b(?:skip|avoid|exclude|mute)\s+/i.test(topic.sourcePhrase || '');
    return !excludedTopic && !negativeSource;
  }).filter(topic => {
    if (!topic || typeof topic !== 'object') return true;
    const combo = comboFor(topic);
    const fixed = fixedById.get(topic.id) || fixedByLabel.get(key(topic.label));
    const identity = combo?.id || fixed?.id || `custom:${key(topic.label)}`;
    if (seenTopics.has(identity)) return false;
    seenTopics.add(identity);
    return true;
  });
  const topics = uniqueTopics.map((topic, index) => {
    if (!topic || typeof topic !== 'object' || typeof topic.label !== 'string' || !topic.label.trim()) throw new TypeError('Invalid topic');
    const phrase = typeof topic.sourcePhrase === 'string' && topic.sourcePhrase.trim() ? topic.sourcePhrase.trim() : topic.label;
    const combo = comboFor(topic);
    const sourceKey = key(phrase);
    const fixed = fixedById.get(topic.id) || fixedByLabel.get(key(topic.label));
    const labelWords = new Set(key(topic.label).split(' '));
    const idAlias = [...localById.values()].find(candidate => topic.id === candidate.id || topic.id.startsWith(`${candidate.id}-`) || labelWords.has(candidate.id));
    const localMatch = localById.get(topic.id) || idAlias || local.topics.find(candidate => {
      const candidateKey = key(candidate.sourcePhrase);
      return candidateKey && (sourceKey === candidateKey || candidateKey === sourceKey);
    });
    const mapped = fixed || (localMatch && fixedById.get(localMatch.id));
    let id = combo?.id || mapped?.id || `custom-${slug(topic.label)}`;
    if (usedIds.has(id)) {
      if (mapped) throw new TypeError('Duplicate fixed topic');
      let suffix = index + 1;
      while (usedIds.has(`${id}-${suffix}`)) suffix += 1;
      id = `${id}-${suffix}`;
    }
    usedIds.add(id);
    const suppliedExclude = Array.isArray(topic.exclude) ? topic.exclude.filter(value => typeof value === 'string').slice(0, 8) : [];
    const mutedWords = mutedLabels.map(label => new Set(key(label).split(' ')));
    const exclude = suppliedExclude.filter(value => {
      const words = key(value).split(' ').filter(Boolean);
      return !mutedWords.some(muted => words.some(word => muted.has(word)));
    });
    const suppliedInclude = Array.isArray(topic.include) ? topic.include.filter(value => typeof value === 'string').slice(0, 8) : [];
    const include = suppliedInclude.length || exclude.length ? suppliedInclude : [phrase];
    return {
      id,
      label: combo?.label || mapped?.label || topic.label.trim().slice(0, 60),
      scope: mapped?.scope || String(topic.scope || 'A topic you named that is not in this sample edition yet.').slice(0, 180),
      include,
      exclude,
      depth: topic.depth,
      cadence: localMatch?.cadence || 'daily',
      mustFollow: !instructionRemoved && localMatch?.mustFollow === true,
      sourcePhrase: (combo?.label || phrase).slice(0, 500),
    };
  });
  const profile = {
    version: 1,
    words,
    minutes: [10, 12, 15].includes(options.minutes) ? options.minutes : ([10, 12, 15].includes(result.profile.minutes) ? result.profile.minutes : 12),
    background: typeof result.profile.background === 'string' ? result.profile.background.slice(0, 120) : null,
    topics,
    muted: mutedLabels.map(label => ({ label })),
  };
  validateProfile(profile);
  return { profile, question: result.question, flags, provider: 'remote' };
}

export async function interpretProfile(words, options = {}) {
  const context = { topics: options.topics, minutes: options.minutes };
  if (options.localOnly) return { ...interpret(words, context), provider: 'local' };
  if (typeof words !== 'string' || words.length > 2000 || !words.trim()) {
    return { ...interpret(words, context), provider: 'local' };
  }
  try {
    const result = await interpretWithGemini(words, context, { signal: options.signal });
    if (options.signal?.aborted) throw new DOMException('Interpretation canceled.', 'AbortError');
    if (result.provider === 'local') {
      return { ...result, fallbackError: result.fallbackError || BASIC_ERROR };
    }
    return normalizeRemote(result, words, context);
  } catch (error) {
    if (options.signal?.aborted || error?.name === 'AbortError') throw error;
    return { ...interpret(words, context), provider: 'local', fallbackError: BASIC_ERROR };
  }
}
