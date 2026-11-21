const STORAGE_KEY = 'briefing:v1:signals';
const LIMIT = 1000;
const BEHAVIOR_DAYS = 30;
const TYPES = new Set(['finish', 'skip', 'save', 'sourceOpen', 'explainOpen', 'replay', 'more', 'less', 'mute']);
const BEHAVIOR = { finish: 0.8, replay: 0.5, save: 1.2, more: 1.2, skip: -0.8, less: -1.2 };

function validEvent(value) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Number.isFinite(value.ts) && TYPES.has(value.type) &&
    typeof value.storyId === 'string' && value.storyId.length > 0 && value.storyId.length <= 100 &&
    typeof value.topicId === 'string' && value.topicId.length > 0 && value.topicId.length <= 100;
}

function read() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.filter(validEvent).slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

export function record(type, storyId, topicId, ts = Date.now()) {
  const event = { ts, type, storyId, topicId };
  if (!validEvent(event)) return false;
  const all = [...read(), event].slice(-LIMIT);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(all)); } catch { /* Keep signals local and optional when storage is blocked. */ }
  return true;
}

export function events() {
  return read().map(event => ({ ...event }));
}

export function countBy(topicId, type, sinceDays, now = Date.now()) {
  if (typeof topicId !== 'string' || !TYPES.has(type) || !Number.isFinite(sinceDays) || sinceDays < 0) return 0;
  const cutoff = now - sinceDays * 86400000;
  return read().filter(event => event.topicId === topicId && event.type === type && event.ts >= cutoff && event.ts <= now).length;
}

// Only this bounded aggregate is synced with the private profile. Raw events
// remain in browser storage and are never included in a profile request.
export function behaviorWeights(topicIds, now = Date.now()) {
  if (!Array.isArray(topicIds) || !Number.isFinite(now)) return {};
  const cutoff = now - BEHAVIOR_DAYS * 86400000;
  const scores = new Map(topicIds.filter(id => typeof id === 'string').map(id => [id, 0]));
  for (const event of read()) {
    const contribution = BEHAVIOR[event.type];
    if (contribution === undefined || event.ts < cutoff || event.ts > now || !scores.has(event.topicId)) continue;
    const ageDays = (now - event.ts) / 86400000;
    scores.set(event.topicId, scores.get(event.topicId) + contribution * (0.5 ** (ageDays / 14)));
  }
  return Object.fromEntries([...scores].map(([topicId, score]) => [
    topicId, Math.round(Math.min(1.5, Math.max(0.5, 1 + score * 0.08)) * 100) / 100,
  ]));
}

export function clear() {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* Clear is best-effort in restricted browser storage. */ }
}
