import { episodeFailureCopy } from './episode-making.js';

export function episodeHistoryState(job) {
  const status = String(job.status || job.stage || 'queued').toLowerCase();
  if (['done', 'ready', 'complete', 'completed'].includes(status)) return { key: 'ready', label: 'Ready', ring: 'done' };
  if (['failed', 'error'].includes(status)) return { key: 'failed', label: 'Failed', ring: 'failed' };
  if (['cancelled', 'canceled'].includes(status)) return { key: 'cancelled', label: 'Cancelled', ring: 'cancelled' };
  return { key: 'making', label: 'Making', ring: 'active' };
}

export function episodeMakingStageCopy(stage) {
  const copy = {
    queued: 'Waiting to start.',
    fetching: 'Finding useful sources.',
    researching: 'Finding useful sources.',
    reading: 'Reading and checking sources.',
    evidence: 'Reading and checking sources.',
    checking: 'Reading and checking sources.',
    writing: 'Writing your episode.',
    recording: 'Preparing the audio.',
    scoring: 'Finishing your episode.',
    publishing: 'Finishing your episode.',
  };
  return copy[String(stage || '').toLowerCase()] || 'Your episode is being made.';
}

export function episodeHistoryDismissed() {
  try {
    const value = JSON.parse(localStorage.getItem('briefing:v1:episode-list-dismissed') || '[]');
    return Array.isArray(value) ? value.filter(id => typeof id === 'string').slice(0, 100) : [];
  } catch { return []; }
}

export function saveEpisodeHistoryDismissed(ids) {
  try { localStorage.setItem('briefing:v1:episode-list-dismissed', JSON.stringify([...new Set(ids)].slice(-100))); }
  catch { /* The visible list still works for this visit if storage is blocked. */ }
}

export function renderEpisodeMakingFromHistory({ content, node, link, job, onRetry }) {
  const page = node('section', null, 'episode-history-making');
  page.append(link('‹ Episodes', '#episodes', 'episode-back'));
  const view = episodeHistoryState(job);
  const heading = view.key === 'failed' ? 'This episode could not be completed' : view.key === 'cancelled' ? 'This episode was cancelled' : 'Your episode is being made';
  page.append(node('h1', heading));
  if (job.topic) page.append(node('p', job.topic, 'episode-topic'));
  const chip = node('span', null, `episode-status-chip episode-status-${view.key}`);
  chip.dataset.state = view.ring;
  const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
  ring.dataset.check = view.ring === 'done' ? '✓' : view.ring === 'failed' ? '!' : view.ring === 'cancelled' ? '–' : '';
  chip.append(ring, node('span', view.label)); page.append(chip);
  const detail = view.key === 'making' ? 'Making your episode. You can leave this screen and come back.' :
    view.key === 'failed' ? episodeFailureCopy({ ...job, failureReason: job.failure_reason || job.failureReason }) :
      view.key === 'cancelled' ? 'This episode was canceled. Your topic is saved.' : 'This request will stay in your episode history.';
  page.append(node('p', detail, 'episode-job-card-status'));
  if (view.key === 'failed' && onRetry) {
    const retry = node('button', 'Try again', 'primary episode-retry-button'); retry.type = 'button'; retry.addEventListener('click', onRetry);
    page.append(retry);
  }
  content.replaceChildren(page);
}

export function renderEpisodeHistory({ content, node, link, result, available, corrections = [],
  filter, searchQuery, savedIds, onRetry, onFilter, onSearch, onSave, onDelete }) {
  const jobs = Array.isArray(result?.jobs) ? result.jobs.filter(job => job && typeof job === 'object' && !Array.isArray(job) && typeof (job.job_id || job.id) === 'string') : [];
  const page = node('section', null, 'episode-list-page'); page.append(node('h1', 'Episodes'));
  if (!available) {
    page.append(node('p', 'Your episode list is not available right now.', 'episode-list-empty'));
    const retry = node('button', 'Try again', 'episode-list-retry'); retry.type = 'button'; retry.addEventListener('click', onRetry);
    page.append(retry); content.replaceChildren(page); return;
  }
  if (jobs.length) page.append(link('Make an episode', '#ask-episode', 'button-link'));
  if (Number.isInteger(result?.left_today)) page.append(node('p', result.left_today
    ? `You can make ${result.left_today} more ${result.left_today === 1 ? 'episode' : 'episodes'} today.`
    : "You have used today's three episodes. More tomorrow.", 'episode-list-allowance'));
  const dismissed = episodeHistoryDismissed();
  const correctionIDs = new Set(corrections.filter(item => item?.target_type === 'episode').map(item => item.target_id).filter(Boolean));
  const visibleJobs = jobs.filter(job => !dismissed.includes(job.job_id || job.id));
  const byNewest = [...visibleJobs].sort((a, b) => String(b.created_at || b.updated_at || b.finished_at || '').localeCompare(String(a.created_at || a.updated_at || a.finished_at || '')));
  const filters = [['all', 'All'], ['making', 'Making'], ['ready', 'Ready'], ['saved', 'Saved'], ['corrected', 'Corrected']];
  const filterBar = node('div', null, 'episode-history-filters');
  filterBar.setAttribute('role', 'group'); filterBar.setAttribute('aria-label', 'Filter episodes');
  for (const [key, label] of filters) {
    const button = node('button', label, 'episode-history-filter'); button.type = 'button'; button.dataset.episodeFilter = key;
    button.setAttribute('aria-pressed', String(filter === key));
    button.addEventListener('click', () => { onFilter(key); content.querySelector(`[data-episode-filter="${key}"]`)?.focus({ preventScroll: true }); });
    filterBar.append(button);
  }
  page.append(filterBar);
  const searchLabel = node('label', 'Search episodes', 'episode-history-search-label');
  const search = node('input'); search.type = 'search'; search.placeholder = 'Search titles or topics'; search.value = searchQuery; search.setAttribute('aria-label', 'Search episodes');
  search.addEventListener('input', () => {
    const value = search.value; onSearch(value);
    const next = content.querySelector('.episode-history-search-label input'); next?.focus(); next?.setSelectionRange(value.length, value.length);
  });
  searchLabel.append(search); page.append(searchLabel);
  const filtered = byNewest.filter(job => {
    const id = job.job_id || job.id; const status = episodeHistoryState(job);
    const episode = job.episode || {};
    const corrected = Boolean(job.updated || job.corrected || episode.updated || correctionIDs.has(id));
    const saved = savedIds.includes(id) || (episode.id && savedIds.includes(episode.id));
    if (filter === 'making' && status.key !== 'making') return false;
    if (filter === 'ready' && status.key !== 'ready') return false;
    if (filter === 'saved' && !saved) return false;
    if (filter === 'corrected' && !corrected) return false;
    const query = searchQuery.trim().toLocaleLowerCase();
    return !query || [job.topic, job.asked, episode.title, episode.asked, episode.topic_label]
      .some(value => String(value || '').toLocaleLowerCase().includes(query));
  });
  const count = node('p', `${filtered.length} ${filtered.length === 1 ? 'episode' : 'episodes'}`, 'episode-history-count');
  count.setAttribute('aria-live', 'polite'); page.append(count);
  const list = node('div', null, 'episode-job-list');
  for (const job of filtered) {
    const id = job.job_id || job.id; const status = episodeHistoryState(job); const episode = job.episode || {};
    const corrected = Boolean(job.updated || job.corrected || episode.updated || correctionIDs.has(id));
    const saved = savedIds.includes(id) || (episode.id && savedIds.includes(episode.id));
    const card = node('article', null, 'episode-job-card'); card.dataset.jobId = id || '';
    const title = episode.title || job.topic || 'Episode request';
    const hash = status.key === 'ready' ? `#episode/${encodeURIComponent(id)}` : `#episode-making/${encodeURIComponent(id)}`;
    const titleLink = link(title, hash, 'episode-job-title');
    const heading = node('h2'); heading.append(titleLink); card.append(heading);
    if (episode.topic_label && episode.topic_label !== title) card.append(node('p', episode.topic_label, 'episode-job-topic'));
    const chip = node('span', null, `episode-status-chip episode-status-${status.key}`); chip.dataset.state = status.ring;
    const ring = node('span', null, 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
    ring.dataset.check = status.ring === 'done' ? '✓' : status.ring === 'failed' ? '!' : status.ring === 'cancelled' ? '–' : '';
    chip.append(ring, node('span', status.label));
    const statusLine = node('div', null, 'episode-job-status-line'); statusLine.append(chip);
    if (corrected) { const updated = link('Updated', '#corrections', 'episode-updated'); updated.setAttribute('aria-label', 'Updated · view corrections'); statusLine.append(updated); }
    card.append(statusLine);
    const date = job.created_at || job.finished_at || job.updated_at || '';
    const parsedDate = date ? new Date(date) : null;
    const dateText = parsedDate && !Number.isNaN(parsedDate.getTime()) ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(parsedDate) : '';
    card.append(node('p', `${job.length || episode.duration_minutes || 5} min${dateText ? ` · ${dateText}` : ''}`, 'episode-job-card-status'));
    const actions = node('div', null, 'episode-job-actions');
    const save = node('button', saved ? 'Saved' : 'Save', 'episode-job-save'); save.type = 'button';
    save.setAttribute('aria-label', `${saved ? 'Remove' : 'Save'} ${title}${saved ? ' from saved episodes' : ''}`); save.setAttribute('aria-pressed', String(saved));
    save.addEventListener('click', () => { onSave(id, episode.revision_id || job.revision_id || null); content.querySelector(`[data-job-id="${CSS.escape(id)}"] .episode-job-save`)?.focus({ preventScroll: true }); });
    const remove = node('button', 'Delete', 'episode-job-delete'); remove.type = 'button'; remove.setAttribute('aria-label', `Delete ${title} from this list`);
    remove.addEventListener('click', () => onDelete(id, title));
    actions.append(save, remove); card.append(actions); list.append(card);
  }
  if (filtered.length) page.append(list);
  else {
    const empty = node('section', null, 'episode-history-empty'); const query = searchQuery.trim();
    const messages = {
      all: ['Ask for your first episode', 'Episodes you request will appear here.'],
      making: ['No episodes being made', 'When an episode is in progress, it will appear here.'],
      ready: ['No ready episodes yet', 'Completed episodes will appear here.'],
      saved: ['No saved episodes', 'Save an episode to keep it close at hand.'],
      corrected: ['No corrected episodes', 'Corrections to an episode will appear here.'],
    };
    if (query) empty.append(node('h2', 'No matching episodes'), node('p', `No episodes match “${query}”.`));
    else if (!visibleJobs.length && filter === 'all') empty.append(node('h2', messages.all[0]), node('p', messages.all[1]), link('Ask for your first episode', '#ask-episode', 'button-link'));
    else empty.append(node('h2', messages[filter][0]), node('p', messages[filter][1]));
    page.append(empty);
  }
  content.replaceChildren(page);
}
