require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');

class FixtureNode {
  constructor(tagName, text = '') {
    this.tagName = String(tagName).toUpperCase();
    this.className = '';
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = String(text);
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
  querySelector(selector) { return find(this, node => selector.startsWith('.')
    ? node.className.split(/\s+/u).includes(selector.slice(1))
    : node.tagName === selector.toUpperCase())[0] || null; }
  click() { for (const callback of this.listeners.click || []) callback({ preventDefault() {} }); }
}

function find(root, predicate) {
  return [root, ...root.children.flatMap(child => find(child, predicate))].filter(predicate);
}

async function run() {
  const [{ renderTopicDetail }, { renderEpisodePage }, { buildPlainTranscript }] = await Promise.all([
    import('../../src/topics/topic-detail.js'),
    import('../../src/episodes/episodes.js'),
    import('../../src/episodes/export.js'),
  ]);
  global.document = {
    activeElement: null,
    createElement: tag => new FixtureNode(tag),
    createTextNode: text => new FixtureNode('#text', text),
  };

  const currentSource = {
    id: 'fictional-current-undated-source',
    publisher: 'Fictional Harbor Desk', outlet: 'Fictional Harbor Desk',
    title: 'Current fictional harbor notice with a complete title',
    url: 'https://fictional.example.test/current-harbor-notice',
  };
  const currentStory = {
    id: 'fictional-current-undated-story',
    title: 'A fictional harbor service update',
    topic_label: 'Fictional harbor service',
    asked: 'What changed in the fictional harbor service?',
    made_date: 'Nov 21, 2026', duration_minutes: 3,
    sources: [{ ...currentSource, number: 1 }],
    chapters: [{ id: 'current-harbor-update', title: 'Current harbor notice', sentences: [{
      text: 'The current fictional notice keeps the harbor open through the weekend.',
      source_ids: [currentSource.id],
    }] }],
  };

  const topicContainer = new FixtureNode('main');
  const openedSources = [];
  renderTopicDetail(topicContainer, {
    topic: { label: 'Fictional harbor service' },
    sources: [currentSource],
    items: [{
      edition: { local_date: '2026-11-21' },
      section: {
        id: 'current-harbor-update', heading: 'A fictional harbor service update',
        source_ids: [currentSource.id], sources: [currentSource],
      },
    }],
    actions: { openSource: id => openedSources.push(id) },
  });
  const topicCard = topicContainer.querySelector('.topic-detail-story');
  const topicSourceAction = find(topicCard.querySelector('.topic-detail-sources'), node => node.tagName === 'BUTTON')[0];
  topicSourceAction.click();

  const episodeContainer = new FixtureNode('main');
  renderEpisodePage(episodeContainer, currentStory);
  const citation = episodeContainer.querySelector('.episode-citation');
  const sourceSummary = citation.querySelector('summary');
  const sourceDate = citation.querySelector('time');
  const sourceLink = citation.querySelector('a');
  const transcript = buildPlainTranscript(currentStory);
  const transcriptSourceLine = transcript.split('\n').find(line => line.startsWith('[1] '));

  assert.equal(openedSources[0], currentSource.id,
    'the Topic source action opens the current source ID');
  assert.equal(topicSourceAction.getAttribute('aria-label'),
    'Open source: Fictional Harbor Desk, Current fictional harbor notice with a complete title',
    'the Topic source action uses the current publisher/title and does not invent a date');
  assert.equal(sourceSummary.getAttribute('aria-label'),
    'Source 1, Fictional Harbor Desk · Current fictional harbor notice with a complete title',
    'the Episode citation has an accessible current-source name without an invented date');
  assert.equal(sourceLink.getAttribute('aria-label'),
    'Open source: Fictional Harbor Desk, Current fictional harbor notice with a complete title');
  assert.equal(sourceLink.href, currentSource.url,
    'the Episode source action keeps the current safe HTTPS URL');
  assert.match(transcript, /The current fictional notice keeps the harbor open through the weekend\. \[1\]/);
  assert.doesNotMatch(transcript, /Published|Updated|Invalid Date|undefined/,
    'transcript export does not invent publication or update metadata');

  assert.deepEqual({
    topicDate: topicCard.querySelector('.topic-detail-date').textContent,
    episodeDate: sourceDate.textContent,
    transcriptSourceLine,
  }, {
    topicDate: 'Date not listed',
    episodeDate: 'Date not listed',
    transcriptSourceLine: '[1] Fictional Harbor Desk — Current fictional harbor notice with a complete title (Date not listed)',
  }, 'Topic, Episode and transcript expose the same honest missing-source-date fallback');
  console.log('Missing-source-date fixture: Topic, Episode and transcript date labels and current HTTPS source identity pass.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
