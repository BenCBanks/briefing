require('../test_guard.cjs').enforceTestEnvironment();

// Execute the production form and submit handlers; no browser or real transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {};
    this.style = {}; this.className = ''; this.value = ''; this.disabled = false;
    this.classList = { toggle() {} };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() {}
  querySelectorAll(selector) {
    const matches = node => selector.startsWith('#') ? node.id === selector.slice(1)
      : selector.startsWith('.') ? node.className.split(' ').includes(selector.slice(1))
        : node.tagName === selector;
    return this.children.flatMap(child => [
      ...(matches(child) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  click() { if (!this.disabled) this.listeners.click?.(); }
}

const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../../src/app.js'), 'utf8');
const screenSource = fs.readFileSync(path.join(__dirname, '../../src/screens/on-demand.js'), 'utf8')
  .replace(/^export /gm, '');
function productionFunction(start, end) {
  const begin = source.indexOf(start); const finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, 'production function boundaries exist');
  return source.slice(begin, finish);
}
const renderer = productionFunction('function renderEpisodeRequestPage()', 'async function loadEpisodeCards(');
const submit = productionFunction('async function startEpisodeJob(', 'function announceEpisodeStage(');

async function check(error, httpFailure = false, malformed = false, staleResponse = false) {
  const request = httpFailure || malformed || staleResponse ? '  Why do fictional harbor tides change?  ' : 'Why do fictional harbor tides change?';
  const content = new Element('main');
  const unrelatedReadyCard = new Element('article');
  unrelatedReadyCard.className = 'ask-episode-card';
  unrelatedReadyCard.setAttribute('data-job-id', 'fictional-unrelated-ready');
  unrelatedReadyCard.setAttribute('data-status', 'done');
  unrelatedReadyCard.textContent = 'Ready · Listen — fictional harbor history';
  const posts = []; let allow = false; let polls = 0; let resolveResponse;
  const pendingResponse = new Promise(resolve => { resolveResponse = resolve; });
  const context = vm.createContext({
    content, document: { createElement: tag => new Element(tag) },
    node(tag, text, className = '') { const el = new Element(tag); el.textContent = text; el.className = className; return el; },
    episodeDraft: { prompt: request, minutes: 15 },
    episodeRequestBlocked: false, episodeRequestBusy: false, episodeRequestError: '',
    episodeJob: null, episodeJobTimer: null,
    EPISODE_MODES: [{ id: 'deep-dive' }],
    episodeTopicSuggestions: () => [],
    loadEpisodeCards: cards => { if (httpFailure) cards.append(unrelatedReadyCard); },
    syncProfile: async () => {}, interestProfile: {}, pack: { topics: [] },
    episodeModeStyle: () => 'explainer',
    location: { hash: '#ask-episode' }, window: { scrollY: 0 }, clearTimeout,
    pollEpisodeJob: () => { polls += 1; },
    fetch: async (url, options) => {
      assert.equal(url, '/api/episodes'); assert.equal(options.method, 'POST');
      posts.push(JSON.parse(options.body));
      if (staleResponse) return pendingResponse;
      return { status: allow || malformed ? 202 : httpFailure ? 500 : 429, ok: allow || malformed,
        json: async () => allow ? { job_id: 'fictional-usage-retry', status: 'queued' }
          : malformed ? { status: 'queued' }
          : httpFailure ? { error: 'internal_error', detail: 'FIXTURE_PRIVATE_SERVER_FAILURE' }
            : { error, left_today: 0 } };
    },
  });
  vm.runInContext(`${screenSource}\n${renderer}\n${submit}\n
    function rememberEpisodeLength(minutes) { episodeDraft.minutes = minutes; }
    function navigate(hash) { location.hash = hash; }
    function route() { content.replaceChildren(); renderEpisodeRequestPage(); }
    route();`, context);
  const input = () => content.querySelector('#episode-request-prompt');
  const action = () => content.querySelector('#episode-request-start');
  const edit = value => { input().value = value; input().listeners.input(); };
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const selectedLength = () => content.querySelectorAll('.episode-request-chip')
    .find(chip => chip.getAttribute('aria-pressed') === 'true').textContent;

  if (httpFailure) {
    assert.equal(content.querySelector('.ask-episode-card'), unrelatedReadyCard,
      'the unrelated ready episode card exists before the failed request');
  }
  action().click(); await settle();
  assert.equal(posts.length, 1);
  if (staleResponse) {
    assert.equal(context.episodeRequestBusy, true, 'the original request is still pending');
    assert.equal(input().disabled, false, 'the prompt remains editable while the request is pending');
    const newerDraft = '  A newer fictional harbor history draft  ';
    edit(newerDraft);
    assert.equal(context.episodeDraft.prompt, newerDraft, 'the reader can enter a newer draft before the old response settles');
    resolveResponse({ status: 500, ok: false, json: async () => ({ error: 'server_failure' }) });
    await settle();
    assert.equal(context.episodeRequestBusy, false, 'the older response settles the busy state');
    assert.equal(context.episodeDraft.prompt, newerDraft, 'the stale response does not replace the newer draft in state');
    assert.equal(input().value, newerDraft, 'the stale response does not replace the newer draft in the form');
    assert.equal(selectedLength(), '15 min', 'the stale response preserves the selected length');
    assert.deepEqual(posts[0], { topic: request, length: 15, style: 'explainer' }, 'the original request payload stays bound to its original question');
    assert.equal(content.querySelector('.episode-request-error').textContent,
      'Your episode could not be started. Please try again.', 'the older response still shows its generic failure');
    assert.equal(context.episodeJob, null, 'the older failure does not invent a job');
    assert.equal(context.location.hash, '#ask-episode', 'the newer draft stays on the request form');
    assert.equal(posts.length, 1, 'settling the older response does not send another request');
    return;
  }
  if (malformed) {
    assert.equal(content.querySelector('.episode-request-error').textContent,
      'Your episode could not be started. Please try again.', 'malformed 202 shows a plain recoverable error');
    assert.equal(context.episodeRequestBusy, false, 'malformed 202 settles the busy state');
    assert.equal(context.episodeRequestBlocked, false, 'malformed 202 does not block editing');
    assert.equal(action().disabled, false, 'submit remains enabled for a deliberate retry');
    assert.equal(input().disabled, false, 'the exact request remains editable');
    assert.equal(input().value, request, 'malformed 202 preserves every whitespace character');
    assert.equal(selectedLength(), '15 min', 'malformed 202 preserves the selected length');
    assert.deepEqual(posts[0], { topic: request, length: 15, style: 'explainer' });
    assert.equal(context.episodeJob, null, 'missing job identity creates no episode job');
    assert.equal(context.episodeJobTimer, null, 'missing job identity creates no polling timer');
    assert.equal(polls, 0, 'missing job identity never polls');
    assert.equal(context.location.hash, '#ask-episode', 'reader stays on the request form');
    assert.equal(content.querySelectorAll('.episode-making-page').length, 0, 'no making state is rendered');
    assert.equal(content.querySelectorAll('.episode-card').length, 0, 'no history card is invented');
    const revised = '  Explain fictional harbor tide cycles.  ';
    edit(revised);
    assert.equal(posts.length, 1, 'editing after malformed 202 does not submit automatically');
    action().click(); await settle();
    assert.equal(posts.length, 2, 'one deliberate retry sends one additional request');
    assert.equal(context.episodeRequestBusy, false, 'the malformed-response retry settles the busy state');
    assert.deepEqual(posts[1], { topic: revised, length: 15, style: 'explainer' },
      'the malformed-response retry sends the exact edited draft and selected length');
    assert.equal(selectedLength(), '15 min', 'the selected length remains after the retry fails again');
    assert.equal(context.episodeJob, null, 'a second malformed response still creates no job');
    assert.equal(context.episodeJobTimer, null, 'a second malformed response still creates no polling timer');
    assert.equal(polls, 0, 'no malformed response starts polling');
    return;
  }
  if (httpFailure) {
    assert.equal(content.querySelector('.episode-request-error').textContent,
      'Your episode could not be started. Please try again.', 'HTTP500 renders a generic recoverable error');
    assert.equal(context.episodeRequestBusy, false, 'HTTP500 settles the busy state');
    assert.equal(context.episodeRequestBlocked, false, 'HTTP500 does not mark the request as quota-blocked');
    assert.equal(action().disabled, false, 'submit remains enabled after the settled HTTP500 error');
    assert.equal(input().disabled, false, 'the exact request remains editable after HTTP500');
    assert.equal(input().value, request, 'HTTP500 preserves every whitespace character in the request');
    assert.equal(selectedLength(), '15 min', 'the selected length remains available for a deliberate retry');
    assert.deepEqual(posts[0], { topic: request, length: 15, style: 'explainer' }, 'HTTP500 submits the exact raw request and selected length');
    assert.equal(context.location.hash, '#ask-episode', 'HTTP500 keeps the reader on the request screen');
    assert.equal(content.querySelectorAll('.ask-episode-card').length, 1,
      'HTTP500 leaves the unrelated ready episode card in history');
    assert.equal(content.querySelector('.ask-episode-card'), unrelatedReadyCard,
      'HTTP500 preserves the same unrelated ready episode card');
    assert.equal(content.querySelector('.ask-episode-card').getAttribute('data-job-id'), 'fictional-unrelated-ready');
    assert.equal(content.querySelector('.ask-episode-card').getAttribute('data-status'), 'done');
    const revised = '  How do fictional harbor tides change?\nExplain the lunar cycle.  ';
    edit(revised);
    assert.equal(posts.length, 1, 'editing after HTTP500 does not submit automatically');
    action().click(); await settle();
    assert.equal(posts.length, 2, 'one deliberate retry sends one additional request');
    assert.deepEqual(posts[1], { topic: revised, length: 15, style: 'explainer' },
      'the HTTP500 retry sends the exact updated draft and selected length');
    assert.equal(input().value, revised, 'the updated draft remains visible after the retry fails');
    assert.equal(context.episodeRequestBusy, false, 'the deliberate retry settles its busy state');
    return;
  }
  assert.equal(action().disabled, true, `${error}: initial refusal prevents immediate retry`);
  assert.equal(input().value, 'Why do fictional harbor tides change?');
  assert.equal(selectedLength(), '15 min');
  action().click(); await settle(); assert.equal(posts.length, 1);

  const revised = '  How do fictional harbor tides change?\nExplain the lunar cycle.  ';
  edit(revised);
  assert.equal(action().disabled, false, `${error}: editing unlocks an explicit retry`);
  assert.equal(action().getAttribute('aria-label'), 'Make episode');
  assert.equal(selectedLength(), '15 min', 'editing keeps the requested length');
  assert.equal(posts.length, 1, 'editing never submits automatically');
  action().click(); await settle();
  assert.equal(posts.length, 2);
  assert.equal(posts[1].topic, revised, 'the retry keeps every character of the edited question');
  assert.equal(posts[1].length, 15);
  assert.equal(action().disabled, true, 'the server may still deny the edited request');
  assert.equal(input().value, revised, 'repeated refusal keeps the exact request');

  edit('   '); assert.equal(action().disabled, true, 'empty edits cannot submit');
  action().click(); await settle(); assert.equal(posts.length, 2);
  edit(revised); allow = true;
  action().click(); action().click(); await settle();
  assert.equal(posts.length, 3, 'one explicit retry creates one request even on double activation');
  assert.equal(posts[2].topic, revised); assert.equal(posts[2].length, 15);
  assert.equal(context.location.hash, '#episode-making', 'a newly allowed request can proceed');
}

(async () => {
  await check('cost_cap_reached');
  await check('daily_limit_reached');
  await check('server_failure', true);
  await check('malformed_response', false, true);
  await check('server_failure', false, false, true);
  console.log('Usage-limit, HTTP500, malformed-success, and stale-response request state controls: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
