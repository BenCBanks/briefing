export function episodeCardsSkeleton(node) {
  const list = node('div', null, 'episode-list-skeleton'); list.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 2; index++) {
    const card = node('div', null, 'episode-card-skeleton');
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-title'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-title episode-skeleton-title-short'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-meta'));
    card.append(node('span', null, 'loading-skeleton-block episode-skeleton-state'));
    list.append(card);
  }
  return list;
}

export function createEpisodeListScreen({ node, link, episodeHistoryState, episodeLengthCopy,
  episodeHasReadyAudio, getEpisodeJob, episodeUnheardIds, syncOnDemandBadge, onSnapshot, onRetry }) {
  async function load(cards, examples = null, prompt = null, { earlier = false } = {}) {
    cards.setAttribute('aria-busy', 'true');
    const earlierAsk = earlier ? link('Make an episode', '#ask-episode', 'earlier-ask-link') : null;
    const skeleton = episodeCardsSkeleton(node);
    const status = node('p', 'Loading your on-demand briefings…', 'sr-only'); status.setAttribute('role', 'status');
    cards.replaceChildren(...(earlierAsk ? [earlierAsk] : []), skeleton, status);
    try {
      const response = await fetch('/api/episodes', { cache: 'no-store' });
      if (response.status === 404) {
        if (!cards.isConnected) return;
        cards.setAttribute('aria-busy', 'false');
        if (examples) syncOnDemandBadge([]);
        if (examples) examples.hidden = false;
        const empty = node('p', earlier ? 'Episodes and briefings you keep will appear here.' : 'No on-demand briefings yet. Your first one will appear here.', 'episode-empty-copy');
        cards.replaceChildren(...(earlierAsk ? [earlierAsk] : []), empty);
        return;
      }
      if (!response.ok) throw new Error();
      const result = await response.json(); if (!cards.isConnected) return;
      if (!result || typeof result !== 'object' || !Array.isArray(result.jobs)) throw new Error('invalid_episode_list');
      const jobs = result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string');
      if (result.jobs.length && !jobs.length) throw new Error('invalid_episode_list');
      cards.setAttribute('aria-busy', 'false');
      if (examples) examples.hidden = false;
      cards.replaceChildren();
      if (examples) syncOnDemandBadge(jobs);
      if (!jobs.length) {
        cards.append(node('p', earlier ? 'Episodes and briefings you keep will appear here.' : 'No on-demand briefings yet. Your first one will appear here.', 'episode-empty-copy'));
      }
      if (earlierAsk) cards.prepend(earlierAsk);
      const activeJob = getEpisodeJob();
      const unheard = new Set(episodeUnheardIds());
      const listedJobs = earlier ? jobs : [...jobs].sort((a, b) => {
        const priority = job => {
          const id = job.job_id || job.id;
          const status = episodeHistoryState(job).key;
          if (id === activeJob?.jobId && !activeJob.done && !activeJob.failed) return 0;
          if (status === 'making') return 0;
          if (status === 'ready' && unheard.has(id)) return 1;
          return 2;
        };
        return priority(a) - priority(b) || String(b.created_at || b.updated_at || '').localeCompare(String(a.created_at || a.updated_at || ''));
      });
      for (const job of listedJobs) {
        const status = episodeHistoryState(job); const id = job.job_id || job.id;
        const activeMaking = id === activeJob?.jobId && !activeJob.done && !activeJob.failed;
        const hash = status.key === 'ready' ? `#episode/${encodeURIComponent(id)}` : activeMaking ? '#episode-making' : `#episode-making/${encodeURIComponent(id)}`;
        const card = link('', hash, 'ask-episode-card'); card.dataset.jobId = id; card.dataset.status = status.key;
        card.append(node('h3', job.episode?.title || job.topic || job.title || 'Your briefing'), node('p', episodeLengthCopy(job), 'episode-length-badge'));
        const state = node('span', null, `episode-status-chip episode-status-${status.key}`);
        const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true'); ring.dataset.check = status.ring === 'done' ? '✓' : status.ring === 'failed' ? '!' : '';
        state.append(ring, node('span', status.key === 'ready' ? episodeHasReadyAudio(job) ? 'Ready · Listen' : 'Transcript ready' : status.key === 'failed' ? 'Could not finish · Retry' : 'Making'));
        card.append(state); cards.append(card);
      }
      onSnapshot({ result, jobs, corrections: [] });
    } catch {
      if (!cards.isConnected) return;
      cards.setAttribute('aria-busy', 'false');
      const empty = node('div', null, 'episode-list-error'); const message = node('p', earlier ? 'Earlier episodes could not be loaded. Try again in a moment.' : 'Your episodes could not be loaded. Try again in a moment.');
      const retry = node('button', 'Try again', 'episode-list-retry'); retry.addEventListener('click', () => onRetry(cards, examples, prompt, { earlier }));
      empty.append(message, retry); cards.replaceChildren(empty);
    }
  }
  return load;
}
