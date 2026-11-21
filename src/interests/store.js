import { validateProfile } from './interpret.js';

const PROFILE_KEY = 'briefing:v1:interest-profile';
let serverSave = Promise.resolve();

export function getProfile() {
  try {
    const raw = JSON.parse(localStorage.getItem(PROFILE_KEY));
    return raw ? validateProfile(raw) : null;
  } catch {
    return null;
  }
}

export function saveProfile(profile, options = {}) {
  if (profile && typeof profile === 'object' && !Object.hasOwn(profile, 'behavior_weights')) {
    const prior = getProfile();
    if (prior?.behavior_weights) {
      const topicIds = new Set(Array.isArray(profile.topics) ? profile.topics.map(topic => topic?.id) : []);
      const retained = Object.fromEntries(Object.entries(prior.behavior_weights)
        .filter(([topicId]) => topicIds.has(topicId)));
      profile = { ...profile, behavior_weights: retained };
    }
  }
  const valid = validateProfile(profile);
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(valid)); }
  catch { /* The profile remains usable in memory for this visit. */ }
  // Keep the saved profile current for scheduled editions.
  // Queue writes so a slower earlier request cannot replace a newer edit.
  if (options.server !== false) {
    void syncProfile(valid).catch(() => {});
  }
  return valid;
}

export function clearProfile() {
  try { localStorage.removeItem(PROFILE_KEY); }
  catch { /* Storage may be disabled; the caller can still begin onboarding. */ }
}

// Explicit updates await the same queue as background saves before starting a build.
export function syncProfile(profile) {
  const valid = validateProfile(profile);
  serverSave = serverSave.catch(() => {}).then(async () => {
    const response = await fetch('/api/profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: valid }), keepalive: true,
    });
    if (!response.ok) throw new Error('profile_save_failed');
  });
  return serverSave;
}
