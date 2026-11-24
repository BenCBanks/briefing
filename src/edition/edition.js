import { splitSentences, estimateReadTime, listenTime } from './story-model.js';
import { audioScriptParagraphs, audioScriptText, withoutCitationNotes } from '../audio-script.js';

const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const shortDate = value => { const day = String(value || '').slice(0, 10); const d = new Date(`${day}T12:00:00Z`); return Number.isNaN(d.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(d); };
const longWeekdayDate = value => { const day = String(value || '').slice(0, 10); const d = new Date(`${day}T12:00:00Z`); return Number.isNaN(d.getTime()) ? '' : new Intl.DateTimeFormat('en-US', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(d); };
const addUtcDays = (value, days) => { const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`); if (Number.isNaN(date.getTime())) return ''; date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10); };
const todayDate = value => { const d = new Date(`${value}T12:00:00Z`); return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(d); };
const date = value => { const d = new Date(`${value}T12:00:00Z`); return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(d); };
const labels = { covered: 'Covered', update: 'Covered', context: 'Context', quiet: 'Covered', limited: 'Coverage limited' };
const MORE_SECTIONS_KEY = 'briefing:story-more-open';
const openTodayTranscripts = new Set();
function moreSectionKey(edition, section) {
  return `${edition?.id || ''}:${edition?.revision_id || ''}:${section?.id || ''}`;
}
function readMoreSectionPreferences() {
  try {
    const value = JSON.parse(localStorage.getItem(MORE_SECTIONS_KEY) || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value)
      .filter(([key, open]) => typeof key === 'string' && key.length <= 320 && typeof open === 'boolean')
      .slice(-100));
  } catch { return {}; }
}
function moreSectionIsOpen(edition, section, defaultOpen) {
  const preferences = readMoreSectionPreferences();
  const key = moreSectionKey(edition, section);
  return Object.hasOwn(preferences, key) ? preferences[key] : defaultOpen;
}
function saveMoreSectionPreference(edition, section, open) {
  try {
    const preferences = readMoreSectionPreferences();
    const key = moreSectionKey(edition, section);
    delete preferences[key];
    preferences[key] = Boolean(open);
    localStorage.setItem(MORE_SECTIONS_KEY, JSON.stringify(Object.fromEntries(Object.entries(preferences).slice(-100))));
  } catch { /* The disclosure remains usable for this visit when storage is unavailable. */ }
}
export function editionDateRange(edition) {
  if (!edition?.week_start) return '';
  const start = new Date(`${String(edition.week_start).slice(0, 10)}T12:00:00Z`);
  const end = edition.week_end ? new Date(`${String(edition.week_end).slice(0, 10)}T12:00:00Z`) : new Date(start.getTime() + 6 * 86400000);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return '';
  const startParts = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).formatToParts(start);
  const endParts = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).formatToParts(end);
  const part = (parts, type) => parts.find(item => item.type === type)?.value || '';
  const startDay = part(startParts, 'day'); const startMonth = part(startParts, 'month'); const startYear = part(startParts, 'year');
  const endDay = part(endParts, 'day'); const endMonth = part(endParts, 'month'); const endYear = part(endParts, 'year');
  if (startYear === endYear && startMonth === endMonth) return `${startDay}–${endDay} ${endMonth}`;
  return `${startDay} ${startMonth}${startYear === endYear ? '' : ` ${startYear}`}–${endDay} ${endMonth} ${endYear}`;
}
const wordCount = text => String(text || '').trim().split(/\s+/).filter(Boolean).length;
function plainSnippetText(value) {
  return String(value || '')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, ' ')
    .replace(/<\/?[a-z][^>]*>/gi, ' ')
    .replace(/&(?:amp|lt|gt|quot|apos|nbsp);|&#(?:x[\da-f]+|\d+);/gi, entity => {
      const named = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&nbsp;': ' ' };
      if (named[entity.toLowerCase()] !== undefined) return named[entity.toLowerCase()];
      const numeric = entity.match(/^&#(x[\da-f]+|\d+);$/i)?.[1];
      const code = numeric ? (numeric[0].toLowerCase() === 'x' ? Number.parseInt(numeric.slice(1), 16) : Number.parseInt(numeric, 10)) : NaN;
      try { return Number.isInteger(code) && code >= 0 && code <= 0x10ffff ? String.fromCodePoint(code) : ' '; }
      catch { return ' '; }
    })
    .replace(/\s+([.,!?;:])/g, '$1')
    .replace(/[\t\f\v ]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .trim();
}

function humanTopicLabel(topicId) {
  const words = String(topicId || '').replace(/^custom-/i, '').replace(/[-_]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 'Topic';
  const acronyms = { ai: 'AI', us: 'U.S.', cms: 'CMS', nba: 'NBA', nfl: 'NFL', fda: 'FDA', fed: 'Fed' };
  const small = new Set(['a', 'an', 'and', 'as', 'at', 'for', 'in', 'of', 'on', 'or', 'the', 'to']);
  const label = words.map((word, index) => {
    const lower = word.toLocaleLowerCase();
    if (acronyms[lower]) return acronyms[lower];
    return index > 0 && small.has(lower) ? lower : `${lower[0].toLocaleUpperCase()}${lower.slice(1)}`;
  }).join(' ');
  return label || 'Topic';
}

function topicLabel(row, topicById) {
  const id = String(row?.topic_id || '');
  const value = String(topicById.get(id)?.label || row?.label || '').trim();
  return value && value.toLocaleLowerCase() !== id.toLocaleLowerCase() ? value : humanTopicLabel(id);
}

const playIcon = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5Z"/></svg>';
const pauseIcon = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4.5" height="16" rx="1.2"/><rect x="13.5" y="4" width="4.5" height="16" rx="1.2"/></svg>';
const moreIcon = '<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m7 10 5 5 5-5"/></svg>';
const lessIcon = '<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m7 14 5-5 5 5"/></svg>';
export function rowPreview(body, maxLength = 90, headline = '') {
  const cleaned = plainSnippetText(body).split(/\n\s*\n/).filter(paragraph => !/^\s*(?:Limits|Citations)\s*:/i.test(paragraph)).join('\n\n')
    .replace(/(^|\n\s*)(?:What happened|Why it matters|Things to consider)\s*:\s*/gim, '$1')
    .replace(/\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const sentences = splitSentences(cleaned);
  const titleWords = String(headline).toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  const addsInformation = sentence => {
    if (!titleWords.length) return true;
    const sentenceWords = sentence.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
    const compareCount = Math.min(titleWords.length, sentenceWords.length);
    if (!compareCount) return true;
    let sharedPrefix = 0;
    while (sharedPrefix < compareCount && titleWords[sharedPrefix] === sentenceWords[sharedPrefix]) sharedPrefix += 1;
    return sharedPrefix / compareCount < 0.72;
  };
  const sentence = sentences.map(value => value.trim()).find(addsInformation) || '';
  if (sentence.length <= maxLength) return sentence;
  const limit = Math.max(1, maxLength - 1);
  const prefix = sentence.slice(0, limit + 1);
  const boundary = prefix.lastIndexOf(' ');
  const excerpt = sentence.slice(0, boundary > 0 ? boundary : limit).trimEnd();
  return `${excerpt}…`;
}

export function hasMaterialCorrection(edition, sectionId = null) {
  const sectionIds = new Set((edition?.sections || []).map(section => section.id));
  return (edition?.corrections || []).some(correction =>
    correction?.severity === 'material' &&
    correction.revision_id === edition.revision_id &&
    typeof correction.previous_revision_id === 'string' && correction.previous_revision_id !== edition.revision_id &&
    Number.isFinite(Date.parse(correction.corrected_at || '')) &&
    Array.isArray(correction.section_ids) && correction.section_ids.some(id => sectionIds.has(id) && (!sectionId || sectionId === id)) &&
    Boolean(correction.what_changed && correction.why_it_matters));
}

function safeExternalURL(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' ? url.href : '';
  } catch { return ''; }
}

function inactiveWeeklyItem(item) {
  return item?.withdrawn === true || item?.superseded === true ||
    ['withdrawn', 'superseded'].includes(String(item?.status || '').toLocaleLowerCase());
}

function textList(value) {
  const values = Array.isArray(value) ? value : [value];
  return values.filter(item => typeof item === 'string' && item.trim()).map(item => item.trim());
}

export function renderWeeklyRecap(container, edition, sources = [], actions = {}) {
  const allSections = visibleSections(edition).filter(section => section.kind !== 'link');
  const inactiveSectionIds = new Set(allSections.filter(inactiveWeeklyItem).map(section => section.id));
  const sections = allSections.filter(section => !inactiveWeeklyItem(section));
  const sourceById = new Map(sources.filter(source => !inactiveWeeklyItem(source)).map(source => [source.id, source]));
  const lead = textList(edition.lead_paragraphs ?? edition.lead ?? edition.summary);
  const developmentData = Array.isArray(edition.key_developments) ? edition.key_developments
    : Array.isArray(edition.keyDevelopments) ? edition.keyDevelopments : sections;
  const developments = developmentData.map(item => {
    if (typeof item === 'string') {
      const section = allSections.find(candidate => candidate.heading === item);
      if (!section || inactiveWeeklyItem(section)) return null;
      const original = actions.originalStoryFor?.(section) || {};
      return { title: item, sectionId: section?.id || '', originalEditionId: original.editionId || '', originalSectionId: original.sectionId || '' };
    }
    if (!item || typeof item !== 'object') return null;
    if (inactiveWeeklyItem(item)) return null;
    const sectionId = item.section_id || item.sectionId || (allSections.some(section => section.id === item.id) ? item.id : '');
    const originalSectionId = item.original_section_id || item.originalSectionId || '';
    if (inactiveSectionIds.has(sectionId) || inactiveSectionIds.has(originalSectionId)) return null;
    const sourceSection = sections.find(section => section.id === sectionId) || item;
    const original = actions.originalStoryFor?.(sourceSection) || {};
    return {
      title: item.title || item.heading || item.label || item.text || '',
      sectionId,
      originalEditionId: item.original_edition_id || item.originalEditionId || original.editionId || '',
      originalSectionId: originalSectionId || original.sectionId || '',
    };
  }).filter(item => item?.title);
  const developmentRows = developments.map(item => `<li>${item.originalEditionId && item.originalSectionId
    ? `<button type="button" data-weekly-original-edition="${esc(item.originalEditionId)}" data-weekly-original-section="${esc(item.originalSectionId)}">${esc(item.title)} <span aria-hidden="true">→</span></button>`
    : `<span>${esc(item.title)}</span>`}</li>`).join('');
  const sourceIds = [...new Set(sections.flatMap(section => section.source_ids || []))]
    .filter(id => sourceById.has(id));
  const sourceRows = sourceIds.map((id, index) => {
    const source = sourceById.get(id);
    if (!source) return '';
    const outlet = source.publisher || source.outlet || source.name || source.organization || source.agency
      || String(source.kind || '').replace(/\s+(?:consumer guide|technical guidance|consumer resource|rules reference|reference)$/i, '') || 'Source';
    const published = shortDate(source.published_at || source.date || source.updated_at);
    return `<li><button type="button" data-weekly-source="${esc(id)}"><span>${index + 1}.</span><span><strong>${esc(outlet)}</strong><small>${esc(source.title || 'Source details')}${published ? ` · ${published}` : ''}</small></span></button></li>`;
  }).filter(Boolean).join('');
  const correction = hasMaterialCorrection(edition) ? edition.corrections.find(item => item.severity === 'material' && item.revision_id === edition.revision_id) : null;
  const correctedAt = correction?.corrected_at ? new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' }).format(new Date(correction.corrected_at)) : '';
  const correctedSection = sections.find(section => correction?.section_ids?.includes(section.id));
  const changedValue = correction?.what_changed?.match(/\b\d[\d,.%]*/g)?.at(-1);
  const currentWording = correction?.current_text || (changedValue && splitSentences(correctedSection?.body || '').find(sentence => sentence.includes(changedValue))) || splitSentences(correctedSection?.body || '')[0] || '';
  const listenAvailable = actions.listeningAvailable !== false;
  const listenEstimate = actions.listenEstimate?.(edition) || `about ${Math.max(1, Math.round(wordCount(sections.map(section => `${section.heading} ${section.body || ''}`).join(' ')) / 155))} min`;
  const firstSection = sections[0];
  const recapEnd = edition.week_end || addUtcDays(edition.week_start, 6);
  const nextRecapDate = edition.next_recap_at || edition.next_recap_available_at || addUtcDays(recapEnd, 7);
  const nextRecapNote = edition.next_recap_note || (longWeekdayDate(nextRecapDate) ? `Next recap expected ${longWeekdayDate(nextRecapDate)}.` : 'A new recap is expected next week.');
  const thinWeek = edition.thin_week === true || edition.thinWeek === true;
  container.innerHTML = `<article class="edition weekly-recap-page" data-edition="${esc(edition.id)}" data-revision="${esc(edition.revision_id)}">
    <header class="weekly-recap-header"><p class="story-context-line">Weekly recap · ${esc(editionDateRange(edition))}</p><h1>${esc(edition.title || 'Weekly recap')}</h1></header>
    ${thinWeek ? `<p class="weekly-thin-week" role="status">${esc(edition.thin_week_note || 'A quieter week for the topics you follow. These are the developments covered.')}</p>` : ''}
    ${correction ? `<aside class="correction-notice" aria-label="Material correction"><strong>Corrected · ${esc(correctedAt)}</strong><p>Earlier wording: “${esc(correction.what_changed)}”</p><p>Current wording: “${esc(currentWording)}”</p><p>${esc(correction.why_it_matters)} Audio for the whole edition is paused until it is re-recorded.</p><a class="correction-history-link" href="#corrections">See all corrections</a></aside>` : ''}
    ${lead.map(text => `<p class="weekly-recap-lead">${esc(text)}</p>`).join('')}
    ${firstSection && (listenAvailable || correction) ? `<button type="button" class="story-listen weekly-listen${correction ? ' is-disabled' : ''}" data-listen-control="story" data-edition-id="${esc(edition.id)}" data-revision-id="${esc(edition.revision_id)}" data-section-id="${esc(firstSection.id)}" data-listen-estimate="${esc(listenEstimate)}" aria-label="${correction ? 'Audio paused. One story was corrected. The text is updated.' : `Listen to the weekly recap, ${listenEstimate.replace(/ min$/, ' minutes')}`}"${correction ? ' disabled' : ''}><span class="story-listen-icon">${correction ? pauseIcon : playIcon}</span><span class="story-listen-label">${correction ? 'Audio paused' : 'Listen'}</span><small>${correction ? 'One story was corrected. The text is updated.' : `· ${esc(listenEstimate)}`}</small></button>` : ''}
    ${developmentRows ? `<section class="weekly-key-developments"><h2>Key developments</h2><ol>${developmentRows}</ol></section>` : ''}
    ${sourceRows ? `<section class="story-sources weekly-recap-sources"><h2>Citations</h2><ol>${sourceRows}</ol></section>` : ''}
    <p class="weekly-next-recap">${esc(nextRecapNote)}</p>
    <footer class="story-next weekly-recap-actions"><button type="button" class="save-story" data-save-edition>${actions.isSaved?.(edition.id) ? 'Saved' : 'Save'}</button></footer>
  </article>`;
  container.querySelectorAll('[data-weekly-original-edition]').forEach(button => button.addEventListener('click', () => actions.openOriginalStory?.(button.dataset.weeklyOriginalEdition, button.dataset.weeklyOriginalSection, button)));
  container.querySelectorAll('[data-weekly-source]').forEach(button => button.addEventListener('click', () => actions.openSource?.(button.dataset.weeklySource)));
  container.querySelector('.story-listen')?.addEventListener('click', () => actions.listen?.(edition));
  container.querySelector('[data-save-edition]')?.addEventListener('click', () => actions.save?.(edition.id, edition.revision_id));
}

export const visibleSections = item => {
  const quiet = new Set((item.coverage || []).filter(row => ['quiet', 'limited'].includes(row.state)).map(row => row.topic_id));
  const visible = (item.sections || []).filter(section => !(section.topic_ids || []).length || section.topic_ids.some(id => !quiet.has(id)));
  return visible.length ? visible : (item.sections || []);
};

// One display adapter for both generated v3 copy and legacy labelled stories.
function storyCopy(section) {
  const plain = value => plainSnippetText(value || '').replace(/\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, '').trim();
  const headline = plain(section.headline || section.heading).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const clean = value => {
    const text = plain(value);
    if (!headline) return text;
    return splitSentences(text).filter((sentence, index) => {
      if (index !== 0) return true;
      const opening = sentence.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
      return opening !== headline && !headline.startsWith(`${opening} `) && !opening.startsWith(`${headline} `);
    }).join(' ').trim();
  };
  const parts = withoutCitationNotes(section.body || '').split(/\n\s*\n/).filter(Boolean);
  const labelled = parts.some(x => /^(What happened|Why it matters|Things to consider):/i.test(x));
  const legacy = (label, index) => labelled ? parts.find(x => x.toLowerCase().startsWith(label.toLowerCase() + ':'))?.replace(/^[^:]+:\s*/, '') : parts[index];
  const blocks = section.section_blocks || {};
  const script = audioScriptParagraphs(section);
  const happened = textList(section.what_happened ?? blocks.what_happened ?? legacy('What happened', 0));
  const explicit = textList(section.lede ?? section.lead_paragraphs ?? section.lead);
  const base = (explicit.length ? explicit : script.length ? script : happened).map(clean).filter(text => text && !/^(?:What happened|Why it matters|Things to consider)[:.]?$/i.test(text) && !/^Source:/i.test(text));
  const all = splitSentences(base.join(' '));
  // Some editions repeat their headline as the first sentence of the body.
  // Drop only a close opening match; later context still belongs in the story.
  const lede = all.slice(0, 2).join(' ');
  const why = textList(section.so_what ?? section.why_it_matters ?? blocks.why_it_matters ?? legacy('Why it matters', 1)).map(clean).join(' ');
  const whySentences = splitSentences(why);
  const more = [...all.slice(2).length ? [all.slice(2).join(' ')] : [],
    ...(explicit.length ? happened.map(clean).filter(x => !base.includes(x)) : []),
    ...textList(section.more).map(clean), ...whySentences.slice(1),
    ...(!labelled && !script.length ? parts.slice(3).map(clean) : [])];
  const watch = textList(section.watch ?? section.things_to_consider ?? blocks.things_to_consider ?? legacy('Things to consider', 2)).map(clean).join(' ');
  const takeawayText = textList(section.read_takeaway ?? section.takeaway).map(clean).join(' ');
  const takeaway = splitSentences(takeawayText)[0] || '';
  return { lede, so: whySentences[0] || '', more: [...new Set(more)].filter(x => x && x !== lede), watch, takeaway };
}

// Only render the server-approved remainder. Raw quick_hits have not passed the
// topic and quality checks and must never be used as a browser-side fallback.
function quickHitHeadline(value) {
  const text = String(value || '').replace(/\s+/gu, ' ').trim().split(/(?<=[.!?])\s/u)[0];
  const clause = text.split(/:\s*|\s+(?:for|to|by)\s+/iu)[0];
  return clause.split(/\s+/u).filter(Boolean).slice(0, 7).join(' ') || text;
}

export function renderQuickHits(container, approvedItems = []) {
  const items = approvedItems.filter(item => item && (item.title || item.headline)
    && (item.outlet || item.publisher) && safeExternalURL(item.url)).slice(0, 5);
  if (items.length < 3) { container.remove(); return; }
  container.className = 'today-quick-hits';
  container.setAttribute('aria-label', 'Quick hits');
  container.innerHTML = `<h2>Quick hits</h2><ul>${items.map(item => `<li><a href="${esc(safeExternalURL(item.url))}" target="_blank" rel="noopener noreferrer"><span>${esc(item.outlet || item.publisher)}</span><strong>${esc(quickHitHeadline(item.title || item.headline))}</strong></a></li>`).join('')}</ul>`;
}

export function renderToday(container, edition, topics, actions = {}) {
  if (!edition?.sections?.length) { container.innerHTML = '<p>Today’s edition is unavailable.</p>'; return; }
  const topicById = new Map(topics.map(topic => [topic.id, topic]));
  const sections = visibleSections(edition);
  const sourceById = new Map((actions.sources || []).map(source => [source.id, source]));
  const sectionForTopic = new Map();
  sections.forEach((section, index) => (section.topic_ids || []).forEach(id => { if (!sectionForTopic.has(id)) sectionForTopic.set(id, { section, index }); }));
  const coverage = edition.coverage || [];
  let stories;
  if (actions.profile) {
    const eligiblePreferences = [];
    const mutedLabels = new Set((actions.profile.muted || []).map(item => String(item.label || '').toLocaleLowerCase()));
    const coverageById = new Map(coverage.map(row => [row.topic_id, row]));
    for (const preference of actions.profile.topics || []) {
      const topic = topicById.get(preference.id) || topics.find(item => item.label.toLocaleLowerCase() === String(preference.label || '').toLocaleLowerCase());
      const topicId = topic?.id || preference.id;
      const mustFollow = preference.mustFollow === true;
      const muted = mutedLabels.has(String(preference.label || '').toLocaleLowerCase()) || (preference.exclude?.length > 0 && !mustFollow);
      if (muted || (!mustFollow && !(preference.include || []).length)) continue;
      const cadenceQuiet = !mustFollow && (preference.cadence === 'weekly' || (preference.cadence === 'weekends' && actions.todayIsWeekend !== true));
      const row = coverageById.get(topicId);
      const found = sectionForTopic.get(topicId);
      if (!cadenceQuiet && row && row.state !== 'quiet' && row.state !== 'limited' && found) {
        eligiblePreferences.push({ topicId, preference, row });
      }
    }
    stories = sections.flatMap(section => {
      const matched = eligiblePreferences.find(item => (section.topic_ids || []).includes(item.topicId));
      return matched ? [{ ...matched.row, topic_id: matched.topicId, label: matched.preference.label || matched.row.label, section_id: section.id }] : [];
    });
  } else {
    const eligibleCoverage = coverage.filter(row => !['quiet', 'limited'].includes(row.state) && sectionForTopic.has(row.topic_id));
    stories = sections.flatMap(section => {
      const matched = eligibleCoverage.find(row => (section.topic_ids || []).includes(row.topic_id));
      return matched ? [{ ...matched, section_id: section.id }] : [];
    });
  }
  const storySections = stories.map(row => sections.find(section => section.id === row.section_id)).filter(section => section && section.kind !== 'link');
  const audio = listenTime(edition.audio, actions.listeningAvailable === true);
  const rows = storySections.map((section, index) => {
    const followedCount = actions.followedTopicCount ?? actions.profile?.topics?.length ?? topics.length;
    const label = section.kicker_topic || topicLabel(stories.find(x => x.section_id === section.id), topicById);
    const overline = followedCount > 1 ? `${section.kicker === 'Update:' ? 'Update: · ' : ''}${label}` : '';
    const key = `${edition.id}:${edition.revision_id || ''}:${section.id}`;
    return `<li class="today-story-row" id="story-${esc(section.id)}"><header class="v3-story-heading"><p class="today-chapter-kicker"><span class="today-chapter-number">${index + 1}</span>${overline ? ` · ${esc(overline)}` : ''}</p><h2 class="inline-story-heading"><button type="button" class="today-chapter-title" data-play-chapter="${esc(section.id)}" aria-label="Play from chapter ${index + 1}: ${esc(section.headline || section.heading)}">${esc(plainSnippetText(section.headline || section.heading))}</button></h2><button class="story-bookmark" data-bookmark="${esc(section.id)}" aria-label="Save story: ${esc(section.headline || section.heading)}" aria-pressed="${Boolean(actions.isSaved?.(section.id))}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5h12v17l-6-4.5-6 4.5z"/></svg></button></header><details class="today-transcript" data-transcript-story="${esc(section.id)}"${audio.available && !openTodayTranscripts.has(key) ? '' : ' open'}><summary>Transcript and sources</summary><div class="inline-story" data-inline-story="${esc(section.id)}"></div>${actions.openReader ? `<button type="button" class="today-full-story-link${actions.isRead?.(edition.id, section.id, edition.revision_id) ? ' is-read' : ''}" data-read-section="${esc(section.id)}" aria-label="Open story: ${esc(section.headline || section.heading)}">Read as a story</button>` : ''}</details></li>`;
  }).join('');
  const countWords = storySections.map(section => [section.headline || section.heading, ...Object.values(storyCopy(section)).flat(), section.background_block?.text || ''].join(' ')).join(' ');
  const rawAudioSeconds = Number(edition.audio?.duration_seconds);
  const actualAudioMinutes = Number.isFinite(rawAudioSeconds) && rawAudioSeconds > 0 ? Math.max(1, Math.round(rawAudioSeconds / 60)) : null;
  const spokenMinutes = actualAudioMinutes || (countWords.trim() ? Math.max(1, Math.round(estimateReadTime(countWords).words / 150)) : null);
  const displayDuration = actions.deferListenDuration ? '' : actualAudioMinutes ? `${actualAudioMinutes} min` : spokenMinutes ? `${spokenMinutes} min` : audio.label;
  const duration = displayDuration ? ` · about ${displayDuration}` : '';
  const listenAriaDuration = displayDuration ? `, about ${esc(displayDuration)}` : '';
  const storyCount = storySections.length;
  container.innerHTML = `<article class="today-page">
    <p class="today-date">${esc(todayDate(edition.local_date))}</p>
    <h1 class="today-title">Your daily briefing</h1>
    <section class="today-run-summary" aria-label="Daily briefing length"><p class="today-run-meta">${storyCount} ${storyCount === 1 ? 'story' : 'stories'}${spokenMinutes ? ` · about ${spokenMinutes} min` : ''}</p></section>
    <section class="today-primary" aria-label="Briefing player">${audio.available ? `<button class="today-listen-pill primary" data-listen-control="edition" data-edition-id="${esc(edition.id)}" data-revision-id="${esc(edition.revision_id)}" data-listen-estimate="${esc(displayDuration || '')}" data-duration-label="${esc(duration)}" aria-label="Play briefing"><span class="mode-icon">${playIcon}</span><span class="mode-label">Play briefing</span></button>` : `<p class="today-audio-unavailable" role="status">Audio isn’t ready yet. Read the briefing below.</p>`}</section>
    ${rows ? `<section class="today-contents" aria-label="Chapters"><ol>${rows}</ol></section>` : ''}
    <section data-quick-hits-slot></section>
    ${actions.weekly && new Date(edition.local_date + 'T12:00:00Z').getUTCDay() === 0 ? '<a class="today-weekly-link" href="#weekly" data-open-weekly>Weekly recap</a>' : ''}
    <footer class="today-footer">
      <div class="today-completion" data-today-completion data-edition-id="${esc(edition.id)}" data-revision-id="${esc(edition.revision_id || '')}" aria-live="polite"${actions.listeningComplete === true ? '' : ' hidden'}><strong>You're caught up for today.</strong><span>Next briefing tomorrow morning</span></div>
      <div class="today-end-actions"><a class="today-earlier-briefings-link" href="#library">Earlier editions</a><div role="group" aria-label="Was this edition useful?">Useful? <button data-edition-useful="yes" aria-pressed="${actions.getFeedback?.(edition.id) === 'yes'}">Yes</button><button data-edition-useful="no" aria-pressed="${actions.getFeedback?.(edition.id) === 'no'}">No</button></div></div>
    </footer>
    <a class="episode-entry-prompt" href="#ask-episode" data-open-episode><span>Brief me on something else</span><span class="episode-entry-arrow" aria-hidden="true">→</span></a>
    </article>`;
  renderQuickHits(container.querySelector('[data-quick-hits-slot]'), edition.also_today?.items);
  for (const slot of container.querySelectorAll('[data-inline-story]')) renderEdition(slot, edition, topics, { ...actions, sectionId: slot.dataset.inlineStory, inline: true, expanded: !audio.available });
  container.querySelectorAll('.today-transcript').forEach(details => details.addEventListener('toggle', () => {
    const key = `${edition.id}:${edition.revision_id || ''}:${details.dataset.transcriptStory}`;
    if (details.open) openTodayTranscripts.add(key); else openTodayTranscripts.delete(key);
  }));
  container.querySelectorAll('[data-play-chapter]').forEach(button => button.addEventListener('click', event => {
    const row = event.currentTarget.closest('.today-story-row');
    row?.scrollIntoView({ block: 'start', behavior: 'instant' });
    event.currentTarget.focus({ preventScroll: true });
    if (audio.available) actions.listenStory?.(event.currentTarget.dataset.playChapter);
    else { const transcript = row?.querySelector('.today-transcript'); if (transcript) transcript.open = true; transcript?.querySelector('summary')?.focus({ preventScroll: true }); }
  }));
  container.querySelectorAll('[data-read-section]').forEach(button => button.addEventListener('click', () => { actions.rememberMode?.(edition.id, 'read'); actions.openReader?.(button.dataset.readSection); }));
  container.querySelectorAll('[data-bookmark]').forEach(button => button.addEventListener('click', () => { actions.save?.(button.dataset.bookmark, edition.revision_id); button.setAttribute('aria-pressed', String(Boolean(actions.isSaved?.(button.dataset.bookmark)))); }));
  container.querySelectorAll('[data-edition-useful]').forEach(button => button.addEventListener('click', () => { actions.feedback?.(edition.id, button.dataset.editionUseful); container.querySelectorAll('[data-edition-useful]').forEach(choice => choice.setAttribute('aria-pressed', String(choice === button))); }));
  container.querySelector('[data-listen-control]')?.addEventListener('click', () => actions.listenEdition?.(edition));
  container.querySelector('[data-open-weekly]')?.addEventListener('click', event => { event.preventDefault(); actions.openWeekly?.(event.currentTarget); });
  container.querySelector('[data-open-episode]')?.addEventListener('click', event => { event.preventDefault(); actions.openEpisodeRequest?.(event.currentTarget); });
}

export function renderEdition(container, edition, topics, actions = {}) {
  const sections = visibleSections(edition).filter(section => section.kind !== 'link');
  if (!sections.length) { container.innerHTML = '<p>This edition is unavailable.</p>'; return; }
  const inline = actions.inline === true;
  const index = Math.max(0, sections.findIndex(section => section.id === actions.sectionId));
  const section = sections[index]; const weekly = edition.kind === 'weekly';
  const spokenScript = audioScriptText(section);
  const topicId = section.topic_ids?.[0] || section.topic_id;
  const topic = topics.find(item => item.id === topicId) || actions.profile?.topics?.find(item => item.id === topicId)
    || (topicId ? { id: topicId, label: humanTopicLabel(topicId) } : null);
  const dive = (actions.deepDives || []).find(item => item.topic_id === topic?.id);
  const topicQuestion = String(dive?.title || '').trim();
  const hasSourceRegistry = Array.isArray(actions.sources);
  const sourceById = new Map((hasSourceRegistry ? actions.sources : [])
    .filter(source => source && source.status !== 'withdrawn' && source.withdrawn !== true)
    .map(source => [source.id, source]));
  const correction = hasMaterialCorrection(edition, section.id) ? edition.corrections.find(item => item.severity === 'material' && item.revision_id === edition.revision_id && item.section_ids?.includes(section.id)) : null;
  const corrected = hasMaterialCorrection(edition);
  const listeningAvailable = actions.listeningAvailable !== false;
  const listenEstimate = actions.listenEstimate?.(`${section.heading} ${spokenScript || section.body}`) || `about ${Math.max(1, Math.round(wordCount(`${section.heading} ${spokenScript || section.body}`) / 155))} min`;
  const listenState = actions.listenState?.(edition, section) || { label: 'Listen from here' };
  const normalized = text => plainSnippetText(text).replace(/\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, '').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const backgroundBlock = section.background_block;
  const backgroundHtml = backgroundBlock?.text
    ? `<aside class="story-background"><h2>${esc(backgroundBlock.label || 'Background, not from today’s sources')}</h2><p>${esc(backgroundBlock.text)}</p></aside>` : '';
  const whyLine = String(actions.whyLine?.(section) || '').trim();
  const preference = actions.getInterestFeedback?.(section) || null;
  const feedbackControls = !inline && typeof actions.interestFeedback === 'function' ? `<div class="story-interest-feedback" role="group" aria-label="More or less of this story"><button data-interest-feedback="more" aria-pressed="${preference === 'more'}">More like this</button><button data-interest-feedback="less" aria-pressed="${preference === 'less'}">Less like this</button><span class="story-interest-feedback-status" role="status"></span></div>` : '';
  const copy = storyCopy(section);
  const references = hasSourceRegistry
    ? (Array.isArray(section.source_ids) ? section.source_ids.map(id => sourceById.get(id)).filter(Boolean) : [])
    : Array.isArray(section.sources)
      ? section.sources.filter(source => source && source.status !== 'withdrawn' && source.withdrawn !== true)
      : [];
  const primary = references[section.primary_source_index || 0] || references[0];
  const outlet = source => source.publisher || source.outlet || source.name || 'Source';
  const domains = new Set(references.map(source => { try { return new URL(source.url).hostname.replace(/^www\./, ''); } catch { return null; } }).filter(Boolean));
  const quietSource = primary ? `<span>${safeExternalURL(primary.url) ? `<a href="${esc(safeExternalURL(primary.url))}" target="_blank" rel="noopener noreferrer">${esc(outlet(primary))}</a>` : esc(outlet(primary))}${domains.size > 1 ? `, plus ${domains.size - 1} more` : ''}</span>` : '';
  const sourceRows = references.map(source => {
    const published = shortDate(source.published_at || source.date || source.updated_at);
    const label = `${outlet(source)}${published ? ` · ${published}` : ''}`;
    return `<li>${source.id ? `<button data-source="${esc(source.id)}">${esc(label)}</button>` : `<a href="${esc(safeExternalURL(source.url) || '#')}">${esc(label)}</a>`}</li>`;
  }).join('');
  const sourceList = sourceRows ? `<section class="story-sources${inline ? ' inline-story-sources' : ''}" aria-label="Sources"><h2>Sources</h2><ul>${sourceRows}</ul></section>` : '';
  const citations = references.map((source, index) => `<li>${references.length > 1 ? `<span class="citation-number">${index + 1}.</span>` : ''}${source.id ? `<button data-source="${esc(source.id)}">${esc(outlet(source))} · ${esc(source.title || 'Source details')}</button>` : `<a href="${esc(safeExternalURL(source.url) || '#')}">${esc(outlet(source))}</a>`}</li>`).join('');
  const moreId = `more-${section.id}`;
  const open = moreSectionIsOpen(edition, section, !inline || actions.expanded);
  const correctedAt = correction?.corrected_at ? new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' }).format(new Date(correction.corrected_at)) : '';
  const changedValue = correction?.what_changed?.match(/\b\d[\d,.%]*/g)?.at(-1);
  const currentWording = correction?.current_text || (changedValue && splitSentences(section.body || '').find(sentence => sentence.includes(changedValue))) || splitSentences(section.body || '')[0] || '';
  container.innerHTML = `<article class="edition" data-edition="${esc(edition.id)}" data-revision="${esc(edition.revision_id)}">
    ${inline ? '' : `<header class="reader-story-header"><p class="story-context-line">${section.kicker === 'Update:' ? 'Update: · ' : ''}${esc(topic?.label || (weekly ? 'Weekly recap' : 'Today'))} · ${esc(shortDate(weekly ? edition.week_start : edition.local_date))}</p><h1>${esc(section.heading)}</h1></header>`}
    ${!inline && whyLine ? `<p class="story-why-line">${esc(whyLine)}</p>` : ''}
    ${correction ? `<aside class="correction-notice" aria-label="Material correction"><strong>Corrected · ${esc(correctedAt)}</strong><p>Earlier wording: “${esc(correction.what_changed)}”</p><p>Current wording: “${esc(currentWording)}”</p><p>${esc(correction.why_it_matters)} Audio for the whole edition is paused until it is re-recorded.</p><a class="correction-history-link" href="#corrections">See all corrections</a></aside>` : ''}
    ${!inline && (listeningAvailable || corrected) ? `<button type="button" class="story-listen${weekly ? ' weekly-listen' : ''}${corrected ? ' is-disabled' : ''}" data-listen-control="story" data-edition-id="${esc(edition.id)}" data-revision-id="${esc(edition.revision_id)}" data-section-id="${esc(section.id)}" data-listen-estimate="${esc(listenEstimate)}" aria-label="${esc(corrected ? 'Audio paused. One story was corrected. The text is updated.' : `${weekly ? 'Listen' : listenState.label || 'Listen from here'}, ${listenEstimate.replace(/ min$/, ' minutes')}`)}"${corrected ? ' disabled' : ''}><span class="story-listen-icon">${corrected ? pauseIcon : playIcon}</span><span class="story-listen-label">${esc(corrected ? 'Audio paused' : weekly ? 'Listen' : listenState.label || 'Listen from here')}</span><small>${corrected ? 'One story was corrected. The text is updated.' : `· ${esc(listenEstimate)}`}</small></button>` : inline ? '' : '<p class="listening-unavailable">Listening isn’t available in this browser</p>'}
    <section class="edition-section${spokenScript ? ' audio-transcript' : ''}" id="${esc(section.id)}"><div class="story-lead"><p>${esc(copy.lede)}</p></div>${copy.so ? `<p class="story-so-what"><strong>So what.</strong> ${esc(copy.so)}</p>` : ''}${!inline && copy.takeaway ? `<p class="story-takeaway"><strong>Read:</strong> ${esc(copy.takeaway)}</p>` : ''}
    <div class="story-source-line">${inline ? '' : quietSource}<button class="story-more-toggle" aria-expanded="${Boolean(open)}" aria-controls="${esc(moreId)}">${open ? `Less ${lessIcon}` : `More ${moreIcon}`}</button></div>
    <div class="story-more" id="${esc(moreId)}"${open ? '' : ' hidden'}>${copy.more.map(text => `<p>${esc(text)}</p>`).join('')}${copy.watch ? `<p class="story-watch">Worth watching: ${esc(copy.watch)}</p>` : ''}${backgroundHtml}${!inline && citations ? `<section class="story-sources" aria-label="Citations"><ol>${citations}</ol></section>` : ''}</div>${inline ? sourceList : ''}</section>
    ${feedbackControls}
    ${!inline && topicQuestion && actions.hasDeepDive?.(topic?.id) ? `<button class="section-explanation" type="button" data-topic="${esc(topic.id)}" data-section="${esc(section.id)}">Explore: ${esc(topicQuestion)} <span aria-hidden="true">→</span></button>` : ''}
    ${inline ? '' : `<footer class="story-next">${index < sections.length - 1 ? `<button type="button" class="next-story" data-next="${esc(sections[index + 1].id)}"><small>Next story</small><strong>${esc(sections[index + 1].heading)}</strong></button>` : `<div class="up-to-date"><h2>You’re up to date</h2><p>That is all for this ${weekly ? 'recap' : 'edition'}.</p><button type="button" data-finish>${actions.isFinished?.(edition.id, edition.revision_id) ? 'Finished' : 'Mark finished'}</button></div>`}<button type="button" class="save-story" data-save-edition>${actions.isSaved?.(edition.id) ? 'Saved' : 'Save'}</button></footer>`}
  </article>`;
  // Preserve audio positions when compact prose moves a legacy paragraph into More.
  const audioParagraphs = audioScriptParagraphs(section);
  const narration = (audioParagraphs.length ? audioParagraphs : withoutCitationNotes(section.body).split(/\n\s*\n/)).map(normalized);
  for (const paragraph of container.querySelectorAll('.edition-section p')) {
    const words = normalized(paragraph.textContent.replace(/^(?:So what\.|Worth watching:)\s*/i, ''));
    const matched = narration.findIndex(text => text && words && (text.includes(words) || words.includes(text)));
    paragraph.dataset.narrationIndex = String(Math.max(0, matched));
  }
  const moreButton = container.querySelector('.story-more-toggle');
  moreButton?.addEventListener('click', () => {
    const expanded = moreButton.getAttribute('aria-expanded') !== 'true';
    moreButton.setAttribute('aria-expanded', String(expanded));
    moreButton.innerHTML = `${expanded ? `Less ${lessIcon}` : `More ${moreIcon}`}`;
    container.querySelector('.story-more').hidden = !expanded;
    saveMoreSectionPreference(edition, section, expanded);
  });
  container.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => actions.openSource?.(button.dataset.source)));
  container.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => actions.openTopic?.(button.dataset.topic, button.dataset.section)));
  const interestFeedback = container.querySelector('.story-interest-feedback');
  interestFeedback?.querySelectorAll('[data-interest-feedback]').forEach(button => button.addEventListener('click', () => {
    const type = button.dataset.interestFeedback;
    if (button.getAttribute('aria-pressed') === 'true') {
      interestFeedback.querySelector('.story-interest-feedback-status').textContent = 'Already saved';
      return;
    }
    if (actions.interestFeedback?.(type, section) === false) {
      interestFeedback.querySelector('.story-interest-feedback-status').textContent = 'Could not save';
      return;
    }
    interestFeedback.querySelectorAll('[data-interest-feedback]').forEach(choice => choice.setAttribute('aria-pressed', String(type === choice.dataset.interestFeedback)));
    interestFeedback.querySelector('.story-interest-feedback-status').textContent = 'Saved';
    button.focus({ preventScroll: true });
  }));
  container.querySelector('[data-next]')?.addEventListener('click', event => actions.nextStory?.(event.currentTarget.dataset.next));
  container.querySelector('[data-finish]')?.addEventListener('click', event => { actions.finish?.(edition.id, edition.revision_id); event.currentTarget.textContent = 'Finished'; });
  container.querySelector('[data-save-edition]')?.addEventListener('click', event => { actions.save?.(edition.id, edition.revision_id); event.currentTarget.textContent = actions.isSaved?.(edition.id) ? 'Saved' : 'Save'; });
  container.querySelector('[data-listen-control="story"]')?.addEventListener('click', () => {
    const sectionElement = container.querySelector('.edition-section');
    const fromParagraph = actions.visibleParagraphIndex?.(sectionElement) ?? 0;
    actions.listenFromStory?.({ ...edition, start_section_id: section.id }, { fromParagraph });
  });
}
