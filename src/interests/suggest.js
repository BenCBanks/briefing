import { events as signalEvents, countBy } from './signals.js';

const SNOOZE_KEY = 'briefing:v1:interest-snoozes';

function readSnoozes() {
  try { return JSON.parse(localStorage.getItem(SNOOZE_KEY)) || {}; } catch { return {}; }
}

export function snoozeSuggestion(id, until = Date.now() + 30 * 86400000) {
  const snoozes = readSnoozes(); snoozes[id] = until;
  try { localStorage.setItem(SNOOZE_KEY, JSON.stringify(snoozes)); } catch { /* This view can still hide a dismissed suggestion. */ }
}

export function clearSuggestionSnoozes() {
  try { localStorage.removeItem(SNOOZE_KEY); } catch { /* Best-effort local clear. */ }
}

export const SUGGESTION_THRESHOLDS = Object.freeze({
  cadenceAfterSkips: 4,
  removeAfterSkips: 6,
  lowerDepthAfterSkips: 3,
  raiseDepthAfterDeepReads: 3,
  addAfterSourceOpens: 3,
  recentDays: 45,
});

const depthOrder = ['headline', 'standard', 'deeper'];
const occurredWithin = (event, days, now) => event.ts >= now - days * 86400000;

export function generateSuggestions({ profile, catalog = [], snoozes = readSnoozes(), now = Date.now() }) {
  // Read signal events and aggregate counts through Player's shared local signal API.
  const recent = signalEvents().filter(event => event.ts <= now && occurredWithin(event, SUGGESTION_THRESHOLDS.recentDays, now));
  const count = (topicId, type) => countBy(topicId, type, SUGGESTION_THRESHOLDS.recentDays, now);
  const suggestions = [];
  for (const topic of profile.topics) {
    if (profile.muted?.some(item => item.label === topic.label)) continue;
    const topicEvents = recent.filter(event => event.topicId === topic.id);
    const explicit = topicEvents.filter(event => ['more', 'less', 'mute'].includes(event.type)).at(-1);
    if (explicit?.type === 'mute') continue;
    const skips = count(topic.id, 'skip');
    const deepReads = ['explainOpen', 'replay', 'finish'].reduce((total, type) => total + count(topic.id, type), 0);
    const add = (kind, next, text, reason, evidence) => suggestions.push({
      id: `${kind}:${topic.id}:${next}`, kind, topicId: topic.id, text, reason, evidence,
    });
    if (explicit?.type === 'more' && topic.depth !== 'deeper') {
      add('depth', depthOrder[Math.min(2, depthOrder.indexOf(topic.depth) + 1)], `Show more depth on ${topic.label}`, 'You asked for more on this topic.', 1);
    } else if (explicit?.type === 'less' && topic.depth !== 'headline') {
      add('depth', depthOrder[Math.max(0, depthOrder.indexOf(topic.depth) - 1)], `Keep ${topic.label} briefer`, 'You asked for less on this topic.', 1);
    } else if (skips >= SUGGESTION_THRESHOLDS.removeAfterSkips && !topic.mustFollow) {
      add('remove', 'remove', `Pause ${topic.label}`, `You skipped the last ${skips} ${topic.label} stories.`, skips);
    } else if (skips >= SUGGESTION_THRESHOLDS.cadenceAfterSkips && topic.cadence !== 'weekly') {
      add('cadence', 'weekly', `Move ${topic.label} to weekly`, `You skipped ${skips} recent stories on this topic.`, skips);
    } else if (skips >= SUGGESTION_THRESHOLDS.lowerDepthAfterSkips && topic.depth !== 'headline') {
      add('depth', depthOrder[Math.max(0, depthOrder.indexOf(topic.depth) - 1)], `Make ${topic.label} shorter`, `You skipped ${skips} recent stories on this topic.`, skips);
    } else if (deepReads >= SUGGESTION_THRESHOLDS.raiseDepthAfterDeepReads && topic.depth !== 'deeper') {
      add('depth', depthOrder[Math.min(2, depthOrder.indexOf(topic.depth) + 1)], `Add more depth on ${topic.label}`, `You opened or finished ${deepReads} explanations or replays.`, deepReads);
    }
  }
  for (const topic of catalog.filter(item => !profile.topics.some(active => active.id === item.id) && !profile.muted.some(muted => muted.label === item.label))) {
    const opens = count(topic.id, 'sourceOpen');
    if (opens >= SUGGESTION_THRESHOLDS.addAfterSourceOpens) suggestions.push({
      id: `add:${topic.id}`, kind: 'add', topicId: topic.id, text: `Add ${topic.label}`,
      reason: `You opened ${opens} sources about ${topic.label}.`, evidence: opens,
    });
  }
  return suggestions.filter(suggestion => !snoozes[suggestion.id] || snoozes[suggestion.id] <= now)
    .sort((a, b) => b.evidence - a.evidence || a.topicId.localeCompare(b.topicId));
}

export function applySuggestion(profile, suggestion, catalog = []) {
  const topic = profile.topics.find(item => item.id === suggestion.topicId);
  if (suggestion.kind === 'add' && !topic) {
    const source = catalog.find(item => item.id === suggestion.topicId);
    if (!source || profile.muted.some(item => item.label === source.label)) return profile;
    return { ...profile, topics: [...profile.topics, { id: source.id, label: source.label, scope: source.scope,
      include: [], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: source.label }] };
  }
  if (!topic) return profile;
  if (suggestion.kind === 'remove') return { ...profile, topics: profile.topics.filter(item => item.id !== suggestion.topicId), muted: [...profile.muted, { label: topic.label }] };
  const value = suggestion.id.split(':').slice(2).join(':');
  return { ...profile, topics: profile.topics.map(item => item.id !== suggestion.topicId ? item :
    suggestion.kind === 'cadence' ? { ...item, cadence: value } : { ...item, depth: value }) };
}
