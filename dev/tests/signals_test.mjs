import assert from 'node:assert/strict';

const store = new Map();
globalThis.localStorage = {
  getItem: key => store.has(key) ? store.get(key) : null,
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: key => store.delete(key),
};
const { record, events, countBy, behaviorWeights, clear } = await import('../../src/interests/signals.js');
const behaviorFixture = (await import('./fixtures/topic-behavior.json', { with: { type: 'json' } })).default;
const base = 2_000_000_000_000;
clear();
for (const [index, type] of ['finish', 'skip', 'save', 'sourceOpen', 'explainOpen', 'replay', 'more', 'less', 'mute'].entries()) {
  assert.equal(record(type, `story-${index}`, 'payments', base - index * 1000), true);
}
assert.equal(events().length, 9);
assert.deepEqual(Object.keys(events()[0]).sort(), ['storyId', 'topicId', 'ts', 'type']);
assert.equal(countBy('payments', 'finish', 2, base), 1);
assert.equal(countBy('payments', 'skip', 0, base), 0);
const fixtureNow = Date.parse(behaviorFixture.as_of);
store.set('briefing:v1:signals', JSON.stringify(behaviorFixture.events.map(({ age_days, ...event }, index) => ({
  ...event, storyId: `fixture-${index}`, ts: fixtureNow - age_days * 86400000,
}))));
const behavior = behaviorWeights(['housing', 'payments', 'banking', 'unused'], fixtureNow);
assert.ok(behavior.housing > 1, 'recent finishes, saves and more feedback raise a topic weight');
assert.ok(behavior.payments < 1, 'recent skips and less feedback lower a topic weight');
assert.equal(behavior.banking, 1, 'old events outside the window do not shift a topic');
assert.equal(behavior.unused, 1, 'topics without events keep a neutral weight');
assert.ok(Object.values(behavior).every(value => value >= 0.5 && value <= 1.5), 'weights stay inside their bounds');
assert.equal(record('invalid', 'story', 'payments', base), false);
assert.equal(record('save', '', 'payments', base), false);
assert.equal(record('save', 'story', '', base), false);
for (let index = 0; index < 1100; index += 1) record('save', `story-${index}`, 'banking', base + index);
assert.equal(events().length, 1000, 'storage is capped at the newest 1000 valid events');
clear();
assert.deepEqual(events(), []);
console.log('signals: event validation, countBy time window, cap, and clear passed.');
