require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

class FixtureNode {
  constructor(tagName, text = '') {
    this.tagName = tagName.toUpperCase(); this.className = ''; this.children = [];
    this.attributes = {}; this.listeners = {}; this._text = String(text); this.type = '';
    this.dataset = new Proxy({}, { set: (target, key, value) => {
      target[key] = String(value);
      this.setAttribute(`data-${String(key).replace(/[A-Z]/gu, letter => `-${letter.toLocaleLowerCase()}`)}`, value);
      return true;
    } });
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  focus() { global.document.activeElement = this; }
  click() { for (const callback of this.listeners.click || []) callback({ preventDefault() {} }); }
  querySelector(selector) {
    const className = selector.startsWith('.') ? selector.slice(1) : '';
    const tagName = className ? '' : selector.toUpperCase();
    return findNodes(this, node => className
      ? node.className.split(/\s+/u).includes(className)
      : node.tagName === tagName)[0] || null;
  }
}

function findNodes(root, predicate) {
  return [root, ...root.children.flatMap(child => findNodes(child, predicate))].filter(predicate);
}

async function run() {
  global.document = {
    createElement: tag => new FixtureNode(tag),
    createTextNode: text => new FixtureNode('#text', text),
  };
  const { renderTopicDetail } = await import('../../src/topics/topic-detail.js');
  const current = {
    id: 'source-current-corrected-r2', publisher: 'Fictional Harbor Desk',
    title: 'Current corrected harbor service notice with the complete title',
    published_at: '2026-11-20', updated_at: '2026-11-22',
    url: 'https://fictional.example.test/current-harbor',
  };
  const withdrawn = {
    id: 'source-withdrawn-r1', publisher: 'Fictional Archive Desk',
    title: 'Superseded harbor closure claim', status: 'withdrawn', withdrawn: true,
    published_at: '2026-11-18', url: 'https://fictional.example.test/old-harbor',
  };
  const opened = [];
  const container = new FixtureNode('main');
  renderTopicDetail(container, {
    topic: { label: 'Fictional harbor service' },
    sources: [withdrawn, current],
    items: [
      { edition: { local_date: '2026-11-22' }, section: {
        id: 'current-story', heading: 'Current fictional harbor update',
        source_ids: [current.id], sources: [current],
      } },
      { section: {
        id: 'withdrawn-story', status: 'withdrawn', heading: 'Superseded harbor closure claim',
        body: 'The old claim said the harbor had closed.', source_ids: [withdrawn.id], sources: [withdrawn],
      } },
    ],
    actions: { openSource: id => opened.push({ id, returnSourceId: global.document.activeElement?.dataset?.source || null }) },
  });

  const actions = findNodes(container, node => node.tagName === 'BUTTON' && node.dataset.source);
  assert.equal(actions.length, 1, 'only the current source action is available');
  const action = actions[0];
  assert.equal(action.type, 'button', 'the citation uses native keyboard-focusable button semantics');
  assert.equal(action.dataset.source, current.id, 'the action carries the current source ID used for return focus');
  assert.equal(action.textContent, `${current.publisher} · ${current.title}`, 'the visible action keeps the complete current title');
  assert.equal(action.getAttribute('aria-label'), `Open source: ${current.publisher}, ${current.title}`,
    'the accessible action name identifies the current source');
  assert.match(container.textContent, /Updated Nov 22, 2026/u, 'current update date remains readable');
  assert.doesNotMatch(container.textContent, /Superseded harbor closure claim|old claim said|old-harbor/u,
    'withdrawn claim and historical source action stay hidden');
  assert.ok(!/^https?:\/\//u.test(action.getAttribute('aria-label')), 'the accessible name does not expose a bare URL');

  action.focus();
  action.click();
  assert.deepEqual(opened, [{ id: current.id, returnSourceId: current.id }],
    'activating the citation passes the same current source ID held as the focus-return key');
  console.log('Source detail return fixture: current source ID, native action semantics, complete label and withdrawn suppression passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
