require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

async function withFetch(stub, callback) {
  const original = globalThis.fetch;
  globalThis.fetch = stub;
  try { return await callback(); }
  finally { globalThis.fetch = original; }
}

async function run() {
  const { interpretProfile } = await import('../../src/interests/provider.js');
  const { interpret } = await import('../../src/interests/interpret.js');
  const topics = [
    { id: 'payments', label: 'Payments', scope: 'How money moves' },
    { id: 'banking', label: 'Banking', scope: 'Banks and deposits' },
    { id: 'digital', label: 'Digital life', scope: 'Identity, data and security' },
    { id: 'insurance', label: 'Insurance', scope: 'Coverage and claims' },
    { id: 'sports', label: 'Sports', scope: 'Rules and competition' },
  ];
  const words = 'I follow payments and digital privacy.';
  const local = interpret(words, { topics, minutes: 10 }).profile;
  const remoteProfile = {
    ...local,
    topics: [
      { ...local.topics[0], id: 'payment-activity', label: 'Payments', include: [], mustFollow: true },
      { ...local.topics[1], id: 'digital-privacy', label: 'Digital privacy', sourcePhrase: words },
      { ...local.topics[1], id: 'climate-law', label: 'Climate law', scope: 'Rules for reducing emissions', sourcePhrase: 'Climate law' },
      { ...local.topics[1], id: 'climate-law-again', label: 'Climate law', scope: 'Rules for reducing emissions', sourcePhrase: 'Climate law' },
    ],
  };
  let requestArgs;
  const remote = await withFetch(async (...args) => {
    requestArgs = args;
    return { ok: true, json: async () => ({ profile: remoteProfile, question: null, flags: ['instruction-like text removed'] }) };
  }, () => interpretProfile(words, { topics, minutes: 15 }));
  assert.equal(requestArgs[0], '/api/interpret');
  assert.equal(requestArgs[1].method, 'POST');
  assert.deepEqual(JSON.parse(requestArgs[1].body), { words });
  assert.equal(remote.provider, 'remote');
  assert.equal(remote.profile.minutes, 15);
  assert.deepEqual(remote.profile.topics.map(topic => topic.id), ['payments', 'digital', 'custom-climate-law']);
  assert.equal(new Set(remote.profile.topics.map(topic => topic.label.toLocaleLowerCase())).size, 3, 'repeat custom topics collapse to one starter profile entry');
  assert.deepEqual(remote.profile.topics[0].include, ['I follow payments'], 'A recognized topic gets its reader-authored source phrase when Gemini returns an empty include list');
  assert.equal(remote.profile.topics[0].mustFollow, false, 'Instruction removal cannot leave a must-follow flag active');
  assert.equal(remote.profile.topics[0].cadence, 'daily', 'A remote model cannot add weekly cadence absent from the reader words');
  assert.equal(remote.profile.topics[1].mustFollow, false, 'A remote model cannot add must-follow absent from the reader words');
  assert.deepEqual(remote.flags, ['instruction-like text removed']);

  const onboardingWords = 'Payments and banking news, health insurance and finance policy, AI in finance. Skip crypto hype.';
  const onboardingResult = await withFetch(async () => ({ ok: true, json: async () => ({ profile: {
    ...local, words: onboardingWords, minutes: 12, muted: [{ label: 'Crypto' }, { label: 'crypto' }],
    topics: [
      { id: 'payments-banking', label: 'Payments and Banking News', scope: 'Payment and banking news',
        include: ['payments', 'banking'], exclude: ['crypto hype'], depth: 'standard', cadence: 'daily',
        mustFollow: false, sourcePhrase: 'Payments and banking news' },
      { id: 'health-insurance-finance-policy', label: 'Health Insurance and Finance Policy', scope: 'insurance policy',
        include: ['health insurance', 'finance policy'], exclude: [], depth: 'standard', cadence: 'daily',
        mustFollow: false, sourcePhrase: 'health insurance and finance policy' },
      { id: 'ai-in-finance', label: 'AI in Finance', scope: 'Artificial intelligence in finance',
        include: ['AI', 'finance'], exclude: ['crypto hype'], depth: 'standard', cadence: 'daily',
        mustFollow: false, sourcePhrase: 'AI in finance' },
      { id: 'crypto-hype', label: 'Crypto Hype', scope: 'Promotion', include: [], exclude: ['Skip crypto hype'],
        depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Skip crypto hype' },
    ]
  }, question: null }) }), () => interpretProfile(onboardingWords, { topics, minutes: 12 }));
  assert.deepEqual(onboardingResult.profile.topics.map(topic => topic.label), [
    'Payments and banking', 'Health insurance and finance policy', 'AI in finance']);
  assert.deepEqual(onboardingResult.profile.topics.map(topic => topic.id), [
    'custom-payments-banking', 'custom-health-insurance-finance-policy', 'custom-ai-in-finance']);
  assert.deepEqual(onboardingResult.profile.muted, [{ label: 'Crypto' }]);
  assert.ok(onboardingResult.profile.topics.every(topic => topic.exclude.length === 0));

  const fallback = await withFetch(async () => { throw new TypeError('offline'); }, () =>
    interpretProfile('I follow banking.', { topics, minutes: 10 }));
  assert.equal(fallback.provider, 'local');
  assert.match(fallback.fallbackError, /retry|basic matching/i);
  assert.equal(fallback.profile.topics[0].id, 'banking');

  const malformed = await withFetch(async () => ({ ok: true, json: async () => ({ profile: {}, question: 'bad shape' }) }), () =>
    interpretProfile('I follow banking.', { topics }));
  assert.equal(malformed.provider, 'local');
  assert.match(malformed.fallbackError, /could not connect/i);

  let localFetchCalled = false;
  const basic = await withFetch(async () => { localFetchCalled = true; throw new Error('must not run'); }, () =>
    interpretProfile('I follow insurance.', { topics, minutes: 12, localOnly: true }));
  assert.equal(localFetchCalled, false);
  assert.equal(basic.provider, 'local');
  assert.equal(basic.profile.topics[0].id, 'insurance');

  const controller = new AbortController();
  const canceled = withFetch((_url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); });
  }), () => interpretProfile('I follow banking.', { topics, signal: controller.signal }));
  controller.abort();
  await assert.rejects(canceled, { name: 'AbortError' }, 'Cancel aborts the shared Gemini adapter request');
  console.log('Interest provider: shared Gemini adapter request, fixed/custom topic normalization, flags, local fallback, invalid response, local-only path and cancellation passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
