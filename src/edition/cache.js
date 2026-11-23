// One bounded, profile-scoped snapshot. Audio is always revalidated before use.
const KEY = 'briefing:v2:edition-cache';
const CACHE_VERSION = 2;
const MAX_BYTES = 2_000_000;
const MAX_AGE = 48 * 60 * 60 * 1000;
export function readEditionCache(profileKey) {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw || raw.length > MAX_BYTES) return null;
    const saved = JSON.parse(raw);
    if (saved.version !== CACHE_VERSION || saved.profileKey !== profileKey || !Number.isFinite(saved.savedAt) ||
        Date.now() - saved.savedAt > MAX_AGE || saved.savedAt > Date.now()) return null;
    return saved.pack;
  } catch { return null; }
}
export function writeEditionCache(profileKey, pack) {
  try {
    const raw = JSON.stringify({ version: CACHE_VERSION, profileKey, savedAt: Date.now(), pack });
    if (raw.length <= MAX_BYTES) localStorage.setItem(KEY, raw);
  } catch { /* Private browsing or full storage must not prevent reading. */ }
}
export function clearEditionCache() {
  try { localStorage.removeItem(KEY); } catch { /* Storage may be disabled. */ }
}
