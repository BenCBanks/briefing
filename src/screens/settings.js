import { narratorDisplayName } from '../player.js';
import { mountAccountSettings } from './account.js';

export function renderSettingsScreen({
  content, node, link, dailyTimeValue, formatEasternTime, nextDailyRunTime, interestProfile,
  navigate, resetBehaviorWeighting, player, NARRATOR_KEY, refreshLastUpdated,
  getState, setState, saveState, localStorage, sessionStorage, resetChoices,
}) {
  document.querySelector('[data-page-title]').textContent = '';
  content.append(node('h1', 'Settings'));
  const quickSettings = node('section', null, 'settings-quick-rows');
  quickSettings.setAttribute('aria-label', 'Briefing settings');
  const dailyRow = node('div', null, 'settings-quick-row settings-daily-row');
  dailyRow.setAttribute('aria-readonly', 'true');
  dailyRow.append(node('span', 'Daily briefing time (ET)', 'settings-row-title'), node('span', formatEasternTime(dailyTimeValue()), 'settings-row-value'));
  quickSettings.append(dailyRow);
  const nextRunRow = node('div', null, 'settings-quick-row settings-next-run-row');
  nextRunRow.setAttribute('aria-readonly', 'true');
  nextRunRow.append(node('span', 'Next run', 'settings-row-title'), node('span', nextDailyRunTime(dailyTimeValue()), 'settings-row-value settings-next-run-value'));
  quickSettings.append(nextRunRow);
  let selectedNarrator = /^[A-F]$/.test(localStorage.getItem(NARRATOR_KEY) || '')
    ? localStorage.getItem(NARRATOR_KEY) : 'A';
  let hasVoicePreference = /^[A-F]$/.test(localStorage.getItem(NARRATOR_KEY) || '');
  const voiceRow = node('button', null, 'settings-quick-row settings-voice-row'); voiceRow.type = 'button';
  voiceRow.append(node('span', 'Voice', 'settings-row-title'));
  const voiceValue = node('span', hasVoicePreference ? narratorDisplayName(selectedNarrator) : 'Choose a voice', 'settings-row-value');
  voiceRow.append(voiceValue); quickSettings.append(voiceRow);
  const spendingRow = node('div', null, 'settings-quick-row settings-spending-row');
  spendingRow.setAttribute('aria-readonly', 'true');
  spendingRow.append(node('span', 'Spending', 'settings-row-title'), node('span', 'Read only', 'settings-row-value'));
  spendingRow.append(node('p', 'Episode limits help keep the service available.', 'settings-row-note')); quickSettings.append(spendingRow);
  const aboutRow = document.createElement('details'); aboutRow.className = 'settings-about-row';
  aboutRow.append(node('summary', 'About', 'settings-row-title'));
  aboutRow.append(node('p', 'A daily five-minute briefing on your topics, ready by 6 am ET. On demand briefings let you explore any topic and choose the length.', 'settings-row-note'));
  quickSettings.append(aboutRow); content.append(quickSettings);
  const interests = node('section', null, 'preferences-interests');
  interests.append(node('h2', 'Your interests'));
  interests.append(node('p', interestProfile?.topics?.length ? interestProfile.topics.filter(topic => !topic.exclude.length).map(topic => topic.label).join(' · ') || 'No active topics yet.' : 'Describe what you want to follow in your own words.'));
  interests.append(link('Edit in my own words →', '#interests', 'button-link'));
  const rebuild = node('button', 'Update my briefing', 'button-link'); rebuild.type = 'button'; rebuild.addEventListener('click', () => { navigate('#topics', { root: 'topics' }); });
  interests.append(rebuild);
  content.append(interests);
  const behavior = node('section', null, 'preferences-behavior');
  behavior.append(node('h2', 'Daily topic balance'));
  behavior.append(node('p', 'Recent listening, saves and skips can shift each topic’s share. Raw reading signals stay on this device; only a bounded topic weight is synced for the scheduled briefing.'));
  const resetBehavior = node('button', 'Reset topic balance'); resetBehavior.type = 'button';
  resetBehavior.addEventListener('click', () => {
    resetBehaviorWeighting();
    const status = node('p', 'Topic balance reset to even shares.', 'local-note');
    status.setAttribute('role', 'status');
    behavior.querySelector('[role="status"]')?.remove();
    behavior.append(status);
  });
  behavior.append(resetBehavior);
  content.append(behavior);
  const voice = node('section', null, 'preferences-voice'); voice.id = 'settings-voice'; voice.tabIndex = -1;
  const voiceHeading = node('h2', hasVoicePreference ? 'Narrator' : 'Choose your voice');
  voice.append(voiceHeading);
  voiceRow.addEventListener('click', () => { voice.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  const voiceStatus = node('p', '', 'narrator-status'); voiceStatus.setAttribute('role', 'status'); voiceStatus.setAttribute('aria-live', 'polite');
  const retrySamples = node('button', 'Try again', 'narrator-retry'); retrySamples.type = 'button'; retrySamples.hidden = true;
  retrySamples.addEventListener('click', () => { retrySamples.hidden = true; void loadAuditionSamples(); });
  const sampleList = node('ol', null, 'narrator-samples');
  const preview = document.createElement('audio'); preview.preload = 'none'; preview.hidden = true; preview.setAttribute('aria-label', 'Narrator sample audio');
  let auditionSamples = [];
  let activePreviewButton = null;
  async function loadVoicePreference() {
    try {
      const response = await fetch('/api/voice', { cache: 'no-store' });
      if (!response.ok) return;
      const preference = await response.json();
      if (!/^[A-F]$/.test(preference?.letter || '')) return;
      selectedNarrator = preference.letter;
      if (player.narratorSample !== selectedNarrator) player.setNarratorSample(selectedNarrator);
      if (!hasVoicePreference && selectedNarrator !== 'A') hasVoicePreference = true;
      voiceHeading.textContent = hasVoicePreference ? 'Narrator' : 'Choose your voice';
      voiceValue.textContent = hasVoicePreference ? narratorDisplayName(selectedNarrator) : 'Choose a voice';
    } catch { /* Keep the default when the saved setting cannot load. */ }
  }
  const voicePreferenceReady = loadVoicePreference();
  async function loadAuditionSamples() {
    voiceStatus.textContent = 'Loading voice samples…';
    try {
      await voicePreferenceReady;
      const response = await fetch('/audition-audio/manifest.json', { cache: 'no-store' });
      if (!response.ok) throw Error('samples unavailable');
      const manifest = await response.json();
      auditionSamples = (Array.isArray(manifest.samples) ? manifest.samples : [])
        .filter(sample => /^[A-F]$/.test(sample?.label || '') && typeof sample.voice === 'string' && /^[a-z0-9][a-z0-9._-]*\.wav$/i.test(sample.file || ''))
        .sort((a, b) => a.label.localeCompare(b.label)).slice(0, 6);
      if (auditionSamples.length !== 6 || new Set(auditionSamples.map(sample => sample.label)).size !== 6 || auditionSamples.map(sample => sample.label).join('') !== 'ABCDEF') throw Error('samples unavailable');
      if (!voice.isConnected) return;
      sampleList.replaceChildren();
      auditionSamples.forEach(sample => {
        const row = node('li', null, 'narrator-sample');
        const name = node('span', `Sample ${sample.label}`);
        const actions = node('div', null, 'narrator-sample-actions');
        const play = node('button', 'Play'); play.type = 'button'; play.setAttribute('aria-label', `Play sample ${sample.label}`);
        play.addEventListener('click', async () => {
          if (activePreviewButton === play && !preview.paused) { preview.pause(); preview.currentTime = 0; play.textContent = 'Play'; play.setAttribute('aria-label', `Play sample ${sample.label}`); activePreviewButton = null; return; }
          if (activePreviewButton) { activePreviewButton.textContent = 'Play'; activePreviewButton.setAttribute('aria-label', `Play sample ${activePreviewButton.closest('.narrator-sample').querySelector('span').textContent.at(-1)}`); }
          preview.pause(); preview.src = `/audition-audio/${encodeURIComponent(sample.file)}`; preview.currentTime = 0;
          activePreviewButton = play; play.textContent = 'Pause'; play.setAttribute('aria-label', `Pause sample ${sample.label}`);
          try { await preview.play(); voiceStatus.textContent = `Playing ${narratorDisplayName(sample.label)}.`; }
          catch { play.textContent = 'Play'; play.setAttribute('aria-label', `Play sample ${sample.label}`); activePreviewButton = null; voiceStatus.textContent = `The ${narratorDisplayName(sample.label)} preview could not be played.`; }
        });
        const use = node('button', 'Use this voice', 'narrator-use'); use.type = 'button';
        const selected = hasVoicePreference && selectedNarrator === sample.label;
        use.setAttribute('aria-pressed', String(selected));
        if (selected) { use.textContent = 'Selected'; use.disabled = true; }
        use.addEventListener('click', async () => {
          use.disabled = true; voiceStatus.textContent = `Saving ${narratorDisplayName(sample.label)}…`;
          try {
            const result = await fetch('/api/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice_name: sample.voice }) });
            if (!result.ok) throw Error('voice could not be saved');
            hasVoicePreference = true;
            selectedNarrator = sample.label;
            localStorage.setItem(NARRATOR_KEY, sample.label);
            player.setNarratorSample(sample.label);
            voiceHeading.textContent = 'Narrator';
            voiceValue.textContent = narratorDisplayName(sample.label);
            sampleList.querySelectorAll('.narrator-use').forEach(button => { button.disabled = false; button.textContent = 'Use this voice'; button.setAttribute('aria-pressed', 'false'); });
            use.disabled = true; use.textContent = 'Selected'; use.setAttribute('aria-pressed', 'true');
            voiceStatus.textContent = `${narratorDisplayName(sample.label)} selected.`;
          } catch {
            use.disabled = false; voiceStatus.textContent = 'Voice could not be saved. Try again.';
          }
        });
        actions.append(play, use); row.append(name, actions); sampleList.append(row);
      });
      voiceStatus.textContent = '';
    } catch {
      if (voice.isConnected) { voiceStatus.textContent = 'Voice samples are unavailable right now.'; retrySamples.hidden = false; }
    }
  }
  preview.addEventListener('ended', () => { if (activePreviewButton) { activePreviewButton.textContent = 'Play'; activePreviewButton.setAttribute('aria-label', `Play sample ${activePreviewButton.closest('.narrator-sample').querySelector('span').textContent.at(-1)}`); } activePreviewButton = null; });
  preview.addEventListener('error', () => { if (activePreviewButton) { activePreviewButton.textContent = 'Play'; activePreviewButton.setAttribute('aria-label', `Play sample ${activePreviewButton.closest('.narrator-sample').querySelector('span').textContent.at(-1)}`); } activePreviewButton = null; voiceStatus.textContent = 'That voice sample could not be played.'; });
  const speedLabel = node('label', 'Listening speed'); speedLabel.htmlFor = 'voice-speed';
  const speedChoice = node('select'); speedChoice.id = 'voice-speed'; speedChoice.setAttribute('aria-label', 'Listening speed');
  for (const value of [0.75, 1, 1.25, 1.5, 2]) { const option = node('option', `${value}×`); option.value = String(value); speedChoice.append(option); }
  speedLabel.append(speedChoice);
  const voiceControls = node('div', null, 'voice-settings'); voiceControls.append(speedLabel);
  player.configureVoiceSettings(null, speedChoice, null);
  const disclosure = document.createElement('details'); disclosure.className = 'narrator-disclosure';
  disclosure.append(node('summary', 'About the AI voice'), node('p', 'The narration uses an AI-generated voice. You can listen to each sample before choosing one.'));
  voice.append(voiceStatus, retrySamples, sampleList, preview, voiceControls, disclosure);
  content.append(voice);
  loadAuditionSamples();
  const lastUpdated = node('p', 'Updated not yet', 'local-note');
  lastUpdated.classList.add('settings-last-updated');
  content.append(lastUpdated);
  refreshLastUpdated(lastUpdated);
  content.append(link('Corrections', '#corrections', 'settings-corrections-link'));
  const form = node('form', null, 'settings-list');
  const timezone = node('fieldset'); timezone.append(node('legend', 'Time zone'));
  const zoneLabel = node('label', 'Your IANA time zone');
  const zone = node('input'); zone.type = 'text'; zone.name = 'timezone'; zone.value = getState().timezone; zone.setAttribute('aria-label', 'Time zone'); zone.autocomplete = 'off';
  timezone.append(zoneLabel, zone);
  const message = node('p', '', 'error'); message.setAttribute('role', 'status');
  const submit = node('button', 'Save time zone', 'primary'); submit.type = 'submit';
  form.append(timezone, message, submit);
  form.addEventListener('submit', event => {
    event.preventDefault();
    try { new Intl.DateTimeFormat('en-US', { timeZone: zone.value.trim() }); }
    catch { message.textContent = 'Enter a valid time zone, such as America/New_York.'; return; }
    setState(saveState({ ...getState(), timezone: zone.value.trim() }));
    sessionStorage.setItem('briefing:settings-saved', 'Time zone saved.');
    const parent = history.state?.routeContext?.parentHash;
    if (parent && history.length > 1) history.back();
    else navigate(history.state?.routeContext?.root === 'library' ? '#library' : history.state?.routeContext?.root === 'topics' ? '#topics' : '#today', { root: history.state?.routeContext?.root || 'today', parentHash: null });
  });
  content.append(form);
  const reset = node('button', 'Reset this browser’s choices'); reset.type = 'button';
  reset.addEventListener('click', resetChoices);
  content.append(node('hr', null, 'rule'), reset, node('p', 'Reset clears local choices, saves and listening progress. It does not affect another device.', 'local-note'));
  void mountAccountSettings({ content, node });
}


