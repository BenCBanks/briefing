require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
class FixtureNode {
  constructor(tagName, text = '') { this.tagName = tagName.toUpperCase(); this.className = ''; this.children = []; this.attributes = {}; this._text = String(text); this.listeners = {}; this.dataset = new Proxy({}, { set: (target, key, value) => { target[key] = String(value); this.setAttribute(`data-${String(key).replace(/[A-Z]/gu, letter => `-${letter.toLocaleLowerCase()}`)}`, value); return true; } }); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = [...items]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  focus() { global.document.activeElement = this; }
  querySelector(selector) { const className = selector.startsWith('.') ? selector.slice(1) : ''; const tag = className ? '' : selector.toUpperCase(); return findNodes(this, node => className ? node.className.split(/\s+/u).includes(className) : node.tagName === tag)[0] || null; }
}
function findNodes(root, predicate) { return [root, ...root.children.flatMap(child => findNodes(child, predicate))].filter(predicate); }

async function run() {
  global.document = { createElement: tag => new FixtureNode(tag), createTextNode: text => new FixtureNode('#text', text) };
  const [{ renderTopicDetail }, { renderEpisodePage }, exporter] = await Promise.all([
    import('../../src/topics/topic-detail.js'), import('../../src/episodes/episodes.js'), import('../../src/episodes/export.js'),
  ]);
  const appSource = fs.readFileSync(path.join(__dirname, '../../src/app.js'), 'utf8');
  const current = { id: 'source-update-parity-r2', publisher: 'Fictional Notice Desk', outlet: 'Fictional Notice Desk', title: 'Current fictional notice with an update date only', updated_at: '2026-11-22', url: 'https://fictional.example.test/update-parity' };
  const old = { id: 'source-update-parity-old-r1', publisher: 'Fictional Archive Desk', outlet: 'Fictional Archive Desk', title: 'Superseded fictional closure report', date: 'Nov 18, 2026', status: 'withdrawn', url: 'https://fictional.example.test/old-closure' };
  const topicContainer = new FixtureNode('main');
  renderTopicDetail(topicContainer, { topic: { label: 'Fictional service notices' }, sources: [current, old], items: [{ edition: { local_date: '2026-11-22' }, section: { id: 'current', heading: 'A current fictional notice', source_ids: [current.id], sources: [current] } }, { section: { id: 'old', status: 'withdrawn', heading: 'Superseded claim', source_ids: [old.id], sources: [old] } }] });
  const topicCard = topicContainer.querySelector('.topic-detail-story');
  const topicAction = findNodes(topicCard, node => node.tagName === 'BUTTON')[0];
  assert.equal(topicCard.querySelector('.topic-detail-date').textContent, 'Updated Nov 22, 2026');
  assert.equal(topicAction.textContent, `${current.publisher} · ${current.title}`);
  assert.equal(topicAction.getAttribute('aria-label'), `Open source: ${current.publisher}, ${current.title}`);
  assert.doesNotMatch(topicContainer.textContent, /Superseded fictional closure report|old-closure/u);
  const episode = { id: 'episode-update-parity-r2', topic_label: 'Fictional service notices', title: 'A current fictional service notice', asked: 'What changed?', duration_minutes: 3, made_date: '22 Nov 2026', sources: [current, old], chapters: [{ id: 'current', title: 'Current notice', sentences: [{ text: 'The current notice was updated.', source_ids: [current.id] }] }, { id: 'old', title: 'Superseded claim', status: 'withdrawn', sentences: [{ text: 'The old claim said service ended.', source_ids: [old.id] }] }] };
  const episodeContainer = new FixtureNode('main'); renderEpisodePage(episodeContainer, episode);
  const marker = findNodes(episodeContainer, node => node.className === 'episode-citation')[0];
  assert.equal(findNodes(marker, node => node.tagName === 'TIME')[0].textContent, 'Updated Nov 22, 2026');
  assert.equal(findNodes(marker, node => node.tagName === 'A')[0].href, current.url);
  assert.doesNotMatch(episodeContainer.textContent, /Superseded fictional closure report|old-closure|The old claim said/u);
  const transcript = exporter.buildPlainTranscript(episode);
  assert.match(transcript, /\[1\] Fictional Notice Desk — Current fictional notice with an update date only \(Updated Nov 22, 2026\)/);
  assert.doesNotMatch(transcript, /Published Nov 22, 2026|Superseded fictional closure report|old-closure/u);
  assert.match(appSource, /const sourceDateLabel = sourcePublication \? `Document date · \$\{sourceDate\}`\s+: sourceUpdate \? `Updated · \$\{sourceDate\}`/u, 'source detail labels an update-only date as Updated');
  assert.match(appSource, /external\.setAttribute\('aria-label', `Open original: \$\{publisher\}, \$\{source\.title \|\| 'current source'\}`\);/u, 'source detail action names current publisher and title');
  console.log('Topic/source-detail/Episode update parity: Updated labels, current title/HTTPS action and withdrawn suppression passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
