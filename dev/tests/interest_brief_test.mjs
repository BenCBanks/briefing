import assert from 'node:assert/strict';
import { applyInterestBrief, deriveInterestBrief } from '../../src/interests/brief.js';

const profile = {
  version: 1,
  words: 'Follow payments and banking in North Carolina. Skip crypto hype. Give me deeper context.',
  minutes: 12,
  background: null,
  muted: [],
  topics: [
    { id: 'payments', label: 'Payments', scope: 'Payment rails', include: ['settlement'], exclude: ['crypto hype'], depth: 'deeper', cadence: 'daily', mustFollow: false, sourcePhrase: 'payments' },
    { id: 'banking', label: 'Banking', scope: 'Deposits and lending', include: ['deposits'], exclude: [], depth: 'deeper', cadence: 'daily', mustFollow: false, sourcePhrase: 'banking' },
  ],
};

const derived = deriveInterestBrief(profile);
assert.equal(derived.geography, 'North Carolina');
assert.deepEqual(derived.subjects, ['Payments', 'Banking']);
assert.deepEqual(derived.exclusions, ['crypto hype']);
assert.equal(derived.depth, 'deeper');

const edited = applyInterestBrief(profile, {
  geography: 'Charlotte, North Carolina',
  subjects: 'Payments, Community banking',
  exclusions: 'crypto hype, personal account advice',
  depth: 'headline',
});
assert.equal(edited.brief.geography, 'Charlotte, North Carolina');
assert.deepEqual(edited.brief.subjects, ['Payments', 'Community banking']);
assert.equal(edited.topics[0].id, 'payments');
assert.match(edited.topics[1].id, /^custom-community-banking$/);
assert.equal(edited.topics[1].scope, 'Community banking in Charlotte, North Carolina.');
assert.deepEqual(edited.topics[1].exclude, ['crypto hype', 'personal account advice']);
assert.equal(edited.topics[1].depth, 'headline');
assert.match(edited.words, /Skip crypto hype, personal account advice/);
assert.throws(() => applyInterestBrief(profile, { subjects: '' }), /at least one subject/);

console.log('Interest brief derivation, plain-language edits, profile topic updates and bounds passed.');
