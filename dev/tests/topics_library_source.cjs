require('../test_guard.cjs').enforceTestEnvironment();

// Fictional-data coverage for Topics, explanation/source returns and the Library stack.
const assert = require('node:assert/strict');
const auditionManifest = require('./fixtures/audition_manifest.json');
const chromium = process.env.BRIEFING_TOPICS_SOURCE_FIXTURE_ONLY === '1' ? null : require('playwright').chromium;
const fs = require('node:fs');
const path = require('node:path');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';

async function screenshot(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name) });
}

class FixtureNode {
  constructor(tagName, text = '') {
    this.tagName = tagName.toUpperCase();
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
  querySelector(selector) {
    const className = selector.startsWith('.') ? selector.slice(1) : '';
    const tagName = className ? '' : selector.toUpperCase();
    return findFixtureNodes(this, node => className
      ? node.className.split(/\s+/u).includes(className)
      : node.tagName === tagName)[0] || null;
  }
  click() { for (const callback of this.listeners.click || []) callback({ preventDefault() {} }); }
}

function findFixtureNodes(root, predicate) {
  return [root, ...root.children.flatMap(child => findFixtureNodes(child, predicate).slice(0))]
    .filter(predicate);
}

async function runSourceFixture() {
  const { renderTopicDetail } = await import('../../src/topics/topic-detail.js');
  const exporter = await import('../../src/episodes/export.js');
  const { toStoryModel } = await import('../../src/edition/story-model.js');
  global.document = {
    createElement: tag => new FixtureNode(tag),
    createTextNode: text => new FixtureNode('#text', text),
  };
  const withdrawnSource = {
    id: 'withdrawn-source', withdrawn: true, publisher: 'Fictional Transit Desk',
    title: 'Withdrawn timetable claim from an earlier revision',
    published_at: '2026-11-16', url: 'https://fictional.example.test/withdrawn',
  };
  const currentSourceA = {
    id: 'current-source-a', publisher: 'Fictional Transit Desk',
    title: 'Corrected fictional timetable notice with the complete current title',
    published_at: '2026-11-18', updated_at: '2026-11-20',
    url: 'https://fictional.example.test/current-notice',
  };
  const currentSourceB = {
    id: 'current-source-b', publisher: 'Fictional Review Desk',
    title: 'Current fictional route review', published_at: '2026-11-19',
    url: 'https://fictional.example.test/current-review',
  };
  const currentSourceC = {
    id: 'current-source-c', publisher: 'Fictional Harbor Office',
    title: 'Current fictional crossing notice', published_at: '2026-11-20',
    url: 'https://fictional.example.test/current-crossing',
  };
  const staleSameIdSourceA = {
    ...currentSourceA, title: 'Superseded fictional timetable notice', updated_at: '2026-11-21',
    url: 'https://fictional.example.test/superseded-notice',
  };
  const staleSource = {
    id: 'stale-source', withdrawn: true, publisher: 'Fictional Archive Desk',
    title: 'Withdrawn fictional source for a superseded claim',
    published_at: '2026-11-17', url: 'https://fictional.example.test/stale',
  };
  // The refreshed source registry deliberately puts B before A; the corrected
  // story below cites A then B, and embeds B before an old A record.
  const refreshedSources = [withdrawnSource, currentSourceB, currentSourceA, currentSourceC, staleSource];
  const refreshedSourceById = new Map(refreshedSources.map(source => [source.id, source]));
  const openedSources = [];
  const container = new FixtureNode('main');
  renderTopicDetail(container, {
    topic: { label: 'Fictional transit', sourcePhrase: 'coastal transit' },
    sources: refreshedSources,
    items: [
      { edition: {
        id: 'fictional-corrected-daily', revision_id: 'fictional-corrected-daily-r2',
        local_date: '2026-11-21', corrections: [{
          severity: 'material', previous_revision_id: 'fictional-corrected-daily-r1',
          revision_id: 'fictional-corrected-daily-r2', section_ids: ['corrected-transit-story'],
          corrected_at: '2026-11-21T15:00:00Z',
          what_changed: 'Earlier wording said the terminal was closed.',
        }],
      }, section: {
        id: 'corrected-transit-story',
        heading: 'Corrected fictional timetable for the coastal route',
        source_ids: ['current-source-a', 'current-source-b'],
        sources: [withdrawnSource, currentSourceB, staleSameIdSourceA],
        why_it_matters: 'The updated timetable changes a connection window.',
      } },
      { edition: { local_date: '2026-11-20' }, section: {
        heading: 'The harbor posts a second current crossing update',
        source_ids: ['current-source-c', 'stale-source'],
        sources: [staleSource, currentSourceC],
        why_it_matters: 'The posted crossing time is the current one.',
      } },
      { edition: { local_date: '2026-11-20' }, section: {
        heading: 'A current harbor review keeps the published route open',
        source_ids: ['current-source-b'],
        sources: [currentSourceB],
        why_it_matters: 'The review confirms the route remains available.',
      } },
    ],
    actions: { openSource: id => {
      const source = refreshedSourceById.get(id);
      openedSources.push(source && { id, title: source.title, published_at: source.published_at, url: source.url });
    } },
  });

  const pageText = container.textContent;
  assert.deepEqual(refreshedSources.filter(source => ['current-source-a', 'current-source-b'].includes(source.id))
    .map(source => source.id), ['current-source-b', 'current-source-a'],
  'the global registry order differs from the current story citation order');
  assert.match(pageText, /Corrected fictional timetable for the coastal route/);
  assert.match(pageText, /Corrected fictional timetable notice with the complete current title/);
  assert.match(pageText, /Current fictional route review/);
  assert.match(pageText, /Current fictional crossing notice/);
  assert.match(pageText, /current harbor review keeps the published route open/);
  assert.doesNotMatch(pageText, /Withdrawn timetable claim|fictional\.example\.test\/(?:withdrawn|superseded-notice|stale)|Superseded fictional timetable notice|Withdrawn fictional source|Earlier wording said the terminal was closed/);
  assert.match(pageText, /Published Nov 18, 2026/);
  assert.match(pageText, /Corrected Nov 21, 2026/);
  const sourceList = container.querySelector('.topic-detail-sources');
  const sourceButtons = findFixtureNodes(sourceList, node => node.tagName === 'BUTTON');
  assert.deepEqual(sourceButtons.map(button => button.getAttribute('aria-label')), [
    'Open source: Fictional Transit Desk, Corrected fictional timetable notice with the complete current title',
    'Open source: Fictional Review Desk, Current fictional route review',
  ], 'source actions have accessible names built from their current publisher and title');
  assert.equal(sourceButtons[0].getAttribute('data-source'), 'current-source-a',
    'the corrected current citation carries its source ID for focus restoration after returning from the source page');
  assert.ok(sourceButtons.every(button => button.type === 'button' && button.getAttribute('role') === null),
    'source actions use native button semantics for keyboard focus and Enter/Space activation');
  assert.ok(sourceButtons.every(button => !/^https?:\/\//iu.test(button.getAttribute('aria-label') || '')),
    'source action names do not use a bare URL');
  const correctedStoryModel = toStoryModel({
    id: 'fictional-corrected-story-model', revision_id: 'fictional-story-r2',
    headline: 'Corrected fictional timetable for the coastal route',
    body: 'The current fictional timetable keeps the route open.',
    corrected: true, corrected_at: '2026-11-21T15:00:00Z',
    source_ids: ['current-source-a'], primary_source_id: 'current-source-a',
  }, { sources: [withdrawnSource, currentSourceA] });
  assert.equal(sourceButtons[0].getAttribute('aria-label'),
    `Open source: ${correctedStoryModel.primary_source.outlet}, ${correctedStoryModel.primary_source.title}`,
    'Topic action label matches the corrected story model publisher and title');
  assert.equal(refreshedSourceById.get('current-source-a').url, correctedStoryModel.primary_source.url,
    'Topic and corrected story resolve the same current source ID to one safe URL');
  assert.equal(correctedStoryModel.sources.length, 1, 'the corrected story model keeps only its current cited source');
  assert.ok(!JSON.stringify(correctedStoryModel).includes(withdrawnSource.title),
    'withdrawn source history is absent from the corrected story model');

  const longTitle = 'A detailed review of fictional coastal timetable reliability during scheduled harbor infrastructure work across the north channel and its neighboring passenger connections';
  const longTitleContainer = new FixtureNode('main');
  renderTopicDetail(longTitleContainer, {
    topic: { label: 'Fictional transit' },
    sources: [{ id: 'current-long-title', publisher: 'Fictional Harbor Office', title: longTitle,
      published_at: '2026-11-20', url: 'https://fictional.example.test/long-title' }],
    items: [{ edition: { local_date: '2026-11-21' }, section: {
      id: 'long-title-story', heading: 'A current fictional harbor update',
      source_ids: ['current-long-title'], why_it_matters: 'It changes a passenger connection.',
    } }],
  });
  const longTitleButton = findFixtureNodes(longTitleContainer.querySelector('.topic-detail-sources'), node => node.tagName === 'BUTTON')[0];
  assert.equal(longTitleButton.textContent, `Fictional Harbor Office · ${longTitle}`,
    'a long current source title remains complete in the visible Topic action');
  assert.equal(longTitleButton.getAttribute('aria-label'), `Open source: Fictional Harbor Office, ${longTitle}`,
    'the accessible Topic action name keeps the complete current title');
  const sourceActionCSS = fs.readFileSync(path.join(__dirname, '../../styles/calm.css'), 'utf8')
    .match(/\.topic-detail-sources button\{([^}]*)\}/u)?.[1] || '';
  assert.match(sourceActionCSS, /min-height:\s*44px/u, 'source actions keep a 44px minimum target as text grows');
  assert.match(sourceActionCSS, /overflow-wrap:\s*anywhere/u, 'source labels can wrap at 390px with enlarged text');
  assert.doesNotMatch(sourceActionCSS, /(?:^|;)\s*(?:height|max-height|text-overflow|white-space|overflow):/u,
    'source label styling does not fix its height or clip enlarged text');

  const visibleClaimCards = findFixtureNodes(container, node => node.className === 'topic-detail-story')
    .filter(card => card.querySelector('h3')?.querySelector('a'));
  assert.equal(visibleClaimCards.length, 3, 'all three current daily claims remain visible');
  assert.deepEqual(visibleClaimCards.map(card => card.querySelector('.topic-detail-date').textContent), [
    'Published Nov 18, 2026', 'Published Nov 20, 2026', 'Published Nov 19, 2026',
  ], 'each claim retains its current source publication date after refresh');
  const correctedDate = visibleClaimCards[0].querySelector('.topic-detail-date');
  const correctionDate = visibleClaimCards[0].querySelector('.topic-detail-corrected');
  assert.equal(correctedDate.tagName, 'P');
  assert.equal(correctedDate.getAttribute('aria-hidden'), null, 'the original publication date remains exposed as readable text');
  assert.equal(correctionDate.textContent, 'Corrected Nov 21, 2026');
  assert.equal(correctionDate.getAttribute('aria-hidden'), null, 'the correction date remains exposed as readable text');

  const parityTranscript = exporter.buildPlainTranscript({
    id: 'fictional-topic-export-parity', title: 'Fictional coastal transit update',
    revision_id: 'fictional-corrected-daily-r2',
    sources: [currentSourceA, currentSourceB, withdrawnSource].map((source, index) => ({
      ...source, outlet: source.publisher, number: index + 1,
    })),
    corrections: [{
      severity: 'material', previous_revision_id: 'fictional-corrected-daily-r1',
      revision_id: 'fictional-corrected-daily-r2', chapter_id: 'corrected-transit-story',
      corrected_at: '2026-11-21T15:00:00Z',
    }],
    chapters: [
      { id: 'corrected-transit-story', title: 'Corrected fictional timetable for the coastal route',
        sentences: [{ text: 'The current fictional timetable keeps the coastal route open.', source_ids: ['current-source-a'] }] },
      { id: 'withdrawn-transit-story', title: 'Superseded claim that the coastal route closed',
        status: 'withdrawn', sentences: [{
          text: 'Earlier wording said the coastal route had closed.', source_ids: ['withdrawn-source'],
        }] },
    ],
  });
  const exportedCurrentSource = parityTranscript.match(/^\[1\].*\(Published (.+)\)$/mu)?.[1];
  const exportedCorrection = parityTranscript.match(/^Corrected [A-Z][a-z]{2} \d{1,2}, \d{4}$/mu)?.[0];
  assert.equal(`Published ${exportedCurrentSource}`, correctedDate.textContent,
    'Topic and transcript export show the same current publication date');
  assert.equal(exportedCorrection, correctionDate.textContent,
    'Topic and transcript export show the same correction date');
  assert.match(parityTranscript,
    /^\[1\] Fictional Transit Desk — Corrected fictional timetable notice with the complete current title \(Published Nov 18, 2026\)$/mu,
    'transcript source row carries the same current publisher, complete title and publication date');
  assert.match(parityTranscript, /Story withdrawn/);
  assert.doesNotMatch(parityTranscript, /Superseded claim that the coastal route closed|Earlier wording said the coastal route had closed|Withdrawn timetable claim from an earlier revision/,
    'transcript export omits withdrawn claim text and historical source titles');
  for (const card of visibleClaimCards) {
    const actions = findFixtureNodes(card.querySelector('.topic-detail-sources'), node => node.tagName === 'BUTTON');
    assert.ok(actions.length > 0, `visible claim ${card.querySelector('h3').textContent} has a source action`);
    for (const action of actions) action.click();
  }
  assert.deepEqual(openedSources, [
    { id: 'current-source-a', title: 'Corrected fictional timetable notice with the complete current title', published_at: '2026-11-18', url: 'https://fictional.example.test/current-notice' },
    { id: 'current-source-b', title: 'Current fictional route review', published_at: '2026-11-19', url: 'https://fictional.example.test/current-review' },
    { id: 'current-source-c', title: 'Current fictional crossing notice', published_at: '2026-11-20', url: 'https://fictional.example.test/current-crossing' },
    { id: 'current-source-b', title: 'Current fictional route review', published_at: '2026-11-19', url: 'https://fictional.example.test/current-review' },
  ], 'each visible claim action resolves to the refreshed source title, date and URL identity');

  const updateOnlySource = {
    id: 'current-update-only', publisher: 'Fictional Notice Desk',
    title: 'Current fictional notice with an update date only',
    updated_at: '2026-11-22', url: 'https://fictional.example.test/update-only',
  };
  const updateOnlyContainer = new FixtureNode('main');
  renderTopicDetail(updateOnlyContainer, {
    topic: { label: 'Fictional service notices' }, sources: [updateOnlySource],
    items: [{ edition: { local_date: '2026-11-21' }, section: {
      id: 'update-only-story', heading: 'A fictional service notice was updated',
      source_ids: [updateOnlySource.id], sources: [updateOnlySource],
    } }],
    actions: { openSource: id => assert.equal(id, updateOnlySource.id) },
  });
  const updateOnlyCard = updateOnlyContainer.querySelector('.topic-detail-story');
  const updateOnlyDate = updateOnlyCard.querySelector('.topic-detail-date');
  assert.equal(updateOnlyDate.textContent, 'Updated Nov 22, 2026', 'an update-only source has a truthful date label');
  assert.equal(updateOnlyDate.getAttribute('aria-hidden'), null, 'the update date is exposed as readable text');
  assert.doesNotMatch(updateOnlyContainer.textContent, /Published|undefined|Invalid Date/,
    'a missing publication date is neither invented nor rendered as a broken date');
  const updateOnlyAction = findFixtureNodes(updateOnlyCard.querySelector('.topic-detail-sources'), node => node.tagName === 'BUTTON')[0];
  assert.equal(updateOnlyAction.getAttribute('aria-label'),
    'Open source: Fictional Notice Desk, Current fictional notice with an update date only',
    'the source action has a useful accessible name without relying on its URL');
  updateOnlyAction.click();

  const correctedWithoutPublication = {
    id: 'corrected-without-publication', publisher: 'Fictional Notice Desk',
    title: 'Current fictional notice with no publication date',
    updated_at: '2026-11-20', url: 'https://fictional.example.test/no-publication',
  };
  const correctionWithoutPublicationDate = {
    severity: 'material', previous_revision_id: 'fictional-date-r1',
    revision_id: 'fictional-date-r2', section_ids: ['fictional-date-story'],
    corrected_at: '2026-11-21T11:00:00Z',
  };
  const missingPublicationContainer = new FixtureNode('main');
  renderTopicDetail(missingPublicationContainer, {
    topic: { label: 'Fictional notices' }, sources: [correctedWithoutPublication, withdrawnSource],
    items: [
      { edition: { revision_id: 'fictional-date-r2', local_date: '2026-11-21', corrections: [correctionWithoutPublicationDate] }, section: {
        id: 'fictional-date-story', heading: 'A corrected fictional notice',
        source_ids: [correctedWithoutPublication.id], sources: [correctedWithoutPublication],
      } },
      { edition: { revision_id: 'fictional-date-r2', corrections: [correctionWithoutPublicationDate] }, section: {
        id: 'withdrawn-date-story', status: 'withdrawn', heading: 'Superseded fictional notice claim',
        source_ids: [withdrawnSource.id], sources: [withdrawnSource],
      } },
    ],
  });
  const missingPublicationCard = findFixtureNodes(missingPublicationContainer, node => node.className === 'topic-detail-story')[0];
  assert.equal(missingPublicationCard.querySelector('.topic-detail-date').textContent, 'Publication date not listed',
    'a corrected source without publication metadata does not borrow its update date as publication');
  assert.equal(missingPublicationCard.querySelector('.topic-detail-corrected').textContent, 'Corrected Nov 21, 2026',
    'the correction date remains distinct when the source publication date is missing');
  assert.doesNotMatch(missingPublicationContainer.textContent, /Superseded fictional notice claim|Published Nov 20, 2026/,
    'the corrected Topic view omits withdrawn text and avoids inventing a publication date');
  const missingPublicationTranscript = exporter.buildPlainTranscript({
    title: 'Fictional notice date fixture', revision_id: 'fictional-date-r2',
    sources: [{ ...correctedWithoutPublication, outlet: correctedWithoutPublication.publisher, number: 1 }, withdrawnSource],
    corrections: [{ ...correctionWithoutPublicationDate, chapter_id: 'fictional-date-story' }],
    chapters: [
      { id: 'fictional-date-story', title: 'A corrected fictional notice', sentences: [{
        text: 'The current fictional notice changes the service window.', source_ids: [correctedWithoutPublication.id],
      }] },
      { id: 'withdrawn-date-story', title: 'Superseded fictional notice claim', status: 'withdrawn', sentences: [{
        text: 'The obsolete notice claimed the service was closed.', source_ids: [withdrawnSource.id],
      }] },
    ],
  });
  assert.match(missingPublicationTranscript, /Corrected Nov 21, 2026/,
    'transcript export retains the correction date when publication is unverified');
  assert.match(missingPublicationTranscript,
    /\[1\] Fictional Notice Desk — Current fictional notice with no publication date \(Updated Nov 20, 2026\)(?:\n|$)/,
    'transcript keeps the truthful source update date distinct from publication and correction dates');
  assert.match(missingPublicationTranscript, /Story withdrawn/);
  assert.doesNotMatch(missingPublicationTranscript, /Published Nov 20, 2026|Superseded fictional notice claim|obsolete notice claimed the service was closed/,
    'transcript export excludes a false publication date and withdrawn claim text');

  const publicationOnlySource = {
    id: 'publication-only', publisher: 'Fictional Transit Desk',
    title: 'Current fictional notice with no update date', published_at: '2026-11-18',
    url: 'https://fictional.example.test/publication-only',
  };
  const missingCorrectionDate = {
    severity: 'material', previous_revision_id: 'fictional-date-r1',
    revision_id: 'fictional-date-r2', section_ids: ['publication-only-story'],
  };
  const publicationOnlyContainer = new FixtureNode('main');
  renderTopicDetail(publicationOnlyContainer, {
    topic: { label: 'Fictional notices' }, sources: [publicationOnlySource],
    items: [{ edition: { revision_id: 'fictional-date-r2', corrections: [missingCorrectionDate] }, section: {
      id: 'publication-only-story', heading: 'A current fictional notice', source_ids: [publicationOnlySource.id],
    } }],
  });
  const publicationOnlyCard = publicationOnlyContainer.querySelector('.topic-detail-story');
  assert.equal(publicationOnlyCard.querySelector('.topic-detail-date').textContent, 'Published Nov 18, 2026');
  assert.equal(publicationOnlyCard.querySelector('.topic-detail-corrected'), null,
    'a missing correction date is not invented from a source publication date');
  const publicationOnlyTranscript = exporter.buildPlainTranscript({
    title: 'Fictional notice date fixture', revision_id: 'fictional-date-r2',
    sources: [{ ...publicationOnlySource, outlet: publicationOnlySource.publisher, number: 1 }],
    corrections: [{ ...missingCorrectionDate, chapter_id: 'publication-only-story' }],
    chapters: [{ id: 'publication-only-story', title: 'A current fictional notice', sentences: [{
      text: 'The current fictional notice remains in effect.', source_ids: [publicationOnlySource.id],
    }] }],
  });
  assert.match(publicationOnlyTranscript, /\[1\].*\(Published Nov 18, 2026\)/);
  assert.doesNotMatch(publicationOnlyTranscript, /Corrected|Updated|undefined|Invalid Date/,
    'transcript export retains publication date without inventing missing update or correction dates');

  const withdrawnContainer = new FixtureNode('main');
  const openedWithdrawnSources = [];
  const withdrawnItem = { section: {
    heading: 'Superseded fictional headline: the terminal is closed',
    body: 'Superseded body text says the terminal is closed.',
    why_it_matters: 'Superseded explanation repeats that the terminal is closed.',
    withdrawn: true, withdrawn_at: '2026-11-21T11:00:00Z',
    source_ids: ['withdrawn-source', 'current-source-a'],
    sources: [withdrawnSource, currentSourceA],
  } };
  renderTopicDetail(withdrawnContainer, {
    topic: { label: 'Fictional transit', sourcePhrase: 'coastal transit' },
    sources: [withdrawnSource, currentSourceA],
    items: [withdrawnItem],
    actions: { openSource: id => openedWithdrawnSources.push(id) },
  });
  const withdrawnHeading = withdrawnContainer.querySelector('.topic-detail-story').querySelector('h3');
  assert.equal(withdrawnHeading.textContent, 'Story withdrawn');
  assert.equal(withdrawnHeading.querySelector('a'), null, 'a withdrawn story cannot open superseded content');
  const withdrawnText = withdrawnContainer.textContent;
  assert.doesNotMatch(withdrawnText, /Superseded fictional headline|Superseded body text|Superseded explanation/);
  assert.match(withdrawnText, /Corrected fictional timetable notice with the complete current title/);
  assert.doesNotMatch(withdrawnText, /Withdrawn timetable claim from an earlier revision/);
  const withdrawnSourceActions = findFixtureNodes(withdrawnContainer.querySelector('.topic-detail-sources'), node => node.tagName === 'BUTTON');
  assert.equal(withdrawnSourceActions.length, 1);
  assert.deepEqual(withdrawnSourceActions.map(action => action.getAttribute('aria-label')),
    ['Open source: Fictional Transit Desk, Corrected fictional timetable notice with the complete current title'],
    'a withdrawn source ID is not actionable, while the current source retains its full accessible name');
  withdrawnSourceActions[0].click();
  assert.deepEqual(openedWithdrawnSources, ['current-source-a'], 'withdrawal preserves only the current source action');
  console.log('Topic source fixture: current links and update date remain correct; withdrawn history is excluded.');
}

async function assert200PercentFit(page, label) {
  await page.setViewportSize({ width: 195, height: 422 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true,
    `${label} fits a 200% zoom viewport`);
  await page.setViewportSize({ width: 390, height: 844 });
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    await page.route('**/audition-audio/manifest.json', route => route.fulfill({ json: auditionManifest }));
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/profile', route => route.fulfill({ status: 404, json: {} }));
    await page.route('**/api/edition**', route => route.fulfill({ status: 404, json: {} }));
    await page.route('**/api/episodes', route => route.fulfill({ json: { jobs: [
      { job_id: 'episode-fixture-ready', topic: 'Fictional coastal transit', title: 'A fictional coastal transit update', length: 10, status: 'done' },
      { job_id: 'episode-fixture-making', topic: 'Fictional harbor timetable', length: 3, status: 'writing' },
    ], left_today: 2 } }));
    await page.route('**/api/status', route => route.fulfill({ json: {
      last_run_at: '2026-11-17T11:02:00Z', ok: true, stories_written: 4, feeds_disabled: [],
    } }));
    await page.goto(`${baseURL}/#source/fed-payments`, { waitUntil: 'networkidle' });
    await page.locator('.source-title').waitFor();
    assert.match(await page.locator('.source-reviewed').innerText(), /November 16, 2026/, 'Direct source route formats the ISO review-date fallback correctly');
    await page.goto(`${baseURL}/#topics`, { waitUntil: 'networkidle' });
    await assert200PercentFit(page, 'Topics');
    const topicActions = await page.locator('.topics-page button, .topics-page textarea, .topics-page a').evaluateAll(items => items.map(item => ({
      label: item.getAttribute('aria-label') || item.innerText || item.tagName,
      height: item.getBoundingClientRect().height,
    })));
    assert.ok(topicActions.every(item => item.height >= 44), `Every Topics action has a 44px target: ${JSON.stringify(topicActions)}`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Topics fits at 390px');
    await screenshot(page, 'topics-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'topics-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.topics-page h1, .topics-page h2, .topics-page p, .topics-page a, .topics-page button, .topics-page label, .topics-page textarea').evaluateAll(items => {
      for (const item of items) item.style.setProperty('font-size', `${Math.round(parseFloat(getComputedStyle(item).fontSize) * 1.25)}px`, 'important');
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, 'Topics fits at 390px with larger text');
    await screenshot(page, 'topics-large-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'topics-large-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('.topics-page *').evaluateAll(items => items.forEach(item => item.style.removeProperty('font-size')));

    await page.locator('[data-nav="library"]').click();
    await page.locator('.library-filters').waitFor();
    await page.locator('.library-filters button').filter({ hasText: 'Saved' }).click();
    await screenshot(page, 'library-empty-390-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'library-empty-390-dark.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.setViewportSize({ width: 1280, height: 900 });
    await screenshot(page, 'library-empty-desktop-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'library-empty-desktop-dark.png');
    await page.emulateMedia({ colorScheme: 'light' }); await page.setViewportSize({ width: 390, height: 844 });
    assert.match(await page.locator('.library-empty').innerText(), /Nothing saved yet/i);
    assert.match(await page.locator('.library-device-note').innerText(), /on this device/i);
    await page.locator('.library-empty-action').click();
    await page.locator('.library-filters button[aria-pressed="true"]').waitFor();
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').innerText(), 'Briefings', 'Empty state offers a path to briefings');
    const editionDates = page.locator('.library-briefing-list li');
    assert.ok(await editionDates.count() > 0, 'Briefings lists dated editions');
    assert.match(await editionDates.first().innerText(), /\d+ stories · \d+ min read/, 'Each date includes story count and reading time');
    await page.getByRole('button', { name: 'Episodes', exact: true }).click();
    await page.locator('.earlier-episodes .ask-episode-card').first().waitFor();
    if (process.env.BRIEFING_SHOT_DIR) {
      await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
      await page.emulateMedia({ colorScheme: 'light' });
      await screenshot(page, 'earlier-episodes-large-text-light.png');
      await page.emulateMedia({ colorScheme: 'dark' });
      await screenshot(page, 'earlier-episodes-large-text-dark.png');
      await page.emulateMedia({ colorScheme: 'light' });
      await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
    }
    assert.deepEqual(await page.locator('.episode-length-badge').allTextContents(),
      ['Requested: 10 min', 'Requested: 3 min']);
    assert.deepEqual(await page.locator('.episode-status-chip').allTextContents(), ['Transcript ready', 'Making']);
    const readyEarlierRow = page.locator('.earlier-episodes .ask-episode-card').first();
    assert.equal(await readyEarlierRow.getAttribute('href'), '#episode/episode-fixture-ready',
      'Earlier keeps the finished episode row linked to its episode page');
    assert.match(await readyEarlierRow.innerText(), /Requested: 10 min[\s\S]*Transcript ready/,
      'Earlier keeps the requested length and transcript state when audio is unavailable');
    await screenshot(page, 'earlier-episodes-light.png');
    await page.emulateMedia({ colorScheme: 'dark' }); await screenshot(page, 'earlier-episodes-dark.png'); await page.emulateMedia({ colorScheme: 'light' });
    await page.locator('[data-nav="topics"]').click();
    await page.locator('[data-nav="library"]').click();
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').innerText(), 'Episodes', 'Earlier remembers its segment after leaving and returning');
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').innerText(), 'Episodes', 'Earlier segment survives reload');
    await page.getByRole('button', { name: 'Saved', exact: true }).click();
    assert.match(await page.locator('.library-empty').innerText(), /Nothing saved yet/i);
    await page.locator('.library-empty-action').click();
    await page.locator('[data-nav="topics"]').click();

    const topic = page.locator('.topic-dive-link').first();
    assert.equal(await topic.innerText(), 'Learn more');
    assert.match(await topic.getAttribute('aria-label'), /Learn more about/);
    await topic.click();
    await page.locator('.deep-dive-page h1').waitFor();
    await assert200PercentFit(page, 'Explanation');
    assert.match(await page.locator('.deep-dive-topline').innerText(), /Sample · fictional/i);
    assert.match(await page.locator('.deep-dive-meta').innerText(), /min read · Reviewed/);
    assert.doesNotMatch(await page.locator('.deep-dive-meta').innerText(), /min listen/, 'Explanation listen time appears in its Listen control');
    assert.match(await page.locator('.deep-dive-listen').innerText(), /Listen · about \d+ min/);
    assert.equal(await page.locator('.reader-back').count(), 1, 'Explanation uses one Back control');

    const diveSource = page.locator('.source-internal-link').first();
    const sourceId = await diveSource.getAttribute('data-source');
    await diveSource.click();
    await page.locator('.source-title').waitFor();
    await assert200PercentFit(page, 'Source');
    assert.equal(await page.locator('.reader-back').count(), 1, 'Source uses one Back control');
    assert.match(await page.locator('.reader-back').innerText(), /Explanation/);
    assert.equal(await page.getByRole('heading', { name: 'What this supports' }).count(), 1);
    assert.equal(await page.getByRole('heading', { name: 'Limits' }).count(), 0, 'Source details omit the legacy negative heading');
    assert.match(await page.locator('.source-published').innerText(), /Document date/);
    assert.match(await page.locator('.source-reviewed').innerText(), /Reviewed/);
    assert.equal(await page.locator('.source-sample-tag').count(), 0, 'A real source is not mislabeled as fictional sample content');
    assert.equal(await page.getByRole('link', { name: /Open original/ }).count(), 1, 'Only a validated HTTPS source gets an original link');
    await page.locator('.reader-back').click();
    await page.locator('.deep-dive-page').waitFor();
    assert.equal(await page.locator(`[data-source="${sourceId}"]`).count(), 1, 'Source Back returns to its source row');
    await page.locator('.deep-dive-save').click();

    await page.locator('.reader-back').click();
    await page.locator('.topics-heading').waitFor();
    await page.evaluate(() => {
      const state = JSON.parse(localStorage.getItem('briefing:v1:state'));
      state.savedIds = [...new Set([...state.savedIds, 'daily-sample-001', 'weekly-sample-001', 'dive-banking-deposits', 'dive-digital-authentication', 'dive-insurance-claims', 'dive-sports-time'])];
      localStorage.setItem('briefing:v1:state', JSON.stringify(state));
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('[data-nav="library"]').click();
    await page.locator('.library-filters').waitFor();
    await page.locator('.library-filters button').filter({ hasText: 'Saved' }).click();
    await assert200PercentFit(page, 'Library');
    assert.ok(await page.locator('.library-list .library-item-link').count() >= 5, 'Saved tab lists multiple fictional saved explanations and editions');
    const savedExplanation = page.locator('.library-item-link').last();
    await savedExplanation.scrollIntoViewIfNeeded();
    const libraryY = await page.evaluate(() => scrollY);
    await savedExplanation.click();
    await page.locator('.deep-dive-page').waitFor();
    assert.match(await page.locator('.reader-back').innerText(), /Library/);
    await page.locator('.source-internal-link').first().click();
    await page.locator('.source-title').waitFor();
    assert.match(await page.locator('.reader-back').innerText(), /Explanation/, 'Source opened from a saved Library item names Explanation as its parent');
    await page.locator('.reader-back').click();
    await page.locator('.deep-dive-page').waitFor();
    await page.locator('.reader-back').click();
    await page.locator('.library-filters').waitFor();
    assert.equal(await page.locator('.library-filters button[aria-pressed="true"]').innerText(), 'Saved', 'Library filter survives a pushed item return');
    await page.waitForFunction(y => Math.abs(scrollY - y) < 5, libraryY);
    await page.goto(`${baseURL}/#today`, { waitUntil: 'networkidle' });
    await page.locator('[data-nav=topics]').click();
    await page.locator('.settings-link').click();
    await page.locator('.preferences-voice').waitFor();
    await page.locator('.settings-last-updated').filter({ hasText: 'Updated 6:02 am' }).waitFor();
    await page.locator('.narrator-sample').first().waitFor();
    await assert200PercentFit(page, 'Settings');
    assert.equal(await page.locator('.settings-link').isVisible(), false, 'Settings hides its redundant header link');
    assert.equal(await page.locator('.narrator-sample').count(), 6, 'Settings shows the six audition samples');
    assert.equal(await page.locator('.main-nav [aria-current="page"]').count(), 1, 'Settings keeps its owning Topics destination selected');
    assert.equal(await page.locator('.main-nav [aria-current="page"][data-nav="topics"]').count(), 1);
    await page.goto(`${baseURL}/#weekly`, { waitUntil: 'networkidle' });
    await page.locator('.weekly-recap-page').waitFor();
    await assert200PercentFit(page, 'Weekly recap');
    assert.equal(await page.locator('.weekly-recap-header .story-context-line').innerText(), 'Weekly recap · 9–15 Nov');
    assert.equal(await page.locator('.weekly-key-developments li').count(), 6, 'Weekly recap has a concise key developments list');
    assert.ok(await page.locator('.weekly-recap-sources li').count() > 0, 'Weekly recap lists its sources');
    const weeklySource = page.locator('[data-weekly-source]').first();
    await weeklySource.click();
    await page.waitForFunction(() => location.hash.startsWith('#source/'));
    await page.locator('.source-title').waitFor();
    await page.locator('.reader-back').filter({ hasText: 'Weekly recap' }).waitFor();
    assert.match(await page.locator('.reader-back').innerText(), /Weekly recap/, 'Source Back names the recap overview');
    await page.locator('.reader-back').click();
    await page.waitForFunction(() => location.hash === '#weekly');
    await page.locator('.weekly-recap-page').waitFor();
    await page.locator('.weekly-key-developments li button').first().click();
    await page.waitForFunction(() => location.hash === '#edition/daily/daily-sample-001/daily-payments-001');
    await page.locator('.reader-story-header').waitFor();
    assert.equal(new URL(page.url()).hash, '#edition/daily/daily-sample-001/daily-payments-001', 'A key development opens its original daily story');
    assert.match(await page.locator('.reader-sticky .story-progress').innerText(), /Story 1 of 4/);
    assert.match(await page.locator('.reader-back').innerText(), /Weekly recap/);
    await page.locator('.reader-back').click();
    await page.waitForFunction(() => location.hash === '#weekly');
    await page.locator('.weekly-recap-page').waitFor();
    assert.match(await page.locator('.weekly-listen').innerText(), /Listen\s*·\s*about \d+ min/);
    await page.goto(`${baseURL}/#library`, { waitUntil: 'networkidle' });
    await page.locator('.library-filters').waitFor();
    await page.locator('.library-filters button').filter({ hasText: 'Briefings' }).click();
    assert.match(await page.locator('.earlier-weekly-card').innerText(), /Weekly recap\s+9-15 Nov · \d+ stories · \d+ min/, 'Earlier features the latest weekly recap at the top');
    assert.doesNotMatch(await page.locator('.library-list').innerText(), /Five systems, one useful question/, 'The featured recap is not repeated in the archive list');
    const listenPage = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
    listenPage.on('pageerror', error => errors.push(error.message));
    await listenPage.route('**/api/profile', route => route.fulfill({ status: 404, json: {} }));
    await listenPage.route('**/api/edition**', route => route.fulfill({ status: 404, json: {} }));
    await listenPage.addInitScript(() => {
      class StubUtterance { constructor(text) { this.text = text; this.rate = 1; } }
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: StubUtterance, configurable: true });
      Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        paused: false, speaking: false,
        speak(utterance) { setTimeout(() => utterance.onstart?.(), 0); },
        pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { this.paused = false; this.speaking = false; },
      } });
    });
    await listenPage.goto(`${baseURL}/#library`, { waitUntil: 'networkidle' });
    await listenPage.evaluate(() => {
      const key = 'briefing:v1:state';
      const state = JSON.parse(localStorage.getItem(key)) || { selectedIds: ['payments', 'banking', 'digital', 'insurance', 'sports'], savedIds: [] };
      state.savedIds = ['daily-sample-001']; localStorage.setItem(key, JSON.stringify(state));
    });
    await listenPage.evaluate(async () => {
      const key = 'briefing:v1:state'; const state = JSON.parse(localStorage.getItem(key));
      const pack = await (await fetch('/content/editions.json')).json();
      const daily = pack.editions.find(item => item.id === 'daily-sample-001');
      const weekly = pack.editions.find(item => item.id === 'weekly-sample-001');
      state.savedIds.push(weekly.id);
      state.progress = { [daily.id]: { seconds: 252, revisionId: daily.revision_id, chapterId: daily.sections[2].id } };
      state.readingProgress = { [weekly.id]: { storyId: weekly.sections[2].id, paragraphIndex: 0, mode: 'read', updatedAt: new Date().toISOString(), revisionId: weekly.revision_id } };
      localStorage.setItem(key, JSON.stringify(state));
    });
    await listenPage.reload({ waitUntil: 'networkidle' });
    await listenPage.locator('.library-filters button').filter({ hasText: 'Saved' }).click();
    assert.equal(await listenPage.locator('.library-row:has-text("The choices behind the tap") .library-row-progress').innerText(), 'Listened 4:12', 'Library shows the saved listening position');
    assert.equal(await listenPage.locator('.library-row:has-text("Five systems, one useful question") .library-row-progress').innerText(), 'Read to story 3', 'Library shows the saved reading position');
    await listenPage.locator('.library-row:has-text("The choices behind the tap") .library-listen-button').click();
    await listenPage.locator('.mini-player-open strong').waitFor();
    assert.equal(await listenPage.locator('.mini-player-open strong').innerText(), 'The choices behind the tap', 'Saved-item Listen starts through Player’s shared player');
    await listenPage.close();
    assert.deepEqual(errors, []);
    console.log('Topics, explanation sources and Library return state passed.');
  } finally { await browser.close(); }
}

const task = process.env.BRIEFING_TOPICS_SOURCE_FIXTURE_ONLY === '1' ? runSourceFixture() : run();
task.catch(error => { console.error(error); process.exitCode = 1; });
