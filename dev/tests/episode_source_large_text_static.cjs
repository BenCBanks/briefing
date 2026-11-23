require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

class FixtureNode {
  constructor(tagName, text = '') {
    this.tagName = tagName.toUpperCase(); this.className = ''; this.children = [];
    this.attributes = {}; this._text = String(text); this.listeners = {};
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = [...items]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  focus() { global.document.activeElement = this; }
}

function findNodes(root, predicate) {
  return [root, ...root.children.flatMap(child => findNodes(child, predicate))].filter(predicate);
}

async function run() {
  global.document = { createElement: tag => new FixtureNode(tag) };
  const { renderEpisodePage } = await import('../../src/episodes/episodes.js');
  const styles = fs.readFileSync(path.join(__dirname, '../../src/episodes/episodes.css'), 'utf8');
  const longTitle = 'Current corrected fictional harbor service notice with a complete title that must wrap at large text';
  const currentSource = {
    id: 'source-large-current-r2', outlet: 'Fictional Harbor Desk', title: longTitle,
    date: 'Nov 20, 2026', url: 'https://fictional.example.test/current-harbor',
  };
  const oldSource = {
    id: 'source-large-old-r1', outlet: 'Fictional Archive Desk', title: 'Superseded harbor closure claim',
    date: 'Nov 18, 2026', url: 'https://fictional.example.test/old-harbor', status: 'withdrawn',
  };
  const corrected = {
    id: 'episode-large-current-r2', topic_label: 'Fictional harbor service',
    title: 'A corrected fictional harbor notice', asked: 'What changed in the harbor notice?',
    duration_minutes: 3, made_date: '21 Nov 2026', audio_recorded: true,
    updated: { date: 'Nov 21, 2026', note: 'Corrected the fictional service date.' },
    sources: [currentSource, oldSource],
    chapters: [{ id: 'current', title: 'The current notice', sentences: [{
      text: 'The current fictional notice keeps service open through the weekend.', source_ids: [currentSource.id],
    }] }, { id: 'old', title: 'Superseded claim', status: 'withdrawn', sentences: [{
      text: 'The earlier fictional notice said service had ended.', source_ids: [oldSource.id],
    }] }],
  };
  const withdrawn = { ...corrected, id: 'episode-large-withdrawn-r2', status: 'withdrawn',
    title: 'Superseded fictional harbor episode title', asked: 'Did the harbor close?',
    chapters: [{ id: 'withdrawn', title: 'Superseded claim', status: 'withdrawn', sentences: [{
      text: 'The superseded fictional claim said the harbor had closed.', source_ids: [oldSource.id],
    }] }],
  };
  const capture = episode => {
    const container = new FixtureNode('main'); renderEpisodePage(container, episode);
    const markers = findNodes(container, node => node.className === 'episode-citation');
    return {
      text: container.textContent,
      marker: markers[0] && {
        name: findNodes(markers[0], node => node.tagName === 'SUMMARY')[0]?.getAttribute('aria-label'),
        detail: findNodes(markers[0], node => node.className === 'episode-citation-detail')[0]?.textContent,
        date: findNodes(markers[0], node => node.tagName === 'TIME')[0]?.textContent,
        link: findNodes(markers[0], node => node.tagName === 'A')[0],
      },
      markers,
      listen: findNodes(container, node => node.tagName === 'BUTTON' && node.textContent.trim() === 'Listen'),
    };
  };
  const current = capture(corrected); const removed = capture(withdrawn);
  assert.equal(current.markers.length, 1, 'Corrected transcript keeps only its current citation');
  assert.equal(current.marker.name, `Source 1, ${currentSource.outlet} · ${longTitle}`, 'Accessible citation name keeps the complete long title');
  assert.match(current.marker.detail, new RegExp(longTitle), 'Citation detail keeps the complete long title');
  assert.equal(current.marker.date, currentSource.date, 'Current source date remains truthful');
  assert.equal(current.marker.link.href, currentSource.url, 'Current citation keeps the HTTPS URL');
  assert.doesNotMatch(current.text, /Superseded harbor closure claim|earlier fictional notice said service had ended|old-harbor/u, 'Corrected view excludes withdrawn history');
  assert.equal(current.listen.length, 1, 'Corrected recorded Episode keeps Listen');
  assert.equal(removed.markers.length, 0, 'Withdrawn Episode has no actionable citation');
  assert.equal(removed.listen.length, 0, 'Withdrawn Episode has no Listen action');
  assert.doesNotMatch(removed.text, /Did the harbor close|Superseded fictional harbor episode title|superseded fictional claim said|old-harbor/u, 'Withdrawn view excludes old Asked, title, claim and source URL');
  assert.match(removed.text, /Story withdrawn/);
  assert.match(styles, /\.episode-citation-detail[^}]*max-width:\s*min\(260px,\s*calc\(100vw - 48px\)\)/u, 'Citation detail has a bounded wrapping width at large text');
  assert.match(styles, /\.episode-citation summary[^}]*min-height:\s*44px/u, 'Citation target remains at least 44px');
  assert.match(styles, /\.episode-citation-detail a[^}]*min-height:\s*44px/u, 'Source action remains at least 44px');
  console.log('Static Episode source audit: long current title, truthful HTTPS citation, 200% CSS contract, and withdrawn action suppression passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
