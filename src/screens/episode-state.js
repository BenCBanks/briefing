export function createEpisodeStateController(app) {
  function readFirstRunBuild() {
    try {
      const pending = JSON.parse(app.localStorage.getItem(app.FIRST_RUN_BUILD_KEY));
      return pending && pending.version === 1 && typeof pending.profileKey === 'string'
        ? { version: 1, profileKey: pending.profileKey, jobId: typeof pending.jobId === 'string' ? pending.jobId : null }
        : null;
    } catch { return null; }
  }

  function writeFirstRunBuild(profile, jobId = null) {
    const pending = { version: 1, profileKey: app.profileBuildKey(profile), jobId: jobId ? String(jobId) : null };
    try { app.localStorage.setItem(app.FIRST_RUN_BUILD_KEY, JSON.stringify(pending)); } catch { /* Keep the current build usable in memory. */ }
    return pending;
  }

  function clearFirstRunBuild() {
    try { app.localStorage.removeItem(app.FIRST_RUN_BUILD_KEY); } catch { /* The server job may still finish for this visit. */ }
  }

  function dailyTimeValue() {
    try {
      const value = app.localStorage.getItem(app.DAILY_TIME_KEY);
      return /^([01]\d|2[0-3]):[0-5]\d$/.test(value || '') ? value : '05:00';
    } catch { return '05:00'; }
  }

  function formatEasternTime(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    const hour = Number(match?.[1] ?? 5);
    const minute = Number(match?.[2] ?? 0);
    const hour12 = hour % 12 || 12;
    return `${hour12}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'am' : 'pm'} ET`;
  }

  function nextDailyRunTime(value, now = new Date()) {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    const runHour = Number(match?.[1] ?? 5);
    const runMinute = Number(match?.[2] ?? 0);
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(now);
    const get = type => Number(parts.find(part => part.type === type)?.value);
    const year = get('year'); const month = get('month'); const day = get('day');
    const isAfterRun = get('hour') * 60 + get('minute') >= runHour * 60 + runMinute;
    const date = new Date(Date.UTC(year, month - 1, day + Number(isAfterRun), 12));
    const dateLabel = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', weekday: 'long', month: 'long', day: 'numeric',
    }).format(date);
    return `${dateLabel} at ${formatEasternTime(`${String(runHour).padStart(2, '0')}:${String(runMinute).padStart(2, '0')}`)}`;
  }

  function readLastEpisodeLength() {
    try {
      const value = Number(app.localStorage.getItem(app.EPISODE_LENGTH_KEY));
      return [3, 5, 10, 15].includes(value) ? value : 5;
    } catch { return 5; }
  }

  function rememberEpisodeLength(value) {
    app.episodeDraft.minutes = value;
    try { app.localStorage.setItem(app.EPISODE_LENGTH_KEY, String(value)); } catch { /* Keep the choice for this visit. */ }
  }

  function episodeUnheardIds() {
    try {
      const ids = JSON.parse(app.localStorage.getItem(app.EPISODE_UNHEARD_KEY) || '[]');
      return Array.isArray(ids) ? ids.filter(id => typeof id === 'string').slice(-100) : [];
    } catch { return []; }
  }

  function saveEpisodeUnheardIds(ids) {
    try { app.localStorage.setItem(app.EPISODE_UNHEARD_KEY, JSON.stringify([...new Set(ids)].slice(-100))); }
    catch { /* The badge is a convenience; episode access still works without storage. */ }
  }

  function syncOnDemandBadge(jobs = app.episodeHistorySnapshot?.jobs || []) {
    const tab = app.nav.find(item => item.dataset.nav === 'on-demand');
    const badge = tab?.querySelector('[data-on-demand-badge]');
    if (!tab || !badge) return;
    const activeId = app.episodeJob?.jobId;
    const making = Boolean(app.episodeJob && !app.episodeJob.done && !app.episodeJob.failed) || jobs.some(job => {
      const id = job.job_id || job.id;
      return id !== activeId && app.episodeHistoryState(job).key === 'making';
    });
    const unheard = episodeUnheardIds();
    const ready = Boolean(app.episodeJob?.done && unheard.includes(app.episodeJob.jobId)) ||
      jobs.some(job => unheard.includes(job.job_id || job.id) && app.episodeHistoryState(job).key === 'ready');
    const state = making ? 'making' : ready ? 'ready' : '';
    badge.hidden = !state;
    badge.dataset.state = state;
    badge.textContent = state === 'ready' ? '✓' : '';
    tab.setAttribute('aria-label', state === 'making' ? 'On demand, episode in progress' : state === 'ready' ? 'On demand, episode ready to play' : 'On demand');
  }

  function markEpisodeUnheard(id) {
    if (!id) return;
    saveEpisodeUnheardIds([...episodeUnheardIds(), id]);
    syncOnDemandBadge();
  }

  function markEpisodeHeard(id) {
    if (!id) return;
    saveEpisodeUnheardIds(episodeUnheardIds().filter(unheard => unheard !== id));
    syncOnDemandBadge();
  }

  return {
    readFirstRunBuild, writeFirstRunBuild, clearFirstRunBuild,
    dailyTimeValue, formatEasternTime, nextDailyRunTime,
    readLastEpisodeLength, rememberEpisodeLength,
    episodeUnheardIds, saveEpisodeUnheardIds, syncOnDemandBadge, markEpisodeUnheard, markEpisodeHeard,
  };
}
