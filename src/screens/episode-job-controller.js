export function createEpisodeJobController(app) {
  const { node, document, content, renderOnDemandScreen, rememberEpisodeLength, loadEpisodeCards, EPISODE_MODES, interpret, defaultProfile, syncProfile, route, syncOnDemandBadge, navigate, announceEpisodeStageView, EPISODE_STAGE_FIXTURES, episodeStageIndex, episodeStageTitle, markEpisodeUnheard, updateEpisodeMakingScreen, renderEpisodeMakingScreen, link, cancelEpisodeJob, player, validateProfile, saveProfile } = app;
function renderEpisodeRequestPage() {
  renderOnDemandScreen({
    node, document, content, draft: app.episodeDraft,
    getRequestState: () => ({ busy: app.episodeRequestBusy, blocked: app.episodeRequestBlocked, error: app.episodeRequestError }),
    setRequestError: value => { app.episodeRequestError = value; },
    setRequestBlocked: value => { app.episodeRequestBlocked = value; },
    rememberLength: rememberEpisodeLength,
    startEpisode: startEpisodeJob,
    loadEpisodeCards,
    defaultMode: EPISODE_MODES[0],
  });
}

function episodeProfileTopic(episode, profile) {
  const source = String(episode.topic_label || episode.topic || episode.asked || episode.title || '')
    .replace(/\s+/g, ' ').trim().slice(0, 500);
  if (!source) throw new Error('This episode has no topic to add.');
  const interpreted = interpret(`Follow ${source}`, { topics: app.pack?.topics || [], minutes: profile.minutes });
  const topic = interpreted.profile.topics[0];
  if (!topic) throw new Error('Could not identify this episode topic.');
  return {
    ...topic,
    label: topic.label.slice(0, 60),
    scope: topic.scope.slice(0, 180),
    include: topic.include.slice(0, 8).map(value => value.slice(0, 240)),
    exclude: topic.exclude.slice(0, 8).map(value => value.slice(0, 240)),
    sourcePhrase: source.slice(0, 500),
  };
}

function isEpisodeInDailyBriefing(episode) {
  const profile = app.interestProfile;
  if (!profile) return false;
  const topic = episodeProfileTopic(episode, profile);
  const muted = new Set((profile.muted || []).map(item => item.label.toLocaleLowerCase()));
  return profile.topics.some(item => (item.id === topic.id || item.label.toLocaleLowerCase() === topic.label.toLocaleLowerCase()) && !muted.has(item.label.toLocaleLowerCase()));
}

async function setEpisodeDailyBriefing(episode, included) {
  const current = app.interestProfile || { version: 1, words: '', minutes: 12, background: null, topics: [], muted: [] };
  const topic = episodeProfileTopic(episode, current);
  const key = topic.label.toLocaleLowerCase();
  const muted = (current.muted || []).filter(item => item.label.toLocaleLowerCase() !== key);
  let topics = [...current.topics];
  const existingIndex = topics.findIndex(item => item.id === topic.id || item.label.toLocaleLowerCase() === key);
  if (included) {
    if (existingIndex < 0 && topics.length >= 12) throw new Error('Your daily briefing already has 12 topics. Remove one in Topics first.');
    if (existingIndex < 0) {
      let id = topic.id;
      let suffix = 2;
      while (topics.some(item => item.id === id)) id = `${topic.id}-${suffix++}`;
      topics.push({ ...topic, id });
    }
  } else if (existingIndex >= 0) {
    topics = topics.filter((_, index) => index !== existingIndex);
  }
  const updated = validateProfile({ ...current, topics, muted });
  try { await syncProfile(updated); }
  catch { throw new Error('Could not update your daily briefing. Try again.'); }
  app.interestProfile = saveProfile(updated, { server: false });
}

function openEpisodeStyleSheet(opener) {
  const dialog = node('dialog', null, 'episode-style-dialog');
  const sheet = node('section', null, 'episode-style-sheet');
  const heading = node('h2', 'How should it be told?'); heading.id = 'episode-style-heading';
  const close = node('button', 'Close', 'episode-style-close'); close.type = 'button';
  close.setAttribute('aria-label', 'Close narration style choices');
  close.addEventListener('click', () => dialog.close());
  sheet.append(close, heading, node('p', 'Choose the shape of your episode.', 'episode-style-intro'));
  for (const mode of EPISODE_MODES) {
    const choice = node('button', null, 'episode-style-choice'); choice.type = 'button';
    choice.append(node('strong', mode.label), node('small', mode.detail));
    choice.addEventListener('click', () => {
      dialog.close();
      startEpisodeJob(mode);
    });
    sheet.append(choice);
  }
  dialog.setAttribute('aria-labelledby', heading.id);
  dialog.append(sheet);
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { dialog.remove(); if (location.hash === '#ask-episode') opener?.focus(); }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
}

function episodeModeStyle(mode) {
  return mode.id === 'brief' ? 'news-brief' : mode.id === 'deep-dive' ? 'explainer' : 'conversational';
}

async function startEpisodeJob(mode) {
  if (app.episodeRequestBusy) return;
  if (app.episodeJobTimer) clearTimeout(app.episodeJobTimer);
  app.episodeJobTimer = null; app.episodeJob = null;
  const request = app.episodeDraft.prompt;
  app.episodeRequestBusy = true;
  app.episodeRequestError = '';
  const status = content.querySelector('.episode-request-error');
  if (status) status.textContent = 'Starting your episode…';
  const start = content.querySelector('#episode-request-start');
  if (start) { start.disabled = true; start.textContent = 'Starting your episode…'; }
  try {
    // The private job API attaches requests to its saved profile. A first-time
    // listener gets the standard local profile until their own interests exist.
    await syncProfile(app.interestProfile || defaultProfile(app.pack.topics));
    const response = await fetch('/api/episodes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: request, length: app.episodeDraft.minutes, style: episodeModeStyle(mode) }),
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 429 && result.error === 'cost_cap_reached') {
      app.episodeRequestError = 'We cannot make this episode right now. Your request is saved here; try again later.';
      app.episodeRequestBlocked = true;
      return;
    }
    if (response.status === 429 && result.error === 'daily_limit_reached') {
      const leftToday = Number.isInteger(result.left_today) ? Math.max(0, result.left_today) : 0;
      app.episodeRequestError = leftToday
        ? `You can make ${leftToday} more ${leftToday === 1 ? 'episode' : 'episodes'} today.`
        : "You have used today's three episodes. More tomorrow.";
      app.episodeRequestBlocked = true;
      return;
    }
    if (response.status === 429 && result.error === 'rate_limited') {
      app.episodeRequestError = 'Please wait a moment, then try again.';
      return;
    }
    if (response.status === 422 && result.error === 'unsupported_format') {
      app.episodeRequestError = 'That kind of episode is not supported yet. Try a factual question.';
      return;
    }
    if (response.status === 400 || response.status === 422) {
      app.episodeRequestError = 'That request needs a little more detail. Edit the topic or question and try again.';
      return;
    }
    if (!response.ok || !result.job_id) throw new Error('episode_request_failed');
    app.episodeJob = {
      costEstimate: result.cost_estimate?.label, jobId: result.job_id, request, minutes: app.episodeDraft.minutes, mode,
      status: result.status || 'queued',
      stage: result.stage || 'queued', counts: result.counts || { chapters: 0, sentences: 0, sources: 0 },
      streaming: result.streaming || null,
      startedAt: Date.now(), stageStartedAt: Date.now(), stageIndex: 0,
      announcedStage: -1, activityKey: '', done: false,
    };
    syncOnDemandBadge();
    navigate('#episode-making', { root: 'today', parentHash: '#ask-episode', parentScroll: window.scrollY,
      parentFocus: { type: 'id', id: 'episode-request-start' } });
    void pollEpisodeJob(app.episodeJob);
  } catch {
    app.episodeRequestError = 'Your episode could not be started. Please try again.';
  } finally {
    app.episodeRequestBusy = false;
    if (location.hash === '#ask-episode' && !app.episodeJob) route();
  }
}

function announceEpisodeStage(job, message) {
  announceEpisodeStageView(content, job, message);
}

async function pollEpisodeJob(job) {
  if (!job || job !== app.episodeJob || !job.jobId) return;
  if (app.episodeJobTimer) clearTimeout(app.episodeJobTimer);
  try {
    const response = await fetch(`/api/episodes/${encodeURIComponent(job.jobId)}`, { cache: 'no-store' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result) throw new Error('episode_status_unavailable');
    job.counts = result.counts || job.counts;
    const nextStage = result.stage || result.status || job.stage;
    const nextIndex = episodeStageIndex(nextStage);
    if (nextStage !== job.stage) {
      job.stage = nextStage;
      job.stageIndex = nextIndex;
      job.stageStartedAt = Date.now();
      const stage = { ...EPISODE_STAGE_FIXTURES[nextIndex], title: episodeStageTitle(job, nextIndex) };
      if (nextStage === 'done') announceEpisodeStage(job, 'Your episode is ready.');
      else if (nextStage === 'failed') announceEpisodeStage(job, 'This episode could not be completed.');
      else if (nextStage === 'cancelled') announceEpisodeStage(job, 'Episode cancelled.');
      else if (job.announcedStage !== nextIndex) announceEpisodeStage(job, `${stage.title}. ${stage.detail}`);
    }
    job.status = result.status || nextStage;
    job.message = result.message || '';
    job.failureReason = result.failure_reason || result.error || '';
    job.streaming = result.streaming || null;
    job.episode = result.episode || job.episode || null;
    job.audio_status = result.audio_status || job.episode?.audio_status || job.episode?.audio?.status || '';
    job.shorterThanRequested = Boolean(result.shorter_than_requested || result.shortfall_notice ||
      job.episode?.shorter_than_requested || job.episode?.shortfall_notice);
    job.done = job.status === 'done';
    job.failed = job.status === 'failed' || job.status === 'cancelled';
    job.pollError = '';
    if (job.done) markEpisodeUnheard(job.jobId);
  } catch {
    job.pollError = 'We could not check progress just now. We’ll try again shortly.';
  }
  syncOnDemandBadge();
  if (location.hash === '#episode-making') updateEpisodeMakingView(job, Date.now() - job.stageStartedAt, Date.now() - job.startedAt);
  if (!job.done && !job.failed) app.episodeJobTimer = setTimeout(() => void pollEpisodeJob(job), 1000);
}

function episodeMakingRetry(job) {
  app.episodeDraft.prompt = job.request; rememberEpisodeLength(job.minutes);
  navigate('#ask-episode', { root: 'today', parentHash: '#episode-making', parentScroll: 0 });
  void startEpisodeJob(job.mode);
}

function updateEpisodeMakingView(job, elapsed, totalElapsed = elapsed) {
  updateEpisodeMakingScreen({ content, node, job, elapsed, totalElapsed, navigate, onRetry: episodeMakingRetry,
    onPlayChapter: chapter => playStreamingChapter(job, chapter) });
}

function playStreamingChapter(job, chapter) {
  if (!job?.jobId || typeof chapter?.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(chapter.id)) return;
  const expectedPath = `/api/episodes/${encodeURIComponent(job.jobId)}/chapters/${encodeURIComponent(chapter.id)}/audio`;
  let audioUrl;
  try {
    const parsed = new URL(chapter.audio_url, location.origin);
    if (parsed.origin !== location.origin || parsed.pathname !== expectedPath || parsed.search) return;
    audioUrl = parsed.pathname;
  } catch { return; }
  const duration = Number(chapter.duration_seconds);
  if (!Number.isFinite(duration) || duration <= 0) return;
  const revisionId = job.episode?.revision_id || `streaming-${job.jobId}`;
  const scriptHash = `${revisionId}-${chapter.id}`;
  const title = job.episode?.title || job.request || 'Your episode';
  const playable = {
    id: job.jobId, kind: 'episode', title, revision_id: revisionId, script_hash: scriptHash,
    requested_minutes: job.minutes, duration_minutes: duration / 60, audio_status: 'recorded',
    audio: { status: 'recorded', revision_id: revisionId, script_hash: scriptHash,
      url: audioUrl, duration_seconds: duration },
    chapters: [{ id: chapter.id, title: chapter.title || 'Chapter 1', start_seconds: 0,
      end_seconds: duration, audio_start_seconds: 0, audio_duration_seconds: duration }],
  };
  player.playRecording(playable, { chapterId: chapter.id });
}

function renderEpisodeMakingPage() {
  renderEpisodeMakingScreen({ content, node, link, job: app.episodeJob, navigate,
    onCancel: cancelEpisodeJob, onRetry: episodeMakingRetry,
    onPlayChapter: chapter => playStreamingChapter(app.episodeJob, chapter) });
}


  return {
    renderEpisodeRequestPage,
    episodeProfileTopic,
    isEpisodeInDailyBriefing,
    setEpisodeDailyBriefing,
    openEpisodeStyleSheet,
    episodeModeStyle,
    startEpisodeJob,
    announceEpisodeStage,
    pollEpisodeJob,
    episodeMakingRetry,
    updateEpisodeMakingView,
    playStreamingChapter,
    renderEpisodeMakingPage,
  };
}
