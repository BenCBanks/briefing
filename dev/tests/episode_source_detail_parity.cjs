require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

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
function safeHTTPS(url) { try { const parsed = new URL(url); return parsed.protocol === 'https:' && parsed.hostname && !parsed.username && !parsed.password ? parsed.href : ''; } catch { return ''; } }
function publishedLabel(value) { const iso = String(value || '').slice(0, 10); const date = new Date(`${iso}T12:00:00Z`); return Number.isNaN(date.getTime()) ? 'Date not listed' : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date); }

async function run() {
  global.document = { createElement: tag => new FixtureNode(tag) };
  const [{ renderEpisodePage }, exporter] = await Promise.all([import('../../src/episodes/episodes.js'), import('../../src/episodes/export.js')]);
  const current = { id: 'source-detail-current-r2', outlet: 'Fictional Harbor Desk', title: 'Current corrected harbor service notice with the complete title', published_at: '2026-11-20T15:00:00Z', url: 'https://fictional.example.test/current-harbor' };
  const old = { id: 'source-detail-old-r1', outlet: 'Fictional Archive Desk', title: 'Superseded harbor closure report', date: 'Nov 18, 2026', status: 'withdrawn', url: 'https://fictional.example.test/old-harbor' };
  const corrected = { id: 'episode-source-detail-r2', topic_label: 'Fictional harbor service', title: 'A corrected fictional harbor notice', asked: 'What changed in the harbor notice?', duration_minutes: 3, made_date: '21 Nov 2026', sources: [current, old], chapters: [{ id: 'current', title: 'Current notice', sentences: [{ text: 'The current notice keeps service open.', source_ids: [current.id] }] }, { id: 'old', title: 'Superseded claim', status: 'withdrawn', sentences: [{ text: 'The old claim said service ended.', source_ids: [old.id] }] }] };
  const withdrawn = { ...corrected, id: 'episode-source-detail-withdrawn-r2', status: 'withdrawn', title: 'Superseded episode title', asked: 'Did the harbor close?', chapters: [{ id: 'old', title: 'Superseded claim', status: 'withdrawn', sentences: [{ text: 'The old claim said service ended.', source_ids: [old.id] }] }] };
  const render = episode => { const container = new FixtureNode('main'); renderEpisodePage(container, episode); return container; };
  const correctedPage = render(corrected); const marker = findNodes(correctedPage, node => node.className === 'episode-citation')[0];
  const summary = findNodes(marker, node => node.tagName === 'SUMMARY')[0]; const time = findNodes(marker, node => node.tagName === 'TIME')[0]; const link = findNodes(marker, node => node.tagName === 'A')[0];
  const sourceDetail = { title: current.title, date: publishedLabel(current.published_at), url: safeHTTPS(current.url) };
  assert.equal(summary.getAttribute('aria-label'), `Source 1, ${current.outlet} · ${sourceDetail.title}`);
  assert.equal(time.textContent, sourceDetail.date, 'Episode citation uses the current publication timestamp when date is absent');
  assert.equal(link.href, sourceDetail.url, 'Episode citation keeps the same safe HTTPS URL as source detail');
  const transcript = exporter.buildPlainTranscript({ ...corrected, sources: [current, old] });
  assert.match(transcript, /Fictional Harbor Desk — Current corrected harbor service notice with the complete title \(Published Nov 20, 2026\)/);
  assert.doesNotMatch(transcript, /old-harbor|Superseded harbor closure report|The old claim said service ended/u);
  const withdrawnPage = render(withdrawn); const withdrawnText = withdrawnPage.textContent;
  assert.doesNotMatch(withdrawnText, /Did the harbor close|Superseded episode title|Superseded claim|old-harbor/u);
  assert.equal(findNodes(withdrawnPage, node => node.className === 'episode-citation').length, 0);
  assert.equal(findNodes(withdrawnPage, node => node.tagName === 'BUTTON' && node.textContent.trim() === 'Listen').length, 0);
  console.log('Episode/source detail parity: current title, fallback publication date and HTTPS URL agree; withdrawn actions stay absent.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
