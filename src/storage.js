const KEY = 'briefing:v1:state';
const TOPIC_IDS = ['payments', 'banking', 'digital', 'insurance', 'sports'];
const DURATIONS = [10, 12, 15];

export const defaultState = {
  selectedIds: [...TOPIC_IDS],
  priorityIds: [],
  durationMinutes: 12,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York',
  savedIds: [],
  savedRevisions: {},
  finishedIds: [],
  progress: {},
  activeAudio: null,
  readingProgress: {},
  feedback: {},
};

function validTimezone(value) {
  if (typeof value !== 'string' || value.length > 60) return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone: value }); return true; }
  catch { return false; }
}

function sanitize(raw) {
  const fallback = structuredClone(defaultState);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fallback;
  const selectedIds = Array.isArray(raw.selectedIds)
    ? [...new Set(raw.selectedIds.filter(id => TOPIC_IDS.includes(id)))].slice(0, 6)
    : fallback.selectedIds;
  const selected = selectedIds.length ? selectedIds : fallback.selectedIds;
  const priorityIds = Array.isArray(raw.priorityIds)
    ? [...new Set(raw.priorityIds.filter(id => selected.includes(id)))].slice(0, 2)
    : [];
  const savedIds = Array.isArray(raw.savedIds)
    ? [...new Set(raw.savedIds.filter(id => typeof id === 'string' && id.length < 100))].slice(0, 100)
    : [];
  const savedRevisions = {};
  if (raw.savedRevisions && typeof raw.savedRevisions === 'object' && !Array.isArray(raw.savedRevisions)) {
    for (const [id, revisionId] of Object.entries(raw.savedRevisions).slice(0, 100)) {
      if (savedIds.includes(id) && id.length <= 100 && typeof revisionId === 'string' && revisionId.length <= 100) savedRevisions[id] = revisionId;
    }
  }
  const finishedIds = Array.isArray(raw.finishedIds)
    ? [...new Set(raw.finishedIds.filter(id => typeof id === 'string' && id.length < 100))].slice(0, 100)
    : [];
  const progress = {};
  if (raw.progress && typeof raw.progress === 'object' && !Array.isArray(raw.progress)) {
    for (const [id, value] of Object.entries(raw.progress).slice(0, 100)) {
      if (id.length > 100 || !value || typeof value !== 'object') continue;
      const seconds = Number(value.seconds);
      if (!Number.isFinite(seconds) || seconds < 0 || seconds > 86400) continue;
      progress[id] = { seconds, revisionId: String(value.revisionId || '').slice(0, 100), ...(typeof value.chapterId === 'string' && value.chapterId.length <= 100 ? { chapterId: value.chapterId } : {}) };
    }
  }
  const feedback = {};
  if (raw.feedback && typeof raw.feedback === 'object' && !Array.isArray(raw.feedback)) {
    for (const [id, value] of Object.entries(raw.feedback).slice(0, 100)) {
      if (id.length <= 100 && ['useful', 'knew', 'unclear'].includes(value)) feedback[id] = value;
    }
  }
  const readingProgress = {};
  if (raw.readingProgress && typeof raw.readingProgress === 'object' && !Array.isArray(raw.readingProgress)) {
    for (const [id, value] of Object.entries(raw.readingProgress).slice(0, 100)) {
      if (id.length > 100 || !value || typeof value !== 'object') continue;
      const storyId = typeof value.storyId === 'string' ? value.storyId : value.sectionId;
      const paragraphIndex = Number(value.paragraphIndex ?? 0);
      if (typeof storyId !== 'string' || storyId.length > 100 || !Number.isInteger(paragraphIndex) || paragraphIndex < 0 || paragraphIndex > 10000) continue;
      readingProgress[id] = {
        storyId,
        paragraphIndex,
        mode: value.mode === 'listen' ? 'listen' : 'read',
        updatedAt: typeof value.updatedAt === 'string' && value.updatedAt.length <= 40 ? value.updatedAt : new Date(0).toISOString(),
        revisionId: String(value.revisionId || '').slice(0, 100),
      };
    }
  }
  const activeAudio = raw.activeAudio && typeof raw.activeAudio === 'object'
    && typeof raw.activeAudio.id === 'string' && raw.activeAudio.id.length < 100
    && typeof raw.activeAudio.revisionId === 'string' && raw.activeAudio.revisionId.length < 100
    ? { id: raw.activeAudio.id, revisionId: raw.activeAudio.revisionId } : null;
  return {
    selectedIds: selected,
    priorityIds,
    durationMinutes: DURATIONS.includes(raw.durationMinutes) ? raw.durationMinutes : fallback.durationMinutes,
    timezone: validTimezone(raw.timezone) ? raw.timezone : fallback.timezone,
    savedIds,
    savedRevisions,
    finishedIds,
    progress,
    activeAudio,
    readingProgress,
    feedback,
  };
}

export function loadState() {
  try { return sanitize(JSON.parse(localStorage.getItem(KEY))); }
  catch { return structuredClone(defaultState); }
}

export function saveState(state) {
  const safe = sanitize(state);
  try { localStorage.setItem(KEY, JSON.stringify(safe)); }
  catch { /* The interface still works for this visit when browser storage is blocked. */ }
  return safe;
}

export function invalidateAudioProgress(state, id) {
  const safe = sanitize(state);
  delete safe.progress[id];
  if (safe.activeAudio?.id === id) safe.activeAudio = null;
  return saveState(safe);
}

export function resetState() {
  try { localStorage.removeItem(KEY); } catch { /* no persistent state to clear */ }
  return structuredClone(defaultState);
}
