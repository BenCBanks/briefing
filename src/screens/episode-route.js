import { renderEpisodeDeck, renderEpisodePage, hasCurrentEpisodeRecording } from '../episodes/episodes.js';

let episodeFixturePromise = null;

export function renderEpisodeRouteFailure({ content, node, link, title, message, onRetry }) {
  const page = node('section', null, 'episode-route-error');
  page.setAttribute('role', 'status');
  page.append(node('h1', title), node('p', message));
  const retry = node('button', 'Try again', 'episode-route-retry'); retry.type = 'button'; retry.addEventListener('click', onRetry);
  page.append(retry, link('Return to today', '#today', 'button-link'));
  content.replaceChildren(page);
}

export async function renderEpisodeRoute(routeName, {
  content, node, location, episodeHistorySnapshot, renderEpisodeHistory,
  renderEpisodeMakingFromHistory, renderRouteFailure, renderMissing, navigate, player,
  isEpisodeInDailyBriefing, setEpisodeDailyBriefing, onEpisodeHeard = () => {}, setEpisodeDeckCleanup,
}) {
  const loading = node('section', null, 'episode-loading');
  loading.setAttribute('role', 'status'); loading.append(node('h1', 'Loading episode'), node('div', null, 'edition-skeleton'));
  content.replaceChildren(loading);
  try {
    if (routeName === 'episodes' && new URLSearchParams(location.search).get('episodes') !== '1') {
      const [response, correctionsResponse] = await Promise.all([
        fetch('/api/episodes', { cache: 'no-store' }),
        fetch('/api/corrections?target_type=episode', { cache: 'no-store' }).catch(() => null),
      ]);
      const result = await response.json().catch(() => ({}));
      const correctionData = await correctionsResponse?.json().catch(() => ({})) || {};
      if (location.hash !== '#episodes') return;
      const jobs = Array.isArray(result?.jobs) ? result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string') : [];
      const listAvailable = response.ok && Array.isArray(result?.jobs) && (!result.jobs.length || jobs.length > 0);
      renderEpisodeHistory(result, listAvailable, Array.isArray(correctionData.corrections) ? correctionData.corrections : []);
      return;
    }
    if (routeName.startsWith('episode-making/')) {
      const id = routeName.slice('episode-making/'.length);
      let job = episodeHistorySnapshot?.jobs.find(item => (item.job_id || item.id) === id) || null;
      if (!job && /^[a-f0-9]{32}$/.test(id)) {
        const response = await fetch(`/api/episodes/${encodeURIComponent(id)}`, { cache: 'no-store' });
        job = await response.json().catch(() => null);
        if (!response.ok || !job || typeof job !== 'object' || Array.isArray(job) || !(job.status || job.stage || job.topic)) job = null;
      }
      if (location.hash !== `#${routeName}`) return;
      if (job) renderEpisodeMakingFromHistory(job); else renderRouteFailure('Episode unavailable', 'This episode could not be loaded right now.');
      return;
    }
    if (routeName.startsWith('episode/')) {
      const id = routeName.slice('episode/'.length);
      if (/^[a-f0-9]{32}$/.test(id)) {
        const response = await fetch(`/api/episodes/${encodeURIComponent(id)}`, { cache: 'no-store' });
        const result = await response.json().catch(() => ({}));
        if (location.hash !== `#${routeName}`) return;
        if (!response.ok || result.status !== 'done' || !result.episode || typeof result.episode !== 'object' || Array.isArray(result.episode) || !Array.isArray(result.episode.chapters)) {
          renderRouteFailure('Episode unavailable', 'This episode could not be loaded right now. Try again in a moment.');
          return;
        }
        const audioReady = (!result.audio_status || result.audio_status === 'recorded') && hasCurrentEpisodeRecording(result.episode);
        renderEpisodePage(content, { ...result.episode, kind: 'episode', id: result.job_id || id,
          topic_label: result.topic || result.episode.topic_label, asked: result.topic || result.episode.asked,
          duration_minutes: result.episode.duration_minutes, requested_minutes: result.length || result.episode.requested_minutes,
          audio_status: audioReady ? 'recorded' : 'pending',
          made_date: String(result.finished_at || result.updated_at || '').slice(0, 10), audio_recorded: audioReady }, {
          onListen: episode => { onEpisodeHeard(result.job_id || id); player.playRecording(episode); },
          compactSources: true,
          inDailyBriefing: isEpisodeInDailyBriefing({ ...result.episode, topic_label: result.topic || result.episode.topic_label }),
          onDailyBriefingChange: setEpisodeDailyBriefing,
        });
        return;
      }
    }
    if (routeName.startsWith('episode/') && new URLSearchParams(location.search).get('episodes') !== '1') {
      if (location.hash === `#${routeName}`) renderMissing();
      return;
    }
    if (!episodeFixturePromise) episodeFixturePromise = fetch('/dev/tests/fixtures/episodes.json', { cache: 'no-store' }).then(response => {
      if (!response.ok) throw new Error('fixture unavailable');
      return response.json();
    }).then(data => {
      if (!Array.isArray(data.episodes)) throw new Error('invalid episode fixture');
      return data.episodes;
    }).catch(error => { episodeFixturePromise = null; throw error; });
    const episodes = await episodeFixturePromise;
    if (location.hash !== `#${routeName}`) return;
    if (routeName === 'episodes') {
      setEpisodeDeckCleanup(renderEpisodeDeck(content, episodes, {
        onClose: () => navigate('#today', { root: 'today', parentHash: null, returnFocus: { type: 'nav', id: 'today' } }),
        onOpen: id => navigate(`#episode/${encodeURIComponent(id)}`, { root: 'today', parentHash: '#episodes', parentLabel: 'Episodes' }),
        onListen: item => player.playRecording({ ...item, kind: 'episode' }),
      }));
      return;
    }
    const id = routeName.slice('episode/'.length);
    const episode = episodes.find(item => item.id === id);
    if (episode) renderEpisodePage(content, { ...episode, kind: 'episode' }, {
      onListen: item => player.playRecording(item),
      inDailyBriefing: isEpisodeInDailyBriefing(episode),
      onDailyBriefingChange: setEpisodeDailyBriefing,
    });
    else renderMissing();
  } catch {
    if (location.hash !== `#${routeName}`) return;
    renderRouteFailure('Episodes are unavailable', 'This screen could not be loaded right now. Try again in a moment.');
  }
}
