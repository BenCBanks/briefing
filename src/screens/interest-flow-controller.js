export function createInterestFlowController(app) {
  const { content, node, navigate, rootForHash, focusToken, back, renderInterestDescribeScreen, dailyTimeValue, DAILY_TIME_KEY, interpret, buildChipLabel, clearFirstRunBuild, interpretProfile, defaultProfile, saveProfile, writeFirstRunBuild, profileBuildKey, FIRST_EDITION_WELCOME_PENDING_KEY, FIRST_EDITION_WELCOME_KEY, syncProfile, pendingTopicsUpdate, setTopicsProgress, topicsProgressFor, loadPack, validatePack, currentEdition, TOPICS_DRAFT_KEY, TOPICS_PENDING_KEY, BUILD_GENERAL_FALLBACK_KEY, showGeneralEditionFallback, renderInterestBuildScreen, renderInterestConfirmScreen, route, showToast } = app;
function interestOriginHash() {
  return history.state?.routeContext?.interestReturnHash || '#today';
}

function navigateInterest(hash) {
  const routeContext = history.state?.routeContext || {};
  const currentHash = location.hash || '#today';
  navigate(hash, {
    root: routeContext.root || rootForHash(currentHash),
    parentHash: currentHash,
    parentScroll: window.scrollY,
    parentFocus: focusToken(),
    interestReturnHash: routeContext.interestReturnHash || (currentHash === '#preferences' ? '#preferences' : '#today'),
  });
}

function renderInterestTopbar(step) {
  const context = history.state?.routeContext || {};
  const parent = context.parentHash;
  if (!parent || context.firstRun) return;
  const label = parent === '#preferences' ? 'Settings' : parent === '#interests/confirm' ? 'What I heard' : 'Describe';
  const sticky = node('div', null, 'reader-sticky interest-reader-sticky');
  const backButton = node('button', `‹ ${label}`, 'reader-back');
  backButton.type = 'button';
  backButton.addEventListener('click', back);
  sticky.append(backButton, node('span', step, 'interest-step'));
  content.append(sticky);
}

function renderInterestDescribe() {
  renderInterestDescribeScreen({
    content, node, draft: app.interestDraft, profile: app.interestProfile, dailyTime: dailyTimeValue(),
    onDailyTimeChange: value => { try { localStorage.setItem(DAILY_TIME_KEY, value); } catch { /* Keep this visit's choice. */ } },
    interpret, topics: app.pack.topics, minutes: app.state.durationMinutes, errorMessage: app.interestInterpretError,
    onBuild: words => {
      app.interestInterpretError = '';
      app.interestDraft = { profile: { ...(app.interestProfile || {}), words } };
      navigate('#interests/build', { root: 'today', parentHash: '#interests/describe', firstRun: true });
      void runFirstBuild(words);
    },
  });
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function renderInterestBuild() {
  renderInterestBuildScreen({
    content, node, profile: app.interestDraft?.profile || app.interestProfile, question: app.buildQuestion, buildChipLabel,
    onRemoveTopic: removeBuildTopic,
    onEditWords: () => { clearFirstRunBuild(); app.buildInProgress = false; navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true }); },
    onGoToday: () => navigate('#today', { root: 'today', parentHash: null }),
    scheduleTimeout: callback => {
      if (app.buildTimeoutHandle) return;
      app.buildTimeoutHandle = setTimeout(() => { app.buildTimeoutHandle = null; callback(); }, 20000);
    },
  });
}

function setBuildStep(index, found = null, writingMessage = null) {
  const labels = ['Understanding your words', 'Finding today’s stories', 'Writing your briefing'];
  const steps = [...content.querySelectorAll('.build-progress-step')];
  steps.forEach((step, current) => {
    if (labels[current]) step.textContent = labels[current];
    step.setAttribute('aria-current', current === index ? 'step' : 'false');
    step.classList.toggle('is-complete', current < index);
  });
  if (index === 1 && found != null && steps[1]) steps[1].textContent = `Finding today’s stories (${found} found)`;
  if (index === 2 && steps[2]) {
    const match = String(writingMessage || '').match(/writing\s+story\s+(?:candidate\s+)?(\d+)\s+of\s+(\d+)/i);
    if (match) steps[2].textContent = `Writing story ${match[1]} of ${match[2]}`;
  }
}

async function runFirstBuild(words) {
  const attempt = ++app.buildAttempt;
  app.buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); app.buildQuestion = '';
  renderInterestBuild();
  try {
    const result = await interpretProfile(words, { topics: app.pack.topics, minutes: app.state.durationMinutes });
    if (attempt !== app.buildAttempt) return;
    if (result.fallbackError || !result.profile?.topics?.length) {
      app.interestDraft = { profile: { ...(app.interestProfile || defaultProfile(app.pack.topics)), words } };
      app.interestInterpretError = result.fallbackError ? 'I could not pick out topics. Try naming a few, like payment rules.' : 'I could not pick out topics. Try naming a few, like payment rules.';
      navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true }); return;
    }
    app.buildQuestion = result.question || '';
    app.interestProfile = saveProfile(result.profile); app.interestDraft = { profile: app.interestProfile };
    writeFirstRunBuild(app.interestProfile);
    sessionStorage.setItem(FIRST_EDITION_WELCOME_PENDING_KEY, profileBuildKey(app.interestProfile));
    renderInterestBuild(); setBuildStep(1);
    await startBuildGeneration(app.interestProfile, attempt, { firstRun: true });
  } catch {
    if (attempt !== app.buildAttempt) return;
    app.interestDraft = { profile: { ...(app.interestProfile || defaultProfile(app.pack.topics)), words } };
    app.interestInterpretError = 'I could not pick out topics. Try naming a few, like payment rules.';
    navigate('#interests/describe', { root: 'today', parentHash: '#interests/build', firstRun: true });
  }
}

async function startBuildGeneration(profile, parentAttempt = null, options = {}) {
  const attempt = parentAttempt ?? ++app.buildAttempt;
  app.buildInProgress = true; app.topicsError = '';
  if (pendingTopicsUpdate()) setTopicsProgress('Saving your interests…');
  app.buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); app.buildStartedAt = Date.now();
  if (!options.inline) { renderInterestBuild(); setBuildStep(1); }
  try {
    try { await syncProfile(profile); } catch (error) { if (pendingTopicsUpdate()) throw error; }
    if (attempt !== app.buildAttempt) return;
    if (pendingTopicsUpdate()) setTopicsProgress('Finding stories…');
    let jobId = options.resumeJobId || null;
    if (!jobId) {
      const response = await fetch('/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile }) });
      if (!response.ok) throw new Error('generation_failed');
      const started = await response.json();
      const jobValue = started.job ?? started.job_id;
      jobId = typeof jobValue === 'string' || typeof jobValue === 'number' ? String(jobValue) : jobValue?.id || jobValue?.job_id;
      if (jobId && options.firstRun) writeFirstRunBuild(profile, jobId);
    }
    if (!jobId) throw new Error('generation_failed');
    // Generation can take up to three minutes. Keep polling through that window,
    // with room for request and scheduling overhead.
    for (let poll = 0; poll < 210; poll += 1) {
      if (attempt !== app.buildAttempt) return;
      const jobResponse = await fetch(`/api/generate/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
      if (!jobResponse.ok) throw new Error('generation_failed');
      const job = await jobResponse.json();
      if (attempt !== app.buildAttempt) return;
      const progress = job.progress || {};
      const stepName = typeof progress === 'string' ? progress.toLowerCase()
        : String(progress.stage || progress.step || '').toLowerCase();
      const found = typeof progress === 'object'
        ? (progress.found ?? progress.stories_found ?? progress.story_count ?? progress.count)
        : job.found;
      if (/writing|write/.test(stepName)) setBuildStep(2, null, progress.message || job.message);
      else if (/scoring|scorecard|edition_writer/.test(stepName)) setBuildStep(2);
      else if (/finding|story|fetching|feed_fetch/.test(stepName) || found != null) setBuildStep(1, found);
      if (pendingTopicsUpdate()) setTopicsProgress(topicsProgressFor(stepName, found, progress, job));
      if (job.status === 'failed' || job.status === 'error') throw new Error('generation_failed');
      if (job.status === 'ready' || job.status === 'complete' || job.edition_ready) {
        setBuildStep(3);
        if (pendingTopicsUpdate()) setTopicsProgress('Checking your new briefing…');
        let loaded = await loadPack(true);
        const ready = typeof job.edition_ready === 'object' ? job.edition_ready : null;
        if (ready?.edition) loaded.editions = [ready.edition, ...loaded.editions.filter(item => item.id !== ready.edition.id)];
        if (Array.isArray(ready?.sources)) loaded.sources = [...new Map([...loaded.sources, ...ready.sources].map(item => [item.id, item])).values()];
        if (attempt !== app.buildAttempt) return;
        const pending = pendingTopicsUpdate();
        if (pending) {
          const fresh = ready?.edition;
          if (!fresh?.generated || pending.revisions.includes(fresh.id + ':' + fresh.revision_id)) throw new Error('stale_edition');
          // Only the completed job's daily may become Today, even if an older response has a later date.
          loaded.editions = [fresh, ...loaded.editions.filter(item => item.kind !== 'daily' || item.local_date < fresh.local_date)];
        }
        app.pack = validatePack(loaded);
        if (!currentEdition('daily')) throw new Error('generation_failed');
        app.buildInProgress = false; app.topicsUpdating = false; app.topicsProgress = ''; app.topicsError = '';
        localStorage.removeItem(TOPICS_PENDING_KEY);
        sessionStorage.removeItem(TOPICS_DRAFT_KEY); app.topicsDraft = null;
        localStorage.setItem('briefing:v1:built-profile-key', profileBuildKey(profile));
        if (sessionStorage.getItem(FIRST_EDITION_WELCOME_PENDING_KEY) === profileBuildKey(profile)) {
          sessionStorage.setItem(FIRST_EDITION_WELCOME_KEY, profileBuildKey(profile));
          sessionStorage.removeItem(FIRST_EDITION_WELCOME_PENDING_KEY);
        }
        localStorage.removeItem(BUILD_GENERAL_FALLBACK_KEY);
        if (options.firstRun) clearFirstRunBuild();
        app.buildNotice = ''; sessionStorage.removeItem('briefing:build-error'); clearTimeout(app.buildTimeoutHandle); app.buildTimeoutHandle = null;
        if (!options.inline || options.returnToday || location.hash === '#today' || location.hash === '#topics') navigate('#today', { root: 'today', parentHash: null });
        return;
      }
      await wait(1000);
    }
    throw new Error('generation_timeout');
  } catch {
    if (attempt !== app.buildAttempt) return;
    app.buildInProgress = false;
    if (options.firstRun) clearFirstRunBuild();
    if (pendingTopicsUpdate()) {
      app.topicsUpdating = false; app.topicsProgress = '';
      app.topicsError = "We couldn't build it right now. Your last briefing is still here.";
      if (location.hash === '#topics' || location.hash === '#today') route();
      return;
    }
    if (await showGeneralEditionFallback(profile, attempt)) return;
    clearTimeout(app.buildTimeoutHandle); app.buildTimeoutHandle = null;
    app.buildNotice = 'Today’s stories did not load'; sessionStorage.setItem('briefing:build-error', app.buildNotice);
    navigate('#today', { root: 'today', parentHash: null });
  }
}

function removeBuildTopic(id) {
  const profile = app.interestProfile || app.interestDraft?.profile;
  const index = profile.topics.findIndex(topic => topic.id === id);
  if (index < 0) return;
  const value = profile.topics.splice(index, 1)[0];
  profile.muted = [...(profile.muted || []).filter(item => item.label !== value.label), { label: value.label }];
  app.interestProfile = saveProfile(profile); app.interestDraft = { profile: app.interestProfile };
  if (app.removedBuildTopic) clearTimeout(app.removedBuildTopic.timer);
  const entry = { topic: { value, index }, timer: null };
  entry.timer = setTimeout(() => { if (app.removedBuildTopic === entry) app.removedBuildTopic = null; }, 4000);
  app.removedBuildTopic = entry;
  showToast(`Removed ${value.label}.`, { actionLabel: 'Undo', onAction: () => {
    if (app.removedBuildTopic !== entry) return;
    clearTimeout(entry.timer); app.removedBuildTopic = null;
    app.interestProfile.topics.splice(index, 0, value);
    app.interestProfile.muted = (app.interestProfile.muted || []).filter(item => item.label !== value.label);
    updateInterestMuted(app.interestProfile); app.interestProfile = saveProfile(app.interestProfile); app.interestDraft = { profile: app.interestProfile }; renderInterestBuild(); void startBuildGeneration(app.interestProfile);
  } });
  renderInterestBuild(); void startBuildGeneration(app.interestProfile);
}

function updateDescribeInterpretationState() {
  const words = document.querySelector('#interest-words');
  if (!words) return;
  const guidance = document.querySelector('#interest-guidance');
  const counter = document.querySelector('#interest-counter');
  const submit = document.querySelector('.interest-form-actions button[type="submit"]');
  const cancel = document.querySelector('.interest-cancel');
  const error = document.querySelector('.interest-form-error');
  const length = words.value.length;
  if (counter) counter.textContent = `${length.toLocaleString()} / 2,000 characters`;
  if (counter) counter.hidden = length < 1800;
  if (guidance) guidance.textContent = app.interestInterpretBusy ? 'Interpreting…' : app.interestInterpretStatus;
  if (submit) submit.disabled = false;
  words.disabled = app.interestInterpretBusy;
  if (cancel) cancel.hidden = !app.interestInterpretBusy;
  if (error) error.textContent = app.interestInterpretError || '';
}

function cancelInterestInterpretation() {
  app.interestInterpretController?.abort();
  app.interestInterpretController = null;
  app.interestInterpretBusy = false;
  app.interestInterpretError = null;
  app.interestInterpretStatus = 'Canceled. Your words are still here.';
  if (location.hash === '#interests/confirm') renderInterestConfirm();
  else updateDescribeInterpretationState();
}

async function runInterestInterpretation(words, destination, localOnly = false) {
  app.interestInterpretController?.abort();
  const controller = new AbortController();
  app.interestInterpretController = controller;
  app.interestInterpretBusy = true;
  app.interestInterpretError = null;
  app.interestInterpretStatus = 'Interpreting…';
  if (destination === 'confirm') renderInterestConfirm();
  else updateDescribeInterpretationState();
  try {
    const result = await interpretProfile(words, {
      topics: app.pack.topics,
      minutes: app.state.durationMinutes,
      signal: controller.signal,
      localOnly,
    });
    if (app.interestInterpretController !== controller) return;
    app.interestDraft = result;
    app.interestInterpretError = result.fallbackError || null;
    app.interestInterpretStatus = '';
    app.interestInterpretBusy = false;
    app.interestInterpretController = null;
    if (destination === 'describe') navigateInterest('#interests/confirm');
    else renderInterestConfirm();
  } catch (error) {
    if (app.interestInterpretController !== controller) return;
    app.interestInterpretController = null;
    app.interestInterpretBusy = false;
    app.interestInterpretError = 'Something went wrong while interpreting your words. You can retry or use basic matching.';
    app.interestInterpretStatus = '';
    if (destination === 'confirm') renderInterestConfirm();
    else updateDescribeInterpretationState();
  }
}

function updateInterestMuted(profile) {
  const labels = new Map();
  for (const item of profile.muted || []) {
    const label = String(item?.label || '').trim();
    if (label) labels.set(label.toLocaleLowerCase(), { label });
  }
  profile.muted = [...labels.values()];
}

function persistInterestDraft(profile) {
  updateInterestMuted(profile);
  app.interestProfile = saveProfile(profile);
}

function renderInterestConfirm() {
  if (!app.interestDraft) {
    const words = app.interestProfile?.words || '';
    app.interestDraft = interpret(words, { topics: app.pack.topics, minutes: app.state.durationMinutes });
  }
  renderInterestConfirmScreen({
    content, node, draft: app.interestDraft, busy: app.interestInterpretBusy, errorMessage: app.interestInterpretError,
    flags: app.interestDraft.flags, renderTopbar: renderInterestTopbar,
    onCancel: cancelInterestInterpretation,
    onRetry: () => void runInterestInterpretation(app.interestDraft.profile.words, 'confirm'),
    onBasic: () => void runInterestInterpretation(app.interestDraft.profile.words, 'confirm', true),
    onUpdate: (update, persist = true) => { update(); if (persist) persistInterestDraft(app.interestDraft.profile); renderInterestConfirm(); },
    onEdit: () => navigateInterest('#interests/describe'),
    onSave: profile => { persistInterestDraft(profile); app.interestDraft = null; navigate(interestOriginHash()); },
  });
}


  return {
    interestOriginHash,
    navigateInterest,
    renderInterestTopbar,
    renderInterestDescribe,
    renderInterestBuild,
    setBuildStep,
    runFirstBuild,
    startBuildGeneration,
    removeBuildTopic,
    updateDescribeInterpretationState,
    cancelInterestInterpretation,
    runInterestInterpretation,
    updateInterestMuted,
    persistInterestDraft,
    renderInterestConfirm,
  };
}
