const DEPTHS = new Set(['headline', 'standard', 'deeper']);
const CADENCES = new Set(['daily', 'change', 'weekly', 'weekends']);
const DURATIONS = new Set([10, 12, 15]);

const TOPIC_RULES = [
  { id: 'payments', patterns: [/\bpayments?\b/gi, /\bmoney\b/gi, /\bsettlement\b/gi, /\btransfers?\b/gi, /\b(?:credit|debit) cards?\b/gi, /\bcheckout\b/gi] },
  { id: 'banking', patterns: [/\bbank(?:s|ing)?\b/gi, /\bsavings?\b/gi, /\bdeposits?\b/gi, /\blending\b/gi, /\bmortgages?\b/gi, /\binterest rates?\b/gi] },
  { id: 'digital', patterns: [/\bdigital(?: life)?\b/gi, /\bprivacy\b/gi, /\bidentit(?:y|ies)\b/gi, /\bpasskeys?\b/gi, /\bpasswords?\b/gi, /\bcybersecurity\b/gi, /\bonline security\b/gi, /\btechnology\b/gi, /\btech\b/gi] },
  { id: 'insurance', patterns: [/\binsurance\b/gi, /\bcoverage\b/gi, /\bclaims?\b/gi, /\bcopays?\b/gi, /\bpremiums?\b/gi, /\bhealth plans?\b/gi] },
  { id: 'sports', patterns: [/\bsports?\b/gi, /\bsoccer\b/gi, /\bfootball\b/gi, /\bbaseball\b/gi, /\bbasketball\b/gi, /\bleagues?\b/gi] },
];

const NEGATIVE = /\b(?:skip|ignore|mute|leave out|not interested in|not keen on|don't want(?: updates)? on|do not want(?: updates)? on|don't follow|do not follow)\b[^.!?]*$/i;
const INSTRUCTION_LIKE = [
  /\b(?:ignore|disregard|forget)\b[^.!?\n]{0,180}\b(?:previous|prior|earlier|above|system)\s+(?:instructions?|prompt)\b/i,
  /\byou\s+are\s+now\b/i,
  /\bas\s+an\s+ai\b/i,
  /\b(?:print|reveal)\b[^.!?\n]{0,100}\b(?:system|hidden|secret|private|developer|initial)?\s*(?:prompt|instructions?|key|secrets?|rules?)\b/i,
];
const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
const titleCase = value => clean(value).split(' ').map(word => word ? word[0].toLocaleUpperCase() + word.slice(1) : '').join(' ');

function topicCatalog(context) {
  const supplied = Array.isArray(context?.topics) ? context.topics : [];
  const byId = new Map(supplied.filter(topic => topic && typeof topic.id === 'string').map(topic => [topic.id, topic]));
  return byId;
}

function sourcePhraseFor(sentence, start, end) {
  const separators = /[,;.!?]|\b(?:and|but|while|whereas)\b/gi;
  let left = 0;
  let right = sentence.length;
  let separator;
  while ((separator = separators.exec(sentence))) {
    const separatorEnd = separators.lastIndex;
    if (separatorEnd <= start) left = separatorEnd;
    else if (separator.index >= end) { right = separator.index; break; }
  }
  const phrase = sentence.slice(left, right).trim();
  return phrase || sentence.trim();
}

function sourcePreferenceScore(match) {
  return Number(match.mustFollow) * 4 + Number(match.excluded) * 2 +
    Number(/\b(headlines?|brief|just the headlines|deep(?:er)?|in detail|full detail|explain(?:ers)?|thoroughly|weekly|weekends?|only when|daily|every day|each day)\b/i.test(match.source));
}

function matchesFor(sentence) {
  const matches = [];
  for (const rule of TOPIC_RULES) {
    for (const pattern of rule.patterns) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(sentence))) {
        const before = sentence.slice(0, match.index);
        const clause = before.split(/[,;.!?]|\bbut\b|\bwhile\b|\bwhereas\b/i).at(-1) || '';
        const source = sourcePhraseFor(sentence, match.index, pattern.lastIndex);
        matches.push({ id: rule.id, start: match.index, end: pattern.lastIndex, source, excluded: NEGATIVE.test(clause), mustFollow: /\b(?:must follow|always follow|never miss)\b/i.test(`${clause} ${source}`) });
        if (!match[0].length) pattern.lastIndex += 1;
      }
    }
  }
  return matches.sort((a, b) => a.start - b.start);
}

function unknownInterests(words) {
  const found = [];
  const add = (phraseText, offset, excluded, source) => {
    let cursor = 0;
    for (const part of phraseText.split(/,|\bbut\b|\bwhile\b|\band\b/i)) {
      const phrase = clean(part.replace(/\b(?:every day|daily|weekly|on weekends|in detail|headlines?)\b/gi, ''));
      const hasKnown = TOPIC_RULES.some(topic => topic.patterns.some(pattern => {
        pattern.lastIndex = 0;
        return pattern.test(phrase);
      }));
      if (phrase && !hasKnown) found.push({ phrase, start: offset + cursor + part.indexOf(part.trim()), excluded,
        source: clean(part) || clean(source || phrase) });
      cursor += part.length + 1;
    }
  };
  const positive = /\b(?:interested in|care about|follow|want updates on|want to know about|keep me posted on|track)\s+([^.!?;\n]{2,90})/gi;
  let match;
  while ((match = positive.exec(words))) add(match[1], match.index + match[0].indexOf(match[1]), false, match[0]);
  const negative = /\b(?:skip|ignore|mute|leave out|not interested in|not keen on|don't want(?: updates)? on|do not want(?: updates)? on|don't follow|do not follow)\s+([^.!?;\n]{2,90})/gi;
  while ((match = negative.exec(words))) {
    if (/\b(?:previous instructions?|executable|javascript|system prompt|commands?)\b/i.test(match[1])) continue;
    add(match[1], match.index + match[0].indexOf(match[1]), true, match[0]);
  }
  const byId = new Map();
  for (const item of found) {
    const id = `custom-${item.phrase.toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40)}`;
    if (id === 'custom-') continue;
    const prior = byId.get(id);
    if (!prior || item.excluded) byId.set(id, { ...item, id });
  }
  return [...byId.values()];
}

function muteLabel(phrase) {
  const label = clean(phrase).replace(/\b(?:hype|news|updates?|stories|content|coverage)\b.*$/i, '').trim();
  return titleCase(label || phrase);
}

function preferenceFor(text, allText, type) {
  const source = `${text} ${allText}`.toLocaleLowerCase();
  if (type === 'depth') {
    if (/\b(headlines?|brief headlines?|just the headlines)\b/.test(source)) return 'headline';
    if (/\b(deep(?:er)?|in detail|full detail|explain(?:ers)?|thoroughly)\b/.test(source)) return 'deeper';
    return 'standard';
  }
  if (/\b(weekends?|saturdays? and sundays?)\b/.test(source)) return 'weekends';
  if (/\b(weekly|once a week|every week|one weekly recap)\b/.test(source)) return 'weekly';
  if (/\b(only when (?:it|something) changes|when it changes|only major changes|only important changes|only when it changes|when there(?:'| i)s a change)\b/.test(source)) return 'change';
  if (/\b(daily|every day|each day|day to day)\b/.test(source)) return 'daily';
  return 'daily';
}

function unscopedSettings(sentences) {
  const settings = [];
  for (const sentenceMatch of sentences) {
    const sentence = sentenceMatch[0].trim();
    const matches = matchesFor(sentence);
    if (!matches.length) { settings.push(sentence); continue; }
    const first = Math.min(...matches.map(match => match.start));
    const last = Math.max(...matches.map(match => match.end));
    const prefix = sentence.slice(0, first);
    const suffix = sentence.slice(last);
    const trailingSettings = suffix.match(/[,;:]([^,;:]*)$/)?.[0] || '';
    if (/\b(headlines?|brief headlines?|just the headlines|deep(?:er)?|in detail|full detail|explain(?:ers)?|thoroughly|weekends?|weekly|once a week|every week|only when (?:it|something) changes|when it changes|daily|every day|each day|day to day)\b/i.test(prefix)) settings.push(prefix);
    if (trailingSettings && /\b(headlines?|brief headlines?|just the headlines|deep(?:er)?|in detail|full detail|explain(?:ers)?|thoroughly|weekends?|weekly|once a week|every week|only when (?:it|something) changes|when it changes|daily|every day|each day|day to day)\b/i.test(trailingSettings)) settings.push(trailingSettings);
  }
  return settings.join(' ');
}

function extractBackground(words) {
  const match = words.match(/\b(?:I work|I am working|my work is|my job is)\s+in\s+([^.!?;\n]+)/i);
  if (!match) return null;
  const phrase = clean(match[1].split(/,|\bbut\b|\bso\b|\band\s+I\b/i)[0]);
  return phrase ? phrase.slice(0, 120) : null;
}

export function validateProfile(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.version !== 1) throw new TypeError('Interest profile version must be 1.');
  if (typeof value.words !== 'string' || value.words.length > 2000) throw new TypeError('Interest profile words must be a string of at most 2000 characters.');
  if (!DURATIONS.has(value.minutes)) throw new TypeError('Interest profile minutes must be 10, 12, or 15.');
  if (value.background !== null && (typeof value.background !== 'string' || value.background.length > 120)) throw new TypeError('Interest profile background is invalid.');
  if (!Array.isArray(value.topics) || value.topics.length > 12) throw new TypeError('Interest profile topics must be an array of at most 12 items.');
  if (value.behavior_weights !== undefined && (!value.behavior_weights || typeof value.behavior_weights !== 'object' || Array.isArray(value.behavior_weights) ||
      Object.entries(value.behavior_weights).length > 12 || Object.entries(value.behavior_weights).some(([id, weight]) =>
        !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || !Number.isFinite(weight) || weight < 0.5 || weight > 1.5))) {
    throw new TypeError('Interest profile behavior weights are invalid.');
  }
  if (!Array.isArray(value.muted) || value.muted.length > 12) throw new TypeError('Interest profile muted must be an array of at most 12 items.');
  const ids = new Set();
  for (const topic of value.topics) {
    if (!topic || typeof topic !== 'object' || Array.isArray(topic) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(topic.id || '') || ids.has(topic.id)) throw new TypeError('Interest profile topic IDs must be unique slugs.');
    ids.add(topic.id);
    if (typeof topic.label !== 'string' || !topic.label.trim() || topic.label.length > 60 || typeof topic.scope !== 'string' || topic.scope.length > 180) throw new TypeError('Interest profile topic label or scope is invalid.');
    if (!['include', 'exclude'].every(key => Array.isArray(topic[key]) && topic[key].length <= 8 && topic[key].every(phrase => typeof phrase === 'string' && phrase.length <= 240))) throw new TypeError('Interest profile topic phrases are invalid.');
    if (!DEPTHS.has(topic.depth) || !CADENCES.has(topic.cadence) || typeof topic.mustFollow !== 'boolean' || typeof topic.sourcePhrase !== 'string' || topic.sourcePhrase.length > 500) throw new TypeError('Interest profile topic preferences are invalid.');
  }
  if (!value.muted.every(item => item && typeof item.label === 'string' && item.label.trim() && item.label.length <= 60)) throw new TypeError('Interest profile muted labels are invalid.');
  return value;
}

export function interpret(words, context = {}) {
  const original = typeof words === 'string' ? words.slice(0, 2000) : '';
  const normalized = clean(original);
  const catalog = topicCatalog(context);
  const detected = new Map();
  const allSentences = [...normalized.matchAll(/[^.!?]+(?:[.!?]+|$)/g)];
  const safeSentences = allSentences.filter(match => !INSTRUCTION_LIKE.some(pattern => pattern.test(match[0])));
  const instructionRemoved = safeSentences.length !== allSentences.length;
  const safeNormalized = safeSentences.map(match => match[0].trim()).join(' ');
  const sentenceMatches = safeSentences;
  for (const sentenceMatch of sentenceMatches) {
    const sentence = sentenceMatch[0].trim();
    const sentenceOffset = sentenceMatch.index + sentenceMatch[0].indexOf(sentence);
    const roleOnly = /^I\s+(?:currently\s+)?(?:work(?:ing)?|my work is|my job is)\s+in\b/i.test(sentence) && !/\b(?:follow|care about|interested|updates|headlines|include|must follow)\b/i.test(sentence);
    if (roleOnly) continue;
    for (const localMatch of matchesFor(sentence)) {
      const match = { ...localMatch, start: sentenceOffset + localMatch.start };
      const previous = detected.get(match.id);
      if (!previous || (match.mustFollow && !previous.mustFollow) || (match.excluded && !previous.excluded && !previous.mustFollow) || (!match.excluded && previous.excluded) || sourcePreferenceScore(match) > sourcePreferenceScore(previous)) detected.set(match.id, match);
    }
  }
  const settingsText = unscopedSettings(sentenceMatches);
  const unknown = unknownInterests(safeNormalized);
  const excludedItems = [...detected.values()].filter(item => item.excluded).map(item => ({
    label: catalog.get(item.id)?.label || titleCase(item.id), phrase: item.source || item.phrase || item.id
  }));
  excludedItems.push(...unknown.filter(item => item.excluded).map(item => ({ label: muteLabel(item.phrase), phrase: item.phrase })));
  const allDetected = [...detected.values()].filter(item => !item.excluded).map(item => ({ ...item, id: item.id, label: catalog.get(item.id)?.label || titleCase(item.id), scope: catalog.get(item.id)?.scope || 'A topic you named that is not in this sample edition yet.' }));
  for (const item of unknown.filter(item => !item.excluded)) {
    const id = item.id;
    if (!allDetected.some(topic => topic.id === id)) allDetected.push({ ...item, label: titleCase(item.phrase), scope: 'A topic you named that is not in this sample edition yet.', excluded: item.excluded === true });
  }
  allDetected.sort((a, b) => a.start - b.start);
  const topics = allDetected.slice(0, 12).map(item => {
    const sourcePhrase = clean(item.source || item.phrase || original).slice(0, 500);
    const muted = item.excluded === true && item.mustFollow !== true;
    const localCadence = preferenceFor(sourcePhrase, settingsText, 'cadence');
    const localDepth = preferenceFor(sourcePhrase, settingsText, 'depth');
    const mustFollow = /\b(?:must follow|always follow|never miss)\b/i.test(sourcePhrase);
    return {
      id: item.id,
      label: item.label,
      scope: item.scope,
      include: muted ? [] : [sourcePhrase],
      exclude: muted ? [sourcePhrase] : [],
      depth: localDepth,
      cadence: localCadence,
      mustFollow: item.mustFollow === true || mustFollow,
      sourcePhrase,
    };
  });
  const muted = [...new Map(excludedItems.map(item => {
    const label = muteLabel(item.label || item.phrase);
    return [label.toLocaleLowerCase(), { label }];
  })).values()];
  const minutes = DURATIONS.has(context.minutes) ? context.minutes : 12;
  const profile = validateProfile({ version: 1, words: original, minutes, background: extractBackground(safeNormalized), topics, muted });
  const unsupported = topics.find(topic => topic.id.startsWith('custom-') && !topic.exclude.length);
  const question = !topics.length
    ? 'Which topics would you like to follow?'
    : unsupported
      ? `I could not match “${unsupported.label}” to a sample topic. You can keep it as a custom topic.`
      : null;
  return { profile, question, flags: instructionRemoved ? ['instruction-like text removed'] : [] };
}
