require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

class FixtureNode {
  constructor(tagName, text = '') { this.tagName = tagName.toUpperCase(); this.className = ''; this.children = []; this.attributes = {}; this._text = String(text); this.listeners = {}; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = [...items]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  focus() { global.document.activeElement = this; }
}
function findNodes(root, predicate) { return [root, ...root.children.flatMap(child => findNodes(child, predicate))].filter(predicate); }

async function run() {
  global.document = { createElement: tag => new FixtureNode(tag) };
  const [{ renderEpisodePage }, exporter] = await Promise.all([
    import('../../src/episodes/episodes.js'), import('../../src/episodes/export.js'),
  ]);
  const detailSource = fs.readFileSync(path.join(__dirname, '../../src/screens/detail-routes.js'), 'utf8');
  const current = { id: 'source-update-only-r2', outlet: 'Fictional Notice Desk', title: 'Current fictional notice with an update date only', updated_at: '2026-11-22', url: 'https://fictional.example.test/update-only' };
  const old = { id: 'source-withdrawn-r1', outlet: 'Fictional Archive Desk', title: 'Superseded fictional closure report', date: 'Nov 18, 2026', status: 'withdrawn', url: 'https://fictional.example.test/old-closure' };
  const episode = { id: 'episode-update-only-r2', topic_label: 'Fictional service notices', title: 'A current fictional service notice', asked: 'What changed?', duration_minutes: 3, made_date: '22 Nov 2026', sources: [current, old], chapters: [{ id: 'current', title: 'Current notice', sentences: [{ text: 'The current fictional notice was updated.', source_ids: [current.id] }] }, { id: 'old', title: 'Superseded claim', status: 'withdrawn', sentences: [{ text: 'The old claim said service ended.', source_ids: [old.id] }] }] };
  const page = new FixtureNode('main'); renderEpisodePage(page, episode);
  const marker = findNodes(page, node => node.className === 'episode-citation')[0];
  const time = findNodes(marker, node => node.tagName === 'TIME')[0];
  const sourceLink = findNodes(marker, node => node.tagName === 'A')[0];
  assert.equal(time.textContent, 'Updated Nov 22, 2026', 'Episode citation labels an update-only source as Updated');
  assert.equal(sourceLink.href, current.url, 'Episode citation keeps the safe current HTTPS URL');
  assert.match(detailSource, /const sourceUpdate = source\.updated_at/u, 'source detail reads the update timestamp');
  assert.match(detailSource, /sourceUpdate \? `Updated · \$\{sourceDate\}`/u, 'source detail labels an update-only date as Updated');
  const transcript = exporter.buildPlainTranscript(episode);
  assert.match(transcript, /\[1\] Fictional Notice Desk — Current fictional notice with an update date only \(Updated Nov 22, 2026\)/);
  assert.doesNotMatch(transcript, /Published Nov 22, 2026|Superseded fictional closure report|old-closure|The old claim said/u);
  assert.doesNotMatch(page.textContent, /Superseded fictional closure report|old-closure|The old claim said/u);
  console.log('Update-only Episode date truth: Updated labels, safe current link and withdrawn suppression passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
