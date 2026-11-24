require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FixtureNode {
  constructor(tagName, text = '') {
    this.tagName = tagName.toUpperCase();
    this.className = '';
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.isConnected = true;
    this._text = String(text);
    this.classList = {
      add: name => { if (!this.className.split(/\s+/).includes(name)) this.className = `${this.className} ${name}`.trim(); },
      remove: name => { this.className = this.className.split(/\s+/).filter(value => value && value !== name).join(' '); },
    };
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

async function checkJobCardRecordingIdentity(episode, hasCurrentEpisodeRecording, renderEpisodePage) {
  const { recordingSession } = await import('../../src/player.js');
  const root = path.join(__dirname, '../..');
  const source = fs.readFileSync(path.join(root, 'src/app.js'), 'utf8');
  const section = (text, start, end) => {
    const first = text.indexOf(start); const last = text.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `production boundaries exist: ${start}`);
    return text.slice(first, last).replace(/^export /gmu, '');
  };
  const historyPath = path.join(root, 'src/screens/episode-history.js');
  const historySource = fs.existsSync(historyPath) ? fs.readFileSync(historyPath, 'utf8') : source;
  const stateHelper = fs.existsSync(historyPath)
    ? section(historySource, 'export function episodeHistoryState(', 'export function episodeHistoryDismissed(')
    : section(historySource, 'function episodeHistoryState(', 'function episodeHistoryDismissed(');
  const makingPath = path.join(root, 'src/screens/episode-making.js');
  const makingSource = fs.existsSync(makingPath) ? fs.readFileSync(makingPath, 'utf8') : source;
  const renderers = section(source, 'async function loadEpisodeCards(', 'function episodeProfileTopic(')
    + stateHelper
    + (fs.existsSync(makingPath)
      ? section(makingSource, 'function appendDoneCard(', 'export function updateEpisodeMakingView(') +
        'function appendEpisodeDoneCard(page, job) { appendDoneCard(page, job, node, episodeHasReadyAudio, navigate); }'
      : section(source, 'function appendEpisodeDoneCard(', 'async function cancelEpisodeJob('))
    + section(source, 'async function renderEpisodeRoute(', 'function renderEpisodeRouteFailure(');
  const readyHelpers = fs.existsSync(makingPath)
    ? section(makingSource, 'export function episodeHasReadyAudio(', 'function failureCopy(')
    : source.includes('function episodeHasReadyAudio(') ? section(source, 'function episodeHasReadyAudio(', 'async function pollEpisodeJob(') : '';
  const current = { ...episode, revision_id: 'fictional-r2', script_hash: 'fictional-script-r2',
    audio_recorded: true, audio_status: 'recorded', requested_minutes: 10,
    duration_minutes: 2, duration_basis: 'measured rendered audio',
    audio: { status: 'recorded', revision_id: 'fictional-r2', script_hash: 'fictional-script-r2',
      url: '/fictional-current.wav', duration_seconds: 120 } };
  const cases = [
    { name: 'matching current recording', episode: current, ready: true },
    { name: 'stale revision with recorded URL', episode: { ...current, audio: { ...current.audio, revision_id: 'fictional-r1' } } },
    { name: 'stale script with recorded URL', episode: { ...current, audio: { ...current.audio, script_hash: 'fictional-script-r1' } } },
    { name: 'pending nested audio with recorded job', episode: { ...current, audio: { ...current.audio, status: 'pending' } } },
    { name: 'recorded job with pending episode and matching recorded asset', episode: { ...current, audio_status: 'pending' } },
    { name: 'missing current identity', episode: { ...current, revision_id: undefined, script_hash: undefined } },
    { name: 'superseded recording', episode: { ...current, audio: { ...current.audio, superseded: true } } },
    { name: 'pending job with recorded episode', episode: current, jobAudioStatus: 'pending' },
  ];
  const issues = []; const evidence = [];
  for (const control of cases) {
    const id = 'abcdef0123456789abcdef0123456789';
    const job = { job_id: id, jobId: id, status: 'done',
      topic: '  What changed in the fictional notice?  ', length: 10, minutes: 10, mode: { label: 'Deep dive' },
      audio_status: control.jobAudioStatus || 'recorded', episode: control.episode };
    const cards = new FixtureNode('section'); const done = new FixtureNode('main'); const navigations = [];
    const route = new FixtureNode('main');
    const played = []; const fetched = []; let projected;
    const context = vm.createContext({ cards, done, job, hasCurrentEpisodeRecording,
      content: route, location: { hash: `#episode/${id}`, search: '' },
      renderEpisodePage: (container, episode, actions) => {
        projected = episode; renderEpisodePage(container, episode, actions);
      },
      player: { playRecording: episode => played.push(episode) },
      isEpisodeInDailyBriefing: () => false, setEpisodeDailyBriefing: async () => {},
      renderEpisodeRouteFailure: (title, message) => assert.fail(`${title}: ${message}`),
      node: (tag, text, className = '') => Object.assign(new FixtureNode(tag, text ?? ''), { className }),
      link: (text, href, className = '') => Object.assign(new FixtureNode('a', text), { href, className }),
      window: { scrollY: 0 }, navigate: hash => navigations.push(hash), episodeHistorySnapshot: null,
      fetch: async (url, options) => {
        assert.ok(['/api/episodes', `/api/episodes/${id}`].includes(url), 'only the fictional list/detail API is requested');
        assert.equal(options.method || 'GET', 'GET'); fetched.push(url);
        return { ok: true, status: 200, json: async () => url === '/api/episodes' ? { jobs: [job] } : job };
      },
    });
    vm.runInContext(readyHelpers + renderers, context);
    await vm.runInContext('loadEpisodeCards(cards)', context);
    vm.runInContext('appendEpisodeDoneCard(done, job)', context);
    const rows = findNodes(cards, node => node.className === 'ask-episode-card');
    assert.equal(rows.length, 1, `${control.name}: production history renderer completed`);
    const action = findNodes(done, node => node.className === 'episode-read-button')[0];
    assert.ok(action, `${control.name}: the completed transcript has an opening action`);
    action.listeners.click[0]();
    assert.deepEqual(navigations, [`#episode/${id}`], 'the reading action still opens the current episode route');
    await vm.runInContext(`renderEpisodeRoute('episode/${id}')`, context);
    assert.deepEqual(fetched, ['/api/episodes', `/api/episodes/${id}`], 'actual API detail route executes');
    assert.equal(projected.asked, job.topic, 'API projection preserves exact original request including whitespace');
    assert.equal(projected.requested_minutes, 10, 'requested length survives API projection separately');
    assert.equal(projected.duration_minutes, 2, 'projection does not replace measured episode duration with requested length');
    assert.equal(projected.audio_status, control.ready ? 'recorded' : 'pending', 'route projection normalizes status to current recording readiness');
    assert.equal(projected.audio_recorded, Boolean(control.ready), 'API projection uses current revision/hash and status guard');
    const routeListen = findNodes(route, node => node.tagName === 'BUTTON' && node.textContent === 'Listen' && !node.disabled).length;
    const routeMeta = findNodes(route, node => node.className === 'episode-meta')[0].textContent;
    assert.match(route.textContent, /The current notice keeps service open through the weekend\./,
      'audio identity never removes the approved current transcript');
    const row = { name: control.name, history: rows[0].textContent, done: done.textContent, action: action.textContent,
      playerIdentity: recordingSession(control.episode, null).playable, routeListen, routeMeta };
    evidence.push(row);
    if (control.ready) {
      const listen = findNodes(route, node => node.tagName === 'BUTTON' && node.textContent === 'Listen' && !node.disabled)[0];
      assert.ok(listen, 'current matching API recording retains Listen');
      listen.listeners.click[0]();
      assert.equal(played.length, 1, 'current route Listen reaches player exactly once');
      assert.equal(played[0], projected, 'player receives the current projected episode');
      assert.match(routeMeta, /^2 min ·/, 'current recording uses measured length rather than requested length');
      if (!row.history.includes('Ready · Listen') || row.action !== 'Open episode' || !row.playerIdentity || routeListen !== 1) issues.push(`${control.name}: current audio action missing`);
    } else {
      assert.equal(played.length, 0, 'stale/pending API route never reaches player');
      if (row.history.includes('Listen')) issues.push(`${control.name}: history promises stale listening`);
      if (row.action !== 'Open transcript') issues.push(`${control.name}: done card does not offer the transcript`);
      if (!row.history.includes('Requested: 10 min') || !row.done.includes('You chose 10 min')) issues.push(`${control.name}: requested length is not truthful`);
      if (/One narrator|Your episode is ready|\b2 min/.test(row.done)) issues.push(`${control.name}: done card promises stale recorded audio`);
      if (routeListen || !routeMeta.startsWith('Requested: 10 min')) issues.push(`${control.name}: route exposes stale audio or measured duration`);
    }
  }
  console.log(JSON.stringify({ jobCardRecordingIdentity: evidence }));
  assert.deepEqual(issues, [], 'history and done cards offer recorded audio only for the current transcript identity');
}

async function checkRequestCardStates(hasCurrentEpisodeRecording) {
  const root = path.join(__dirname, '../..');
  const source = fs.readFileSync(path.join(root, 'src/app.js'), 'utf8');
  const section = (text, start, end) => {
    const first = text.indexOf(start); const last = text.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `production boundaries exist: ${start}`);
    return text.slice(first, last).replace(/^export /u, '');
  };
  const renderers = section(source, 'async function loadEpisodeCards(', 'function episodeProfileTopic(');
  const historyPath = path.join(root, 'src/screens/episode-history.js');
  const historySource = fs.existsSync(historyPath) ? fs.readFileSync(historyPath, 'utf8') : source;
  const stateHelper = fs.existsSync(historyPath)
    ? section(historySource, 'export function episodeHistoryState(', 'export function episodeHistoryDismissed(')
    : section(historySource, 'function episodeHistoryState(', 'function episodeHistoryDismissed(');
  const makingPath = path.join(root, 'src/screens/episode-making.js');
  const makingSource = fs.existsSync(makingPath) ? fs.readFileSync(makingPath, 'utf8') : source;
  const audioSource = makingSource;
  const audioHelpers = fs.existsSync(makingPath)
    ? section(audioSource, 'export function episodeHasReadyAudio(', 'function failureCopy(')
    : section(audioSource, 'function episodeHasReadyAudio(', 'async function pollEpisodeJob(');
  const requested = [
    '  What changed in the fictional water notice?  \n',
    '\tWhy did the fictional ferry schedule change?\n  ',
    'How does the fictional payment hold settle?  ',
  ];
  const recorded = {
    revision_id: 'fictional-card-r2', script_hash: 'fictional-card-hash-r2',
    audio_recorded: true, audio_status: 'recorded', duration_minutes: 2,
    duration_basis: 'measured rendered audio',
    audio: { status: 'recorded', revision_id: 'fictional-card-r2', script_hash: 'fictional-card-hash-r2',
      url: '/fictional-card.wav', duration_seconds: 120 },
  };
  const jobs = [
    { job_id: '11111111111111111111111111111111', status: 'researching', topic: requested[0], length: 10 },
    { job_id: '22222222222222222222222222222222', status: 'failed', topic: requested[1], length: 7 },
    { job_id: '33333333333333333333333333333333', status: 'done', topic: requested[2], length: 5, audio_status: 'recorded', episode: recorded },
  ];
  const cards = new FixtureNode('div');
  const context = vm.createContext({
    cards, hasCurrentEpisodeRecording, episodeHistorySnapshot: null,
    node: (tag, text, className = '') => Object.assign(new FixtureNode(tag, text ?? ''), { className }),
    link: (text, href, className = '') => Object.assign(new FixtureNode('a', text), { href, className }),
    fetch: async (url, options) => {
      assert.equal(url, '/api/episodes', 'only the fictional list transport is used');
      assert.equal(options.method || 'GET', 'GET');
      return { ok: true, status: 200, json: async () => ({ jobs }) };
    },
  });
  vm.runInContext(audioHelpers + stateHelper + renderers, context);
  await vm.runInContext('loadEpisodeCards(cards)', context);
  const rows = findNodes(cards, item => item.className === 'ask-episode-card');
  assert.equal(rows.length, 3, 'pending, failed and recorded request cards render');
  const expected = [
    { id: jobs[0].job_id, state: 'Making', href: `#episode-making/${jobs[0].job_id}`, length: 'Requested: 10 min' },
    { id: jobs[1].job_id, state: 'Could not finish · Retry', href: `#episode-making/${jobs[1].job_id}`, length: 'Requested: 7 min' },
    { id: jobs[2].job_id, state: 'Ready · Listen', href: `#episode/${jobs[2].job_id}`, length: '2 min of audio' },
  ];
  const evidence = rows.map((row, index) => {
    const heading = findNodes(row, item => item.tagName === 'H3')[0];
    const chip = findNodes(row, item => item.className.startsWith('episode-status-chip'))[0];
    const length = findNodes(row, item => item.className === 'episode-length-badge')[0];
    assert.equal(row.tagName, 'A', 'the whole request card is the visible primary action');
    assert.equal(row.href, expected[index].href, 'primary card action opens the status-appropriate route');
    assert.equal(heading.textContent, requested[index], 'the exact raw request, including whitespace/newlines, is retained');
    assert.equal(chip.textContent, expected[index].state);
    assert.equal(length.textContent, expected[index].length);
    return { href: row.href, request: heading.textContent, status: chip.textContent, length: length.textContent };
  });
  assert.equal(rows[0].textContent.includes('Listen'), false, 'pending card does not promise playback');
  assert.equal(rows[1].textContent.includes('Listen'), false, 'failed card does not promise playback');
  console.log(JSON.stringify({ requestCardStates: evidence }));
}

async function run() {
  global.document = { createElement: tag => new FixtureNode(tag) };
  const { renderEpisodePage, hasCurrentEpisodeRecording } = await import('../../src/episodes/episodes.js');
  await checkRequestCardStates(hasCurrentEpisodeRecording);
  if (process.env.MICA_CARD_STATES_ONLY === '1') return;
  const episodeStyles = fs.readFileSync(path.join(__dirname, '../../src/episodes/episodes.css'), 'utf8');
  const currentSource = {
    id: 'source-current-r2', outlet: 'Fictional City Desk',
    title: 'Current corrected fictional notice with the complete title',
    date: 'Nov 20, 2026', url: 'https://fictional.example.test/current-notice',
  };
  const supersededSource = {
    id: 'source-old-r1', outlet: 'Fictional Archive Desk',
    title: 'Superseded fictional notice from the earlier revision',
    date: 'Nov 18, 2026', url: 'https://fictional.example.test/superseded-notice', status: 'withdrawn',
  };
  const corrected = {
    id: 'episode-current-r2', topic_label: 'Fictional city notices',
    title: 'A corrected fictional city notice', asked: 'What changed in the notice?',
    duration_minutes: 3, made_date: '21 Nov 2026', updated: {
      date: 'Nov 21, 2026', note: 'Corrected the service date.',
    },
    sources: [currentSource, supersededSource],
    chapters: [
      { id: 'current-chapter', title: 'The current notice', sentences: [{
        text: 'The current notice keeps service open through the weekend.', source_ids: [currentSource.id],
      }] },
      { id: 'withdrawn-chapter', title: 'Superseded fictional claim', status: 'withdrawn', sentences: [{
        text: 'The earlier notice said service had ended.', source_ids: [supersededSource.id],
      }] },
    ],
  };
  const withdrawn = {
    id: 'episode-withdrawn-r2', topic_label: 'Fictional city notices',
    title: 'Superseded fictional episode title', asked: 'Did the fictional service end on Nov 19?', status: 'withdrawn',
    audio_recorded: true, duration_minutes: 3, made_date: '21 Nov 2026', updated: {
      date: 'Nov 21, 2026', note: 'The earlier notice was withdrawn after review.',
    },
    sources: [currentSource, supersededSource],
    chapters: [{ id: 'withdrawn-old-claim', title: 'Superseded fictional notice claim', status: 'withdrawn', sentences: [{
      text: 'The superseded notice said the service had ended.', source_ids: [supersededSource.id],
    }] }],
  };
  const capture = episode => {
    const container = new FixtureNode('main');
    renderEpisodePage(container, episode);
    const markers = findNodes(container, node => node.className === 'episode-citation');
    const markerEvidence = markers.map(marker => {
      const summary = findNodes(marker, node => node.tagName === 'SUMMARY')[0];
      const date = findNodes(marker, node => node.tagName === 'TIME')[0];
      const sourceLink = findNodes(marker, node => node.tagName === 'A')[0];
      return {
        name: summary?.getAttribute('aria-label'),
        detail: findNodes(marker, node => node.className === 'episode-citation-detail')[0]?.textContent,
        date: date?.textContent,
        url: sourceLink?.href,
      };
    });
    const heading = findNodes(container, node => node.tagName === 'H1')[0];
    return {
      markers: markerEvidence,
      text: container.textContent,
      headingFocused: document.activeElement === heading,
      listenControls: findNodes(container, node => node.tagName === 'BUTTON' && node.textContent.trim() === 'Listen').length,
    };
  };
  const correctedEvidence = capture(corrected);
  const withdrawnEvidence = capture(withdrawn);
  const unsafeURLs = [
    'http://fictional.example.test/insecure',
    'javascript:alert(1)',
    'https://reader:secret@fictional.example.test/credentialed',
    'https://fictional.example.test/with\ncontrol',
  ];
  const unsafeEvidence = unsafeURLs.map((url, index) => {
    const source = { ...currentSource, id: `source-unsafe-${index}`, url };
    const episode = {
      ...corrected,
      id: `episode-unsafe-${index}`,
      sources: [source],
      chapters: [{ id: `unsafe-chapter-${index}`, title: 'Fictional URL check', sentences: [{
        text: 'This fictional sentence tests source link safety.', source_ids: [source.id],
      }] }],
    };
    return capture(episode);
  });
  console.log(JSON.stringify({ corrected: correctedEvidence, withdrawn: withdrawnEvidence }));

  const issues = [];
  if (correctedEvidence.markers.length !== 1) issues.push('corrected transcript includes a superseded citation');
  if (correctedEvidence.markers[0]?.name !== `Source 1, ${currentSource.outlet} · ${currentSource.title}`) {
    issues.push('corrected source action omits the complete current title from its accessible name');
  }
  if (!correctedEvidence.markers[0]?.detail?.includes(currentSource.title)) issues.push('corrected source detail omits the current title');
  if (correctedEvidence.markers[0]?.date !== currentSource.date) issues.push('corrected source date differs from the current record');
  if (correctedEvidence.markers[0]?.url !== currentSource.url) issues.push('corrected source link differs from the current URL');
  if (!correctedEvidence.text.includes('Nov 21, 2026')) issues.push('correction date is absent from corrected transcript');
  if (/Superseded fictional claim|earlier notice said service had ended|Superseded fictional notice|fictional\.example\.test\/superseded-notice/u.test(correctedEvidence.text)) {
    issues.push('corrected transcript exposes withdrawn claim text or citation');
  }
  if (withdrawnEvidence.text.includes('Superseded fictional notice') ||
      withdrawnEvidence.text.includes('fictional.example.test/superseded-notice') ||
      withdrawnEvidence.text.includes('superseded notice said the service had ended')) {
    issues.push('withdrawn transcript exposes superseded source history');
  }
  if (!withdrawnEvidence.text.includes('Story withdrawn')) issues.push('withdrawal notice is absent from withdrawn transcript');
  if (withdrawnEvidence.markers.length) issues.push('withdrawn transcript exposes actionable source citations');
  if (!withdrawnEvidence.text.includes('Nov 21, 2026')) issues.push('withdrawal date is absent from the withdrawn transcript');
  if (withdrawnEvidence.text.includes(withdrawn.asked)) issues.push('withdrawn transcript exposes the old request text');
  if (withdrawnEvidence.listenControls) issues.push('withdrawn episode keeps an active Listen control');
  if (!correctedEvidence.headingFocused || !withdrawnEvidence.headingFocused) issues.push('episode heading does not receive focus on entry');
  if (!/\.episode-page h1:focus\s*\{[^}]*outline:\s*3px solid var\(--focus\)/u.test(episodeStyles)) {
    issues.push('programmatically focused episode heading has no visible theme-aware focus outline');
  }
  if (unsafeEvidence.some(evidence => evidence.markers.length !== 1 || evidence.markers[0].url)) {
    issues.push('an unsafe current source URL remains an actionable transcript link');
  }
  assert.deepEqual(issues, [], 'corrected and withdrawn transcript routes use only current source identity');
  await checkJobCardRecordingIdentity(corrected, hasCurrentEpisodeRecording, renderEpisodePage);
}

run().catch(error => { console.error(error); process.exitCode = 1; });
