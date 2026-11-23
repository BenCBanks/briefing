import assert from 'node:assert/strict';
import { applySuggestion, generateSuggestions, SUGGESTION_THRESHOLDS } from '../../src/interests/suggest.js';

const signalStore = new Map();
globalThis.localStorage = {
  getItem: key => signalStore.has(key) ? signalStore.get(key) : null,
  setItem: (key, value) => signalStore.set(key, String(value)),
  removeItem: key => signalStore.delete(key),
};
const { record, events, countBy, clear } = await import('../../src/interests/signals.js');
const { getProfile, saveProfile } = await import('../../src/interests/store.js');

const now = Date.UTC(2026, 10, 16);
const payments = { id: 'payments', label: 'Payments', scope: 'Payment systems', include: [], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' };
const profile = { version: 1, words: 'payments', minutes: 12, background: null, topics: [payments], muted: [] };
function seed(items) {
  clear();
  for (const [index, item] of items.entries()) record(item.type, item.storyId || `story-${index}`, item.topicId || 'payments', item.ts ?? now);
}
const signal = (type, topicId = 'payments', storyId = `story-${type}`, age = 0) => ({ ts: now - age, type, storyId, topicId });

seed([signal('more', 'payments', 'story-one')]);
assert.deepEqual(Object.keys(events()[0]).sort(), ['storyId', 'topicId', 'ts', 'type']);
assert.equal(countBy('payments', 'more', 1, now), 1);
assert.equal(record('invalid', 'story-one', 'payments', now), false);
clear();
assert.equal(events().length, 0, 'clear follows Tern\'s local signals API');

const customProfile = { version: 1, words: 'I follow local transit.', minutes: 12, background: null, muted: [], topics: [
  { id: 'custom-local-transit', label: 'Local transit', scope: 'A topic you named that is not in this sample edition yet.', include: ['local transit'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'I follow local transit' },
] };
saveProfile(customProfile);
assert.equal(getProfile().topics[0].id, 'custom-local-transit', 'Reed validated store persists custom topics');

assert.deepEqual(Object.keys(SUGGESTION_THRESHOLDS), ['cadenceAfterSkips', 'removeAfterSkips', 'lowerDepthAfterSkips', 'raiseDepthAfterDeepReads', 'addAfterSourceOpens', 'recentDays']);
seed(Array.from({ length: 3 }, (_, i) => signal('skip', 'payments', `s${i}`)));
const threeSkips = generateSuggestions({ profile, now });
assert.equal(threeSkips.some(item => item.kind === 'cadence' || item.kind === 'remove'), false,
  'cadence and removal wait for their named thresholds');

seed(Array.from({ length: 6 }, (_, i) => signal('skip', 'payments', `skip-${i}`)));
const skipSuggestions = generateSuggestions({ profile, now });
assert.equal(skipSuggestions[0].kind, 'remove');
assert.deepEqual(Object.keys(skipSuggestions[0]).sort(), ['evidence', 'id', 'kind', 'reason', 'text', 'topicId'].sort(), 'suggestions match the documented shape');
assert.match(skipSuggestions[0].reason, /6/);

seed([...Array.from({ length: 6 }, (_, i) => signal('skip', 'payments', `skip-${i}`)), signal('more')]);
const explicitWins = generateSuggestions({ profile, now });
assert.equal(explicitWins.length, 1);
assert.equal(explicitWins[0].kind, 'depth', 'explicit More outranks all implicit skip signals');
const accepted = applySuggestion(profile, explicitWins[0]);
assert.equal(accepted.topics[0].depth, 'deeper', 'only accepting applies the profile change');
assert.equal(profile.topics[0].depth, 'standard', 'generating a suggestion never mutates the profile');

seed([...Array.from({ length: 6 }, (_, i) => signal('skip', 'payments', `skip-${i}`)), signal('less')]);
const lessWins = generateSuggestions({ profile: { ...profile, topics: [{ ...payments, depth: 'deeper' }] }, now });
assert.equal(lessWins[0].id, 'depth:payments:standard');
assert.equal(applySuggestion({ ...profile, topics: [{ ...payments, depth: 'deeper' }] }, lessWins[0]).topics[0].depth, 'standard');

const addProfile = { ...profile, topics: [] };
seed(Array.from({ length: 3 }, (_, i) => signal('sourceOpen', 'banking', `source-${i}`)));
const catalog = [{ id: 'banking', label: 'Banking', scope: 'Banks' }];
const add = generateSuggestions({ profile: addProfile, catalog, now });
assert.equal(add[0].kind, 'add');
assert.equal(applySuggestion(addProfile, add[0], catalog).topics[0].id, 'banking');

seed(Array.from({ length: 6 }, (_, i) => signal('skip', 'payments', `skip-${i}`)));
const snoozed = generateSuggestions({ profile, now, snoozes: { 'remove:payments:remove': now + 1000 } });
assert.equal(snoozed.length, 0, 'Not now hides a suggestion until its snooze expires');
seed([signal('skip', 'payments', 'old', 50 * 86400000)]);
assert.equal(generateSuggestions({ profile, now }).length, 0, 'old behavior does not generate fresh suggestions');
seed([...Array.from({ length: 6 }, (_, i) => signal('skip', 'payments', `skip-${i}`)), signal('mute')]);
assert.equal(generateSuggestions({ profile, now }).length, 0, 'muted topics never receive automatic suggestions');

console.log('Interest suggestion unit checks passed.');
