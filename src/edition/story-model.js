/** Keep abbreviation/decimal periods inside their sentence, including its prefix. */
export function splitSentences(value) {
  const text = asText(value);
  const protectedPeriods = new Set();
  for (const match of text.matchAll(/\b(?:U\.S\.|U\.K\.|Inc\.|Mr\.|Mrs\.|Ms\.|Dr\.|e\.g\.|i\.e\.|a\.m\.|p\.m\.)/giu)) {
    const after = text.slice(match.index + match[0].length).replace(/^["”’')\]]*/u, '').trimStart();
    for (let i = 0; i < match[0].length; i += 1) {
      if (match[0][i] !== '.') continue;
      // Some abbreviations can end sentences. A bare capital is insufficient
      // (U.S. Bank, Inc. CEO, 9 a.m. Monday); use common sentence starters.
      if (i === match[0].length - 1 && /^(?:U\.[SK]\.|Inc\.|[ap]\.m\.)$/iu.test(match[0]) && /^(?:The|This|That|These|Those|It|They|We|He|She|Next|Another|A|An|But|However|Meanwhile|Then)\b/u.test(after)) continue;
      protectedPeriods.add(match.index + i);
    }
  }
  const result = [];
  let start = 0;
  for (const match of text.matchAll(/[.!?]+["”’')\]]*(?=\s|$)/gu)) {
    if (protectedPeriods.has(match.index)) continue;
    const end = match.index + match[0].length;
    result.push(text.slice(start, end).trim());
    start = end;
  }
  if (text.slice(start).trim()) result.push(text.slice(start).trim());
  return result;
}

function asText(value) {
  if (typeof value === 'string') return value.trim();
  if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(' ');
  return '';
}

function safeSourceURL(value) {
  const raw = asText(value);
  if (!raw || /[\u0000-\u001f\u007f]/u.test(raw)) return '';
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) return '';
    return url.href;
  } catch {
    return '';
  }
}

function asParagraphs(value) {
  if (typeof value === 'string') return value.split(/\n\s*\n/u).map(text => text.trim()).filter(Boolean);
  if (Array.isArray(value)) return value.flatMap(item => typeof item === 'string' ? [item.trim()] : []).filter(Boolean);
  return [];
}

function blockText(block) {
  if (typeof block === 'string') return block;
  if (!block || typeof block !== 'object') return '';
  return asText(block.text ?? block.body ?? block.content ?? block.paragraphs);
}

function sectionBlocks(section) {
  const blocks = section?.section_blocks;
  if (Array.isArray(blocks)) {
    return blocks.map(block => ({
      kind: String(block?.kind ?? block?.type ?? block?.section ?? '').toLowerCase().replace(/[\s-]+/gu, '_'),
      text: blockText(block),
    })).filter(block => block.text);
  }
  if (blocks && typeof blocks === 'object') {
    return Object.entries(blocks).flatMap(([kind, value]) => asParagraphs(value).map(text => ({ kind: kind.toLowerCase(), text })));
  }
  return [];
}

function newestDate(current, candidate) {
  if (!current) return candidate;
  if (!candidate) return current;
  const currentTime = Date.parse(current);
  const candidateTime = Date.parse(candidate);
  if (!Number.isFinite(currentTime)) return Number.isFinite(candidateTime) ? candidate : current;
  if (!Number.isFinite(candidateTime)) return current;
  return candidateTime > currentTime ? candidate : current;
}

function sentences(text) {
  return splitSentences(text);
}

function firstSentence(text) {
  return sentences(text)[0] || '';
}

function sentenceCount(text) {
  return sentences(text).length;
}

function sentenceList(paragraphs) {
  return paragraphs.flatMap(paragraph => sentences(paragraph));
}

function uniqueParagraphs(paragraphs, excluding = []) {
  const seen = new Set(excluding.map(value => value.toLocaleLowerCase()));
  return paragraphs.map(value => value.trim()).filter(value => {
    const key = value.toLocaleLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function sourceList(section, context) {
  const byId = new Map((Array.isArray(context?.sources) ? context.sources : []).filter(Boolean).map(source => [source.id, source]));
  const sectionSources = Array.isArray(section?.sources) ? section.sources : [];
  let raw = sectionSources;
  if (Array.isArray(section?.source_ids) && section.source_ids.length) {
    const sectionById = new Map(sectionSources
      .filter(source => source && typeof source === 'object' && source.id)
      .map(source => [source.id, source]));
    raw = section.source_ids.map(id => sectionById.get(id) || byId.get(id)).filter(Boolean);
  } else if (!raw.length && Array.isArray(section?.source_ids)) {
    raw = section.source_ids.map(id => byId.get(id)).filter(Boolean);
  }
  const sourceIndex = new Map();
  const result = [];
  for (let source of raw) {
    if (typeof source === 'string') source = byId.get(source) || { outlet: source };
    if (!source || typeof source !== 'object') continue;
    const outlet = asText(source.outlet ?? source.publisher ?? source.label ?? source.name ?? source.organization ?? source.agency);
    const title = asText(source.title ?? source.source_title);
    const publishedAt = asText(source.published_at ?? source.date);
    const updatedAt = asText(source.updated_at);
    const url = safeSourceURL(source.url ?? source.href ?? source.link);
    const key = `${outlet.toLocaleLowerCase()}|${url || title.toLocaleLowerCase()}`;
    if ((!outlet && !title && !url)) continue;
    const duplicateIndex = sourceIndex.get(key);
    if (duplicateIndex !== undefined) {
      const existing = result[duplicateIndex];
      if (title.length > (existing.title?.length || 0)) existing.title = title;
      if (!existing.published_at && publishedAt) existing.published_at = publishedAt;
      const latestUpdate = newestDate(existing.updated_at, updatedAt);
      if (latestUpdate) existing.updated_at = latestUpdate;
      continue;
    }
    sourceIndex.set(key, result.length);
    result.push({
      outlet: outlet || 'Source',
      ...(title ? { title } : {}),
      ...(publishedAt ? { published_at: publishedAt } : {}),
      ...(updatedAt ? { updated_at: updatedAt } : {}),
      url,
    });
  }
  return result;
}

function primarySourceFor(section, sources, context) {
  const explicit = section?.primary_source;
  const id = section?.primary_source_id ?? (typeof explicit === 'string' ? explicit : '');
  if (id && (!Array.isArray(section?.source_ids) || section.source_ids.includes(id))) {
    const rows = [
      ...(Array.isArray(section?.sources) ? section.sources : []),
      ...(Array.isArray(context?.sources) ? context.sources : []),
    ];
    const selected = rows.find(source => source && typeof source === 'object' && source.id === id);
    if (selected) {
      const outlet = asText(selected.outlet ?? selected.publisher ?? selected.label ?? selected.name ?? selected.organization ?? selected.agency) || 'Source';
      const title = asText(selected.title ?? selected.source_title);
      const url = safeSourceURL(selected.url ?? selected.href ?? selected.link);
      const publishedAt = asText(selected.published_at ?? selected.date);
      const updatedAt = asText(selected.updated_at);
      const match = sources.find(source => url
        ? source.url === url
        : source.outlet === outlet && source.title === title &&
          source.published_at === (publishedAt || undefined) && source.updated_at === (updatedAt || undefined));
      if (match) return match;
    }
  }
  if (explicit && typeof explicit === 'object') {
    const outlet = asText(explicit.outlet ?? explicit.publisher ?? explicit.name);
    const url = safeSourceURL(explicit.url ?? explicit.href);
    if (outlet || url) return { outlet, url };
  }
  const index = Number(section?.primary_source_index);
  if (Number.isInteger(index) && index >= 0 && index < sources.length) return sources[index];
  if (id) {
    const match = sources.find(source => source.url === id || source.outlet === id);
    if (match) return match;
  }
  return sources[0] || null;
}

function kickerFor(section, context) {
  const explicit = asText(section?.kicker ?? section?.kicker_topic ?? section?.topic);
  if (explicit) return explicit;
  const ids = section?.topic_ids || section?.topic_id;
  const id = Array.isArray(ids) ? ids[0] : ids;
  if (!id) return '';
  const topics = Array.isArray(context?.topics) ? context.topics : [];
  return asText(topics.find(topic => topic?.id === id)?.label) || String(id).replace(/[-_]+/gu, ' ');
}

/** Map legacy edition sections and newer story records to the v3 display model. */
export function toStoryModel(section = {}, context = {}) {
  const blocks = sectionBlocks(section);
  const happenedBlock = blocks.filter(block => /what_happened|lede|lead/u.test(block.kind)).map(block => block.text);
  const whyBlock = blocks.filter(block => /why_it_matters|so_what/u.test(block.kind)).map(block => block.text);
  const watchBlock = blocks.filter(block => /things_to_consider|watch/u.test(block.kind)).map(block => block.text);

  const happened = asParagraphs(section.what_happened ?? section.lede ?? section.lead_paragraphs ?? section.body);
  const leadSource = happenedBlock.length ? happenedBlock : happened;
  const leadSentences = sentenceList(leadSource);
  const explicitLede = asParagraphs(section.lede);
  const ledeSentences = explicitLede.length ? sentenceList(explicitLede).slice(0, 3) : leadSentences.slice(0, 3);
  const lede = ledeSentences.join(' ');

  const explicitSoWhat = asText(section.so_what ?? section.soWhat);
  const why = explicitSoWhat || asText(section.why_it_matters) || whyBlock.join(' ');
  const soWhat = firstSentence(why);

  const remainingLead = leadSentences.slice(ledeSentences.length).join(' ');
  const bodySentences = sentenceList(asParagraphs(section.body));
  let bodyLeadLength = 0;
  while (bodyLeadLength < bodySentences.length && bodyLeadLength < leadSentences.length &&
    bodySentences[bodyLeadLength].toLocaleLowerCase() === leadSentences[bodyLeadLength].toLocaleLowerCase()) bodyLeadLength += 1;
  const moreInput = [
    ...asParagraphs(section.more),
    ...blocks.filter(block => /^(?:more|additional|detail|details)$/u.test(block.kind)).map(block => block.text),
    ...(bodyLeadLength ? [bodySentences.slice(bodyLeadLength).join(' ')] : []),
  ];
  const more = uniqueParagraphs([...moreInput, remainingLead], [lede, soWhat]);

  const watchText = asText(section.watch) || asText(section.things_to_consider) || watchBlock.join(' ');
  const watch = watchText ? firstSentence(watchText) || null : null;
  const sources = sourceList(section, context);
  const withdrawn = section.withdrawn === true;
  return {
    corrected: section.corrected === true,
    updated: section.updated === true,
    corrected_at: asText(section.corrected_at) || null,
    withdrawn,
    withdrawn_at: asText(section.withdrawn_at) || null,
    kicker: kickerFor(section, context),
    headline: withdrawn ? 'Story withdrawn' : asText(section.headline ?? section.title ?? section.heading),
    lede: withdrawn ? '' : lede,
    so_what: withdrawn ? '' : soWhat,
    more: withdrawn ? [] : more,
    watch: withdrawn ? null : watch,
    sources,
    primary_source: primarySourceFor(section, sources, context),
  };
}

function words(text) {
  return String(text || '').trim().match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || [];
}

/** A conservative headline excerpt: original words/case, never a generated claim. */
export function shortHeadline(value) {
  const original = asText(value).replace(/\s+/gu, ' ').replace(/[.!?]+$/u, '');
  const text = original
    .replace(/^(?:(?:breaking(?: news)?|update|exclusive|report|analysis):\s*)+/iu, '')
    .replace(/^(?:what to know about|the latest on|a look at)\s+/iu, '')
    .replace(/^(?:(?:U\.S\.|U\.K\.)\s+)?(?:(?:peer-to-peer )?payment network|payments? giant|fintech company)\s+/iu, '');
  const clause = text.split(/:\s*|\s+(?:for|to|by)\s+/iu)[0];
  let tokens = clause.split(/\s+/u).filter(Boolean);
  // A clipped verb/modal can lose its qualifier: fall back to the subject.
  const unfinished = tokens.findIndex((word, i) => i > 0 && /^(?:will|would|could|should|may|might|must|can|cannot|can't|won't|is|are|was|were|has|have|plans?|prepares?|seeks?|aims?|needs?)$/iu.test(word));
  if (unfinished > 0) tokens = tokens.slice(0, unfinished);
  if (/^(?:still|also|now|already|just)$/iu.test(tokens.at(-1))) tokens.pop();
  if (tokens.length > 6) {
    tokens = tokens.slice(0, 6);
    // If a prepositional object was cut, keep the complete subject phrase instead.
    const boundary = tokens.findIndex((word, i) => i > 0 && /^(?:in|on|onto|at|with|from|of|over|under|after|before|as|than)$/iu.test(word));
    if (boundary > 0) tokens = tokens.slice(0, boundary);
  }
  while (tokens.length && /^(?:a|an|the|and|or|but|for|to|by|in|on|onto|at|with|from|of|over|under|after|before|as|than)$/iu.test(tokens.at(-1))) tokens.pop();
  return tokens.join(' ').replace(/[,;:—–-]+$/u, '').trim() || original.split(/\s+/u).slice(0, 1).join('');
}

/** Build a fact-safe greeting from headlines only; does not invent a summary. */
export function buildGreeting(stories = []) {
  const headlines = (Array.isArray(stories) ? stories : []).map(story => shortHeadline(story?.headline ?? story?.title ?? story?.heading ?? story)).filter(Boolean).slice(0, 3);
  if (!headlines.length) return '';
  const thin = headlines.length < 3;
  const intro = headlines.length === 1 ? 'One thing today:' : headlines.length === 2 ? 'Two things today:' : 'Three things today:';
  const joined = headlines.length === 1 ? headlines[0]
    : headlines.length === 2 ? `${headlines[0]} and ${headlines[1]}`
      : `${headlines[0]}, ${headlines[1]}, and ${headlines[2]}`;
  const result = `${intro} ${joined}`;
  const resultWords = result.match(/[^\s]+/gu) || [];
  const bounded = resultWords.length <= 35 ? result : resultWords.slice(0, 35).join(' ');
  return /[.!?]$/u.test(bounded) ? bounded : `${bounded}.`;
}

function storyText(story) {
  const model = story?.headline !== undefined && story?.lede !== undefined ? story : toStoryModel(story);
  return [model.headline, model.lede, model.so_what, ...(model.more || []), model.watch].filter(Boolean).join(' ');
}

/** Honest text estimate at 200 wpm. Empty copy is zero; non-empty copy rounds up. */
export function estimateReadTime(value) {
  const text = Array.isArray(value) ? value.map(storyText).join(' ') : value && typeof value === 'object' ? storyText(value) : String(value || '');
  const wordCount = words(text).length;
  const minutes = wordCount ? Math.ceil(wordCount / 200) : 0;
  return { words: wordCount, minutes, label: wordCount ? `${minutes} min` : '0 min' };
}

/** Audio duration is metadata, not a text-speed estimate. Unknown stays unknown. */
export function listenTime(audio, available = false) {
  const seconds = Number(audio?.duration_seconds);
  const minutes = available && Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds / 60) : null;
  return { available: Boolean(available), minutes, label: minutes === null ? '' : `${minutes} min` };
}

/** Retain only quality-filtered extras when at least two are available. */
export function alsoToday(items = []) {
  return Array.isArray(items) && items.length >= 2 ? items : [];
}

// Named aliases keep call sites readable while preserving a small stable API.
export const mapStory = toStoryModel;
export const greetingSentence = buildGreeting;
export const readTime = estimateReadTime;
export const alsoTodayItems = alsoToday;

// Exported for focused schema tests and consumers that need to enforce constraints.
export const storyModelInternals = Object.freeze({ sentenceCount, firstSentence });
