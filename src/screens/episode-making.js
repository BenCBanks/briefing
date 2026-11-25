import { episodeAudioFailureCopy, hasCurrentEpisodeRecording } from '../episodes/episodes.js';

export const EPISODE_STAGE_FIXTURES = [
  { title: 'Finding sources', detail: 'Searching for useful, relevant sources.' },
  { title: 'Reading', detail: 'Reading sources and checking claims against them.' },
  { title: 'Writing', detail: 'Turning checked material into chapters.' },
  { title: 'Recording', detail: 'Preparing the narrated episode.' },
  { title: 'Ready', detail: 'Your episode is ready to play.' },
];
const LONG_WAIT_MS = 2 * 60 * 1000;

export function announceEpisodeStage(content, job, message) {
  const region = content.querySelector('.episode-stage-announcement');
  if (region) region.textContent = message;
  job.announcedStage = job.stageIndex;
}

export function episodeStageIndex(stage) {
  if (stage === 'done') return 4;
  if (stage === 'evidence' || stage === 'checking') return 1;
  const index = ['researching', 'reading', 'writing', 'recording'].indexOf(stage);
  return index < 0 ? 0 : index;
}

export function episodeStageTitle(_job, index) {
  return EPISODE_STAGE_FIXTURES[index]?.title || 'Finding sources';
}

export function episodeHasReadyAudio(job) {
  return (!job?.audio_status || job.audio_status === 'recorded') && hasCurrentEpisodeRecording(job?.episode);
}

export function episodeLengthCopy(job) {
  const episode = job?.episode || {};
  const minutes = Number(episode.duration_minutes);
  if (episodeHasReadyAudio(job) && episode.duration_basis === 'measured rendered audio'
      && Number.isFinite(minutes) && minutes > 0) return `${minutes} min of audio`;
  const requested = job?.length ?? job?.minutes ?? episode.requested_minutes;
  return Number.isFinite(Number(requested)) && Number(requested) > 0
    ? `Requested: ${Number(requested)} min` : 'Requested length not listed';
}

export function episodeFailureCopy(job) {
  if (job.status === 'cancelled') return 'This episode was canceled. Your topic is saved.';
  if (job.failureReason === 'cost_cap_reached') return "We couldn't make this episode right now. Your topic is saved. Try again later.";
  if (job.failureReason === 'insufficient_evidence') return "We couldn't find enough useful sources to make this episode. Your topic is saved. Try a more specific question.";
  if (job.failureReason === 'unsupported_format') return "We can't make that format yet. Your topic is saved. Try asking about the subject instead.";
  if (job.failureReason === 'provider_failure') return "A service interrupted this episode. Your topic is saved. Try again.";
  return "We couldn't finish this episode. Your topic is saved. Try again.";
}

function renderFailureState(page, job, node, onRetry) {
  page.querySelector('.episode-stage-list')?.remove();
  page.querySelector('.episode-progress-note')?.remove();
  let notice = page.querySelector('.episode-job-error');
  if (!notice) { notice = node('p', '', 'episode-job-error'); notice.setAttribute('role', 'status'); page.append(notice); }
  notice.textContent = episodeFailureCopy(job);
  if (job.status === 'cancelled' || job.failureReason === 'cost_cap_reached' || page.querySelector('.episode-retry-button')) return;
  const retry = node('button', 'Try again', 'primary episode-retry-button'); retry.type = 'button'; retry.addEventListener('click', () => onRetry(job));
  page.append(retry);
}

function renderActivityPanel(panel, job) {
  if (!panel) return;
  const counts = job.counts || {};
  const episodeSources = Array.isArray(job.episode?.sources) ? job.episode.sources.length : 0;
  const found = Number(counts.sources_found) || Number(counts.sources) || episodeSources;
  const read = Number(counts.sources_read) || 0;
  panel.textContent = found ? `${found} ${found === 1 ? 'source' : 'sources'} found${read ? `, ${read} read` : ''}` : '';
}

function appendDoneCard(page, job, node, hasReadyAudio, navigate) {
  const audioReady = hasReadyAudio(job);
  const card = node('section', null, 'episode-done-card');
  card.append(node('p', audioReady ? 'Episode ready' : 'Transcript ready', 'episode-done-kicker'),
    node('h2', audioReady ? 'Your episode is ready' : 'Your transcript is ready'));
  card.append(node('p', `Episode length: ${job.minutes} ${Number(job.minutes) === 1 ? 'minute' : 'minutes'}`, 'episode-done-meta'));
  if (!audioReady) {
    const reason = job.episode?.audio?.reason || job.episode?.audio_failure_reason;
    const audioFailed = job.audio_status === 'failed' || job.audioStatus === 'failed' || job.episode?.audio_status === 'failed' ||
      job.episode?.audio?.status === 'failed';
    const note = audioFailed
      ? 'Audio could not be recorded. ' + episodeAudioFailureCopy(reason) + ' The transcript is ready to read.'
      : 'Audio is not recorded yet. The transcript is ready to read.';
    card.append(node('p', note, 'episode-audio-pending-note'));
  }
  if (job.shorterThanRequested || job.episode?.shortfall_notice) card.append(node('p', 'We found limited coverage; here is a shorter episode.', 'episode-done-notice episode-thin-evidence'));
  const actions = node('div', null, 'episode-done-actions');
  const open = node('button', audioReady ? 'Open episode' : 'Open transcript', 'primary episode-read-button'); open.type = 'button';
  open.addEventListener('click', () => navigate(`#episode/${encodeURIComponent(job.jobId)}`, { root: 'today', parentHash: '#episode-making', parentScroll: window.scrollY }));
  const another = node('button', 'Ask for another episode', 'episode-back-button'); another.type = 'button';
  another.addEventListener('click', () => navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 }));
  actions.append(open, another); card.append(actions); page.append(card);
}

function updateStreamingChapters(page, node, job, onPlayChapter) {
  const stream = job.streaming;
  const ready = Array.isArray(stream?.ready_chapters)
    ? stream.ready_chapters.filter(row => row && typeof row === 'object' && typeof row.id === 'string') : [];
  const expected = Math.max(ready.length, Number.isInteger(stream?.expected_chapters) ? stream.expected_chapters : 0);
  if (!stream || expected < 1) {
    page.querySelector('.episode-streaming-chapters')?.remove();
    return;
  }
  const signature = JSON.stringify([stream.status, expected, ready.map(row => [row.id, row.title, row.audio_url, row.duration_seconds])]);
  let panel = page.querySelector('.episode-streaming-chapters');
  if (!panel) {
    panel = node('section', null, 'episode-streaming-chapters');
    panel.setAttribute('aria-labelledby', 'episode-streaming-heading');
    const heading = node('h2', 'Chapters'); heading.id = 'episode-streaming-heading';
    panel.append(heading, node('p', '', 'episode-streaming-progress'), node('ol', null, 'episode-streaming-list'));
    const stages = page.querySelector('.episode-stage-list');
    const note = page.querySelector('.episode-progress-note');
    if (stages) stages.before(panel); else if (note) note.after(panel); else page.append(panel);
  }
  if (panel.dataset.renderSignature === signature) return;
  panel.dataset.renderSignature = signature;
  panel.querySelector('.episode-streaming-progress').textContent = `${ready.length} of ${expected} chapters ready`;
  const list = panel.querySelector('.episode-streaming-list');
  list.replaceChildren();
  for (let index = 0; index < expected; index += 1) {
    const chapter = ready[index];
    const row = node('li', null, 'episode-streaming-row');
    if (chapter) {
      const title = chapter.title || `Chapter ${index + 1}`;
      row.append(node('span', title, 'episode-streaming-title'));
      const play = node('button', 'Play', 'primary episode-streaming-play'); play.type = 'button';
      play.setAttribute('aria-label', `Play ${title}`);
      play.addEventListener('click', () => onPlayChapter(chapter));
      row.append(play);
    } else {
      row.append(node('span', `Chapter ${index + 1}`, 'episode-streaming-title'));
      row.append(node('span', stream.status === 'complete' || job.done ? 'Not recorded' : 'Still being made', 'episode-streaming-status'));
    }
    list.append(row);
  }
}

export function updateEpisodeMakingView({ content, node, job, elapsed, totalElapsed = elapsed, navigate, onRetry, onPlayChapter }) {
  const page = content.querySelector('.episode-making-page');
  if (!page) return;
  const kicker = page.querySelector('.episode-making-kicker');
  const longWait = !job.done && !job.failed && totalElapsed >= LONG_WAIT_MS;
  const failureKicker = job.failureReason === 'cost_cap_reached' ? 'Try again later' : 'Ready to try again';
  if (kicker) kicker.textContent = job.done ? 'Episode ready' : job.failed ? failureKicker : longWait ? 'Still working' : 'Making your episode';
  const longWaitNote = page.querySelector('.episode-long-wait');
  if (longWaitNote) longWaitNote.hidden = !longWait;
  if (job.done || job.failed) page.querySelector('.episode-making-intro')?.remove();
  if (job.done) {
    page.querySelector('.episode-stage-list')?.remove();
    page.querySelector('.episode-progress-note')?.remove();
  }
  if (job.done || job.failed) page.querySelector('.episode-making-actions')?.remove();
  if (job.failed) renderFailureState(page, job, node, onRetry);
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const progress = job.done ? 1 : Math.min(.94, Math.max(.08, elapsed / 4000));
  page.querySelectorAll('.episode-stage-row').forEach((row, index) => {
    const stateName = job.done || index < job.stageIndex ? 'done' : index === job.stageIndex ? 'active' : 'upcoming';
    row.dataset.state = stateName;
    if (stateName === 'active') row.setAttribute('aria-current', 'step'); else row.removeAttribute('aria-current');
    const ring = row.querySelector('.episode-stage-ring');
    row.querySelector('.episode-stage-copy strong').textContent = episodeStageTitle(job, index);
    ring.textContent = ''; ring.dataset.check = stateName === 'done' ? '✓' : '';
    const fill = stateName === 'done' ? 360 : stateName === 'active' ? (reducedMotion ? 90 : Math.round(progress * 360)) : 0;
    ring.style.setProperty('--episode-ring-fill', `${fill}deg`);
    const read = Number(job.counts?.sources_read ?? (index === 1 ? job.counts?.sources : 0)) || 0;
    const detail = index === 1 && read ? `${read} ${read === 1 ? 'source' : 'sources'} read; checking claims.` : EPISODE_STAGE_FIXTURES[index].detail;
    row.querySelector('.episode-stage-status').textContent = stateName === 'done' ? 'Complete.' : stateName === 'active' && job.status === 'queued' ? 'Waiting to start.' : detail;
  });
  const activityKey = `${job.done ? 'done' : job.stageIndex}:${JSON.stringify(job.counts)}`;
  if (job.activityKey !== activityKey) { job.activityKey = activityKey; renderActivityPanel(page.querySelector('.episode-progress-note'), job); }
  updateStreamingChapters(page, node, job, onPlayChapter);
  if (job.done && !page.querySelector('.episode-done-card')) appendDoneCard(page, job, node, episodeHasReadyAudio, navigate);
}

export function renderEpisodeMakingPage({ content, node, link, job, navigate, onCancel, onRetry, onPlayChapter }) {
  if (!job) {
    content.append(node('h1', 'No episode is being made'), link('Return to episode request', '#ask-episode', 'button-link'));
    return;
  }
  const sticky = node('div', null, 'reader-sticky');
  const back = node('button', '‹ On demand', 'reader-back'); back.type = 'button';
  back.addEventListener('click', () => navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 }));
  sticky.append(back); content.append(sticky);
  const announcement = node('p', '', 'episode-stage-announcement episode-visually-hidden');
  announcement.setAttribute('role', 'status'); announcement.setAttribute('aria-live', 'polite'); announcement.setAttribute('aria-atomic', 'true');
  const page = node('section', null, 'episode-making-page');
  page.append(node('p', job.done ? 'Ready' : job.failed ? 'Not finished' : 'Making your episode', 'episode-making-kicker'));
  page.append(node('blockquote', `“${job.request}”`, 'episode-making-quote'));
  if (!job.done && !job.failed) page.append(node('p', 'Making your episode. You can leave this screen and come back.', 'episode-making-intro'));
  page.append(node('p', `Episode length: ${job.minutes} ${Number(job.minutes) === 1 ? 'minute' : 'minutes'}`, 'episode-making-meta'));
  const longWait = node('p', 'This is taking a little longer. Your briefing will stay in the On demand list if you leave this page.', 'episode-long-wait');
  longWait.setAttribute('role', 'status'); longWait.hidden = true; page.append(longWait);
  const stages = node('ol', null, 'episode-stage-list');
  EPISODE_STAGE_FIXTURES.forEach((stage, index) => {
    const row = node('li', null, 'episode-stage-row'); row.dataset.index = String(index);
    const ring = node('span', '', 'episode-stage-ring'); ring.setAttribute('aria-hidden', 'true');
    const copy = node('div', null, 'episode-stage-copy'); copy.append(node('strong', stage.title), node('small', stage.detail, 'episode-stage-status'));
    row.append(ring, copy); stages.append(row);
  });
  page.append(stages, node('p', null, 'episode-progress-note'));
  if (!job.done && !job.failed) {
    const actions = node('div', null, 'episode-making-actions');
    const cancel = node('button', 'Cancel this episode', 'episode-cancel-button'); cancel.type = 'button'; cancel.addEventListener('click', onCancel);
    actions.append(cancel); page.append(actions);
  }
  if (job.pollError) { const notice = node('p', job.pollError, 'episode-job-status'); notice.setAttribute('role', 'status'); page.append(notice); }
  content.append(announcement, page);
  if (job.announcedStage < 0) announceEpisodeStage(content, job, `${episodeStageTitle(job, job.stageIndex)}. ${EPISODE_STAGE_FIXTURES[job.stageIndex].detail}`);
  if (job.done) announceEpisodeStage(content, job, 'Your episode is ready.');
  updateEpisodeMakingView({ content, node, job, elapsed: Date.now() - job.stageStartedAt,
    totalElapsed: Date.now() - job.startedAt, navigate, onRetry, onPlayChapter });
}
