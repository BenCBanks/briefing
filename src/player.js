import { audioScriptParagraphs, audioScriptText, withoutCitationNotes } from './audio-script.js';

const CHUNK_LIMIT = 180;
const VOICE_KEY = 'briefing:voice-uri';
const SPEED_KEY = 'briefing:playback-speed';
const SPEED_OPTIONS = [0.75, 1, 1.25, 1.5, 2];
function readPlaybackSpeed() {
  try {
    const value = Number(localStorage.getItem(SPEED_KEY));
    return SPEED_OPTIONS.includes(value) ? value : 1;
  } catch { return 1; }
}
function savePlaybackSpeed(value) {
  try { localStorage.setItem(SPEED_KEY, String(value)); } catch { /* Listening still works when device storage is unavailable. */ }
}
export const NARRATOR_KEY = 'briefing:narrator-sample';
const MEDIA_ARTWORK = [{ src: '/content/briefing-artwork.png', sizes: '1024x1024', type: 'image/png' }];
const formatTime = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  return `${minutes}:${String(value % 60).padStart(2, '0')}`;
};

function resolvedForCurrentRecording(item, correction) {
  return correction.audio_status === 'recorded' &&
    typeof correction.previous_revision_id === 'string' && correction.previous_revision_id.length > 0 &&
    typeof correction.revision_id === 'string' && correction.revision_id.length > 0 &&
    correction.previous_revision_id !== correction.revision_id &&
    correction.previous_revision_id !== item?.revision_id &&
    item?.audio?.status === 'recorded' &&
    (!item.audio_status || item.audio_status === 'recorded') &&
    hasCurrentRecordingIdentity(item);
}

function materialCorrection(item, recordingMode = false) {
  const material = item?.corrections?.filter(correction => correction.severity === 'material' &&
    !(recordingMode && resolvedForCurrentRecording(item, correction))) || [];
  return (item?.kind === 'episode' ? material.some(correction => !correction.section_ids?.length) : material.length > 0) || item?.audio?.state === 'superseded' || item?.audio?.superseded === true;
}

function correctedChapter(item, chapter) {
  return (item?.corrections || []).some(correction => correction.severity === 'material' &&
    !resolvedForCurrentRecording(item, correction) &&
    (!correction.section_ids?.length || correction.section_ids.includes(chapter?.id)));
}

function chaptersFor(item) {
  const sections = Array.isArray(item?.sections) ? item.sections : [];
  const quiet = new Set((item?.coverage || []).filter(row => ['quiet', 'limited'].includes(row.state)).map(row => row.topic_id));
  const visible = sections.filter(section => !(section.topic_ids || []).length || section.topic_ids.some(id => !quiet.has(id)));
  return (visible.length ? visible : sections).map((section, index) => ({
    id: section.id || `chapter-${index + 1}`,
    title: section.heading || `Chapter ${index + 1}`,
    section,
    index,
  }));
}

function hasCurrentRecordingIdentity(candidate) {
  const audio = candidate?.audio;
  return typeof candidate?.revision_id === 'string' && candidate.revision_id.length > 0 &&
    typeof candidate?.script_hash === 'string' && candidate.script_hash.length > 0 &&
    audio?.revision_id === candidate.revision_id &&
    audio?.script_hash === candidate.script_hash &&
    typeof audio?.url === 'string' && audio.url.length > 0;
}

export function recordingSession(item, previous, chapterId = null) {
  const chapters = Array.isArray(item?.chapters) ? item.chapters : [];
  const targetIndex = chapterId ? chapters.findIndex(chapter => chapter.id === chapterId) : 0;
  const chapterIndex = Math.max(0, targetIndex);
  const voiceName = item?.audio?.voice_name ?? item?.voice_name ?? null;
  const previousVoiceName = previous?.audio?.voice_name ?? previous?.voice_name ?? null;
  const currentIdentity = hasCurrentRecordingIdentity(item);
  const previousIdentity = hasCurrentRecordingIdentity(previous);
  return {
    playable: currentIdentity,
    reusable: typeof item?.id === 'string' && item.id.length > 0 &&
      currentIdentity && previousIdentity &&
      previous?.id === item.id && previous?.revision_id === item.revision_id &&
      previous?.script_hash === item.script_hash &&
      previous?.audio?.url === item?.audio?.url && previousVoiceName === voiceName,
    item: { ...item, sections: chapters.map(chapter => ({
      id: chapter.id, heading: chapter.title, body: '',
      start_seconds: chapter.start_seconds, end_seconds: chapter.end_seconds,
    })) },
    chapterIndex,
    startSeconds: Number(chapters[chapterIndex]?.start_seconds) || 0,
  };
}

export function isCurrentRecordingEvent(activeRecording, eventRecording, recordingMode) {
  return recordingMode && activeRecording === eventRecording;
}

// Reed · generated citation markers are clickable in text and silent in narration.
function chapterText(chapter) {
  const script = audioScriptText(chapter.section);
  if (script) return script;
  return withoutCitationNotes(chapter.section?.body)
    .replace(/\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, '')
    .replace(/(^|\n\s*)(?:What happened|Why it matters|Things to consider)\s*:\s*/gim, '$1');
}

function chapterParagraphs(chapter) {
  return audioScriptText(chapter.section)
    ? audioScriptParagraphs(chapter.section)
    : chapterText(chapter).split(/\n\s*\n/).map(value => value.trim()).filter(Boolean);
}

export function cleanSpeechText(text) {
  return withoutCitationNotes(text)
    .replace(/\[\s*(?:\d+(?:\s*,\s*\d+)*|[A-Z]{1,4}\d*)\s*\]/g, '')
    .replace(/\b(?:read details? at|read|visit|see|source|more details? at)\s*:?\s+https?:\/\/\S+/gi, '')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/\bWhat happened\s*:\s*/gi, '')
    .replace(/\bWhy it matters\s*:\s*/gi, 'Why it matters. ')
    .replace(/\bThings to consider\s*:\s*/gi, 'Things to consider. ')
    .replace(/\s+([,.;!?])/g, '$1').replace(/\s{2,}/g, ' ').trim();
}

export function preferredEnglishVoice(voices) {
  const rejected = /novelty|robot|whisper|singing|alien|monster|zombie|funny|comic|fred|trinoid|bells|boing|bubbles|cellos|good news|bad news|junior|superstar|wobble|zarvox/i;
  const preferred = /neural|premium|enhanced|google|siri|natural|microsoft|aria|jenny|ava|samantha/i;
  return (voices || []).filter(voice => /^en(?:[-_]|$)/i.test(voice.lang || '') && !rejected.test(voice.name || ''))
    .sort((a, b) => Number(preferred.test(b.name)) - Number(preferred.test(a.name)) || Number(b.default) - Number(a.default))[0] || null;
}

export function narrationUnits(item) {
  const sections = chaptersFor(item);
  const date = item?.local_date ? new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric' }).format(new Date(`${item.local_date}T12:00:00Z`)) : '';
  const topics = [...new Set(item?.narrationTopics || [])].filter(Boolean);
  const units = [];
  if (date || topics.length) units.push({ text: `Here's your briefing${date ? ` for ${date}` : ''}${topics.length ? `. Today we're following ${topics.join(', ')}` : ''}.`, chapterIndex: 0, paragraphIndex: 0, intro: true });
  sections.forEach((chapter, chapterIndex) => {
    const heading = cleanSpeechText(chapter.title).replace(/[.!?]+$/, '');
    const transition = chapterIndex === 0 ? `${heading}.` : `Next story: ${heading}.`;
    splitSpeechText(transition).forEach(text => units.push({ text: cleanSpeechText(text), chapterIndex, paragraphIndex: 0, transition: true }));
    chapterParagraphs(chapter).forEach((paragraph, paragraphIndex) => {
      splitSpeechText(cleanSpeechText(paragraph)).forEach(text => units.push({ text, chapterIndex, paragraphIndex }));
    });
  });
  return units;
}

const playGlyph = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5.8c0-.8.9-1.3 1.6-.9l9.1 6.2a1.1 1.1 0 0 1 0 1.8l-9.1 6.2c-.7.5-1.6 0-1.6-.9V5.8Z" fill="currentColor" stroke="none"/></svg>';
const pauseGlyph = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 5.5h3.6v13H7zM13.4 5.5H17v13h-3.6z" fill="currentColor" stroke="none"/></svg>';

export function splitSpeechText(text, maxLength = CHUNK_LIMIT) {
  const sentences = String(text || '').match(/[^.!?]+[.!?]+(?:[”’"']+)?|[^.!?]+$/g) || [];
  const chunks = [];
  let chunk = '';
  for (const raw of sentences) {
    const sentence = raw.trim();
    if (!sentence) continue;
    if (sentence.length > maxLength) {
      if (chunk) chunks.push(chunk), chunk = '';
      let rest = sentence;
      while (rest.length > maxLength) {
        let cut = rest.lastIndexOf(' ', maxLength);
        if (cut < maxLength * 0.55) cut = maxLength;
        chunks.push(rest.slice(0, cut).trim());
        rest = rest.slice(cut).trim();
      }
      chunk = rest;
    } else if (!chunk) chunk = sentence;
    else if (chunk.length + sentence.length + 1 <= maxLength) chunk += ` ${sentence}`;
    else { chunks.push(chunk); chunk = sentence; }
  }
  if (chunk) chunks.push(chunk);
  return chunks;
}

export function estimateListenMinutes(text) {
  const words = String(text || '').trim().split(/\s+/).filter(Boolean).length;
  return `about ${Math.max(1, Math.round(words / 155))}`;
}

export function createPlayer(root, onProgress, getProgress, onChange = () => {}, onTranscript = () => ({ available: false }), transcriptLabel = () => 'Read along', onReplay = () => {}) {
  const synth = window.speechSynthesis;
  const available = Boolean(synth && typeof SpeechSynthesisUtterance === 'function');
  let current = null;
  let offered = null;
  let verified = true;
  let phase = 'idle';
  let dialog = null;
  let speed = readPlaybackSpeed();
  let selectedVoiceURI = localStorage.getItem(VOICE_KEY) || '';
  let narratorSample = /^[A-F]$/.test(localStorage.getItem(NARRATOR_KEY) || '') ? localStorage.getItem(NARRATOR_KEY) : 'A';
  let chapterIndex = 0;
  let chunkIndex = 0;
  let sentenceIndex = 0;
  let introPending = false;
  let transitionPending = true;
  let interruptedRetries = 0;
  let chunks = [];
  let elapsed = 0;
  let startedAt = 0;
  let opener = null;
  let restoreFocusOnClose = true;
  let generation = 0;
  let error = '';
  let hiddenPause = false;
  let watchdog = null;
  let recording = null;
  let recordingMode = false;
  let chapterSelectionPaused = false;
  let pausedChapterTime = null;
  const positionListeners = new Set();
  function chapters() { return chaptersFor(current); }
  function voices() { return synth?.getVoices?.() || []; }
  function activeVoice() {
    return voices().find(voice => voice.voiceURI === selectedVoiceURI) || preferredEnglishVoice(voices());
  }
  function currentChapter() { return chapters()[chapterIndex] || null; }
  function chapterDuration(chapter) {
    const words = chapterParagraphs(chapter).join(' ').split(/\s+/).filter(Boolean).length;
    return Math.max(1, Math.round(words / (155 * speed)));
  }
  function duration() {
    if (recordingMode && Number.isFinite(recording?.duration) && recording.duration > 0) return recording.duration;
    if (recordingMode && Number.isFinite(current?.audio?.duration_seconds)) return current.audio.duration_seconds;
    const words = chapters().reduce((sum, chapter) => sum + chapterParagraphs(chapter).join(' ').split(/\s+/).filter(Boolean).length, 0);
    return Math.max(1, Math.round(words / (155 * speed) * 60));
  }
  function position() {
    if (recordingMode && recording) {
      if (chapterSelectionPaused && phase === 'paused' && Number.isFinite(pausedChapterTime)) return pausedChapterTime;
      return Number(recording.currentTime) || 0;
    }
    return elapsed + (phase === 'playing' ? (Date.now() - startedAt) / 1000 : 0);
  }
  function publishPosition(mode = 'listen') {
    const chapter = currentChapter();
    const place = { itemId: current?.id, revisionId: current?.revision_id, storyId: chapter?.id || current?.id, paragraphIndex: chunkIndex, mode, updatedAt: new Date().toISOString() };
    positionListeners.forEach(listener => listener(place));
  }
  function savePosition() {
    if (!current) return;
    const chapter = currentChapter();
    onProgress(current.id, Math.max(0, position()), current.revision_id, chapter?.id || null);
    publishPosition('listen');
  }
  function actionLabel() {
    if (phase === 'playing') return 'Pause';
    if (phase === 'paused') return `Resume · ${formatTime(position())}`;
    if (phase === 'complete') return 'Replay';
    if (phase === 'error') return "Listening isn't available in this browser";
    return 'Play';
  }
  function estimatedChapterIndex(seconds) {
    const all = chapters();
    let cursor = 0;
    for (let i = 0; i < all.length; i += 1) {
      const partDuration = Math.max(1, Math.round(chapterText(all[i]).split(/\s+/).filter(Boolean).length / (155 * speed) * 60));
      if (seconds < cursor + partDuration) return { index: i, within: seconds - cursor };
      cursor += partDuration;
    }
    return { index: Math.max(0, all.length - 1), within: 0 };
  }
  function chunkForTime(index, seconds) {
    const chapter = chapters()[index];
    if (!chapter) return 0;
    const pieces = splitSpeechText(chapterText(chapter));
    const targetWords = Math.max(0, seconds) * 155 * speed / 60;
    let passed = 0;
    for (let i = 0; i < pieces.length; i += 1) {
      const words = pieces[i].split(/\s+/).filter(Boolean).length;
      if (targetWords < passed + words) return i;
      passed += words;
    }
    return Math.max(0, pieces.length - 1);
  }
  function chapterStartSeconds(index) {
    return chapters().slice(0, index).reduce((total, chapter) =>
      total + Math.max(1, Math.round(chapterText(chapter).split(/\s+/).filter(Boolean).length / (155 * speed) * 60)), 0);
  }
  function render() {
    let announcement = root.querySelector('.player-announcement');
    if (!announcement) { announcement = document.createElement('span'); announcement.className = 'player-announcement sr-only'; announcement.setAttribute('role', 'status'); root.append(announcement); }
    const announced = current ? phase === 'playing' ? 'Playing' : phase === 'paused' ? 'Paused' : phase === 'complete' ? 'Finished' : '' : '';
    if (announcement.textContent !== announced) announcement.textContent = announced;
    let bar = root.querySelector('.mini-player');
    const item = current || offered;
    if (!current || phase !== 'playing') { bar?.remove(); if (dialog?.open) updateDialog(); onChange(); return; }
    const blocked = materialCorrection(item, recordingMode) || (!recordingMode && !available) || !verified;
    if (!bar) {
      bar = document.createElement('div'); bar.className = 'mini-player';
      const open = document.createElement('button'); open.type = 'button'; open.className = 'mini-player-open';
      open.append(document.createElement('strong'), document.createElement('small'));
      open.addEventListener('click', () => openDialog(open));
      const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'mini-player-toggle';
      toggle.classList.add('today-mode-listen');
      toggle.innerHTML = `<span class="mode-icon">${playGlyph}</span><span class="mode-label"></span><small></small>`;
      toggle.addEventListener('click', () => current ? toggleVoice() : play(offered));
      toggle.setAttribute('aria-keyshortcuts', 'Space');
      toggle.setAttribute('aria-describedby', 'mini-player-shortcut-help');
      toggle.title = 'Space: play or pause';
      const shortcuts = document.createElement('span'); shortcuts.className = 'mini-player-shortcuts';
      shortcuts.id = 'mini-player-shortcut-help'; shortcuts.textContent = 'Space plays or pauses · ←/→ changes story';
      shortcuts.setAttribute('aria-hidden', 'true');
      const progress = document.createElement('span'); progress.className = 'mini-player-progress';
      const back = document.createElement('button'); back.type = 'button'; back.className = 'mini-player-back'; back.textContent = '↶'; back.setAttribute('aria-label', 'Back 15 seconds'); back.addEventListener('click', () => seekTo(Math.max(0, position() - 15)));
      bar.setAttribute('aria-label', 'Now playing'); bar.setAttribute('role', 'region');
      bar.append(open, back, shortcuts, toggle, progress); root.prepend(bar);
    }
    const activeChapter = currentChapter();
    const currentTime = position();
    const total = current ? duration() : 0;
    bar.querySelector('strong').textContent = current ? current.title : 'Listen to this edition';
    const remainingMinutes = Math.max(1, Math.ceil(Math.max(0, total - currentTime) / 60));
    const storyNumber = Math.min(chapterIndex + 1, Math.max(1, chapters().length));
    bar.querySelector('.mini-player-open small').textContent = current ? recordingMode
      ? `Chapter ${storyNumber} of ${chapters().length} · ${remainingMinutes} min left`
      : `Story ${storyNumber} of ${chapters().length} · ${remainingMinutes} min left` : 'Voice';
    bar.querySelector('.mini-player-open').setAttribute('aria-label', current ? recordingMode
      ? `${activeChapter?.title || current.title}, Chapter ${storyNumber} of ${chapters().length}, ${formatTime(currentTime)} of ${formatTime(total)}, opens player`
      : `${activeChapter?.title || current.title}, Story ${storyNumber} of ${chapters().length}, ${formatTime(currentTime)}, about ${remainingMinutes} minutes left, opens player` : 'Edition chapters');
    bar.querySelector('.mini-player-open').disabled = !current;
    const toggle = bar.querySelector('.mini-player-toggle');
    const label = blocked ? materialCorrection(item, recordingMode) ? 'Audio paused' : !verified ? 'Checking audio' : 'Unavailable' : !current ? 'Listen' : recordingMode ? (phase === 'playing' ? 'Pause' : phase === 'complete' ? 'Replay' : phase === 'error' ? 'Retry' : phase === 'loading' ? 'Loading' : 'Play') : phase === 'error' ? 'Retry' : actionLabel().split(' · ')[0];
    toggle.querySelector('.mode-icon').innerHTML = phase === 'playing' ? pauseGlyph : playGlyph;
    toggle.querySelector('.mode-label').textContent = label;
    toggle.querySelector('small').textContent = blocked ? '' : current ? `${formatTime(currentTime)} elapsed` : `${estimateListenMinutes(chaptersFor(item).map(chapterText).join(' '))} min`;
    toggle.dataset.editionId = item.id;
    toggle.dataset.revisionId = item.revision_id;
    toggle.disabled = blocked;
    toggle.setAttribute('aria-label', `${label} ${item.title}${!current && !blocked ? ', computer voice' : ''}`);
    bar.querySelector('.mini-player-progress').style.width = `${total ? Math.min(100, currentTime / total * 100) : 0}%`;
    if (dialog?.open) updateDialog();
    onChange();
  }
  function updateDialog() {
    if (!dialog || !current) return;
    const activeChapter = currentChapter();
    const total = duration(); const currentTime = Math.min(duration(), position());
    const remainingMinutes = Math.ceil(Math.max(0, total - currentTime) / 60);
    dialog.querySelector('[data-player-title]').textContent = current.title;
    const phaseLabel = dialog.querySelector('[data-player-phase]');
    phaseLabel.textContent = recordingMode
      ? `${phase === 'complete' ? 'Finished' : phase === 'error' ? 'Failed' : phase === 'loading' ? 'Loading audio' : phase === 'playing' ? 'Playing' : 'Paused'}${error ? ` · ${error}` : ` · ${activeChapter?.title || current.title}`}`
      : error || activeChapter?.title || '';
    phaseLabel.hidden = !recordingMode && !error;
    dialog.querySelector('[data-player-now-title]').textContent = activeChapter?.title || current.title;
    dialog.querySelector('[data-player-now-length]').textContent = phase === 'complete'
      ? 'Finished' : `${Math.max(1, remainingMinutes)} min left`;
    const queue = dialog.querySelector('[data-player-queue]');
    if (queue.dataset.chapterIndex !== `${chapterIndex}:${speed}`) renderQueue();
    dialog.querySelector('[data-player-chapters]').hidden = chapters().length < 2;
    dialog.querySelector('[data-player-transcript]').textContent = transcriptLabel(current);
    const seek = dialog.querySelector('[data-player-seek]');
    seek.max = String(total); seek.value = String(currentTime);
    seek.setAttribute('aria-valuetext', `${formatTime(currentTime)} of ${formatTime(total)}`);
    dialog.querySelector('[data-player-elapsed]').textContent = formatTime(currentTime);
    dialog.querySelector('[data-player-remaining]').textContent = `−${formatTime(total - currentTime)}`;
    const toggle = dialog.querySelector('[data-player-toggle]');
    toggle.innerHTML = phase === 'playing' ? pauseGlyph : playGlyph;
    toggle.setAttribute('aria-label', recordingMode ? (phase === 'playing' ? 'Pause episode audio' : phase === 'complete' ? 'Replay episode audio' : phase === 'error' ? 'Retry episode audio' : phase === 'loading' ? 'Episode audio loading' : 'Play episode audio') : actionLabel());
    toggle.disabled = recordingMode && phase === 'loading';
    dialog.querySelector('.player-voice-label').textContent = `Voice: Sample ${narratorSample}`;
    dialog.querySelector('.player-queue').setAttribute('aria-label', recordingMode ? 'Episode chapters' : 'Edition queue');
    dialog.querySelectorAll('[data-player-speed]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.playerSpeed) === speed)));
    dialog.querySelectorAll('.player-chapter-list [data-chapter-index]').forEach((button, index) => {
      const active = index === chapterIndex;
      if (active) button.setAttribute('aria-current', 'true'); else button.removeAttribute('aria-current');
      button.querySelector('[data-chapter-check]').textContent = active ? '✓ Current' : '';
    });
  }
  function renderQueue() {
    if (!dialog || !current) return;
    const queue = dialog.querySelector('[data-player-queue]');
    queue.dataset.chapterIndex = `${chapterIndex}:${speed}`;
    const list = dialog.querySelector('[data-player-next-list]');
    list.replaceChildren();
    const remaining = chapters().slice(chapterIndex + 1);
    const empty = dialog.querySelector('[data-player-queue-empty]');
    empty.hidden = remaining.length > 0;
    remaining.forEach((chapter, offset) => {
      const index = chapterIndex + offset + 1;
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'player-queue-row';
      button.dataset.queueChapter = String(index);
      const title = document.createElement('strong'); title.textContent = chapter.title;
      const length = document.createElement('small'); length.textContent = recordingMode
        ? `${formatTime(chapter.section?.start_seconds || 0)}–${formatTime(chapter.section?.end_seconds || 0)}`
        : `about ${chapterDuration(chapter)} min`;
      button.append(title, length);
      button.disabled = recordingMode && correctedChapter(current, chapter.section);
      if (button.disabled) button.setAttribute('aria-label', `${chapter.title}, audio unavailable after correction`);
      button.addEventListener('click', () => jumpToChapter(index, true));
      row.append(button); list.append(row);
    });
  }
  function setVoice(uri) {
    selectedVoiceURI = uri;
    localStorage.setItem(VOICE_KEY, selectedVoiceURI);
    const wasPlaying = phase === 'playing';
    const wasActive = wasPlaying || phase === 'paused';
    if (wasActive) {
      if (wasPlaying) elapsed += (Date.now() - startedAt) / 1000;
      generation += 1; synth.cancel(); phase = 'paused';
    }
    render();
    if (wasPlaying) speakFromCurrent();
  }
  function closeDialog() { if (dialog?.open) dialog.close(); }
  function openDialog(trigger = document.activeElement) {
    if (!current || dialog?.open) return;
    opener = trigger instanceof HTMLElement ? trigger : null;
    restoreFocusOnClose = true;
    dialog = document.createElement('dialog'); dialog.className = 'player-dialog';
    dialog.setAttribute('aria-label', current.title || 'Audio player');
    dialog.innerHTML = `<div class="player-sheet"><div data-player-main>
      <div class="player-sheet-top"><button type="button" class="player-minimize" data-player-close aria-label="Minimize"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button><span>Now listening</span></div>
      <h2 data-player-title></h2><p class="player-voice-label">Voice: Sample ${narratorSample}</p><p class="player-phase" data-player-phase role="status"></p>
      <section class="player-queue" data-player-queue aria-label="Edition queue">
        <div class="player-queue-current"><div><h3>Now playing</h3><strong data-player-now-title></strong></div><small><span data-player-now-length></span></small></div>
        <h3 class="player-queue-next-heading">Playing next</h3><ol class="player-queue-list" data-player-next-list></ol>
        <p class="player-queue-empty" data-player-queue-empty hidden>You're at the end of the edition.</p>
      </section>
      <label class="player-seek-label" for="player-seek">Playback position</label><input id="player-seek" type="range" min="0" max="1" step="1" value="0" data-player-seek>
      <div class="player-times"><span data-player-elapsed>0:00</span><span data-player-remaining>−0:00</span></div>
      <div class="player-controls"><button type="button" data-player-skip="-15" aria-label="Back 15 seconds">−15</button><button type="button" class="player-toggle" data-player-toggle aria-label="Play">${playGlyph}</button><button type="button" data-player-skip="30" aria-label="Forward 30 seconds">+30</button></div>
      <div class="player-secondary"><span>Speed</span><div class="player-speeds" role="group" aria-label="Playback speed">${[0.75, 1, 1.25, 1.5, 2].map(value => `<button type="button" data-player-speed="${value}" aria-pressed="${value === speed}">${value}×</button>`).join('')}</div></div>
      <div class="player-links"><button type="button" data-player-chapters hidden>Chapters</button><button type="button" data-player-transcript>Read along</button></div></div>
      <div data-player-chapters-view hidden><button type="button" data-player-back>Back to player</button><h2>Chapters</h2><ul class="player-chapter-list" data-player-chapter-list></ul></div>
      <p class="player-transcript-error" data-player-transcript-error role="status"></p></div>`;
    dialog.querySelector('[data-player-close]').addEventListener('click', closeDialog);
    dialog.querySelector('[data-player-toggle]').addEventListener('click', toggleVoice);
    dialog.querySelector('[data-player-seek]').addEventListener('change', event => seekTo(Number(event.target.value)));
    dialog.querySelectorAll('[data-player-skip]').forEach(button => button.addEventListener('click', () => skipBy(Number(button.dataset.playerSkip))));
    dialog.querySelectorAll('[data-player-speed]').forEach(button => button.addEventListener('click', () => {
      const wasPlaying = phase === 'playing';
      speed = Number(button.dataset.playerSpeed); savePlaybackSpeed(speed);
      if (recordingMode) { if (recording) recording.playbackRate = speed; render(); return; }
      if (phase === 'playing' || phase === 'paused') {
        if (phase === 'playing') elapsed += (Date.now() - startedAt) / 1000;
        generation += 1; synth.cancel(); phase = 'paused';
      }
      render(); if (wasPlaying) speakFromCurrent();
    }));
    dialog.querySelector('[data-player-chapters]').addEventListener('click', showChapters);
    dialog.querySelector('[data-player-back]').addEventListener('click', showPlayerMain);
    dialog.querySelector('[data-player-transcript]').addEventListener('click', () => {
      const result = onTranscript(current) || {};
      if (!result.available) { dialog.querySelector('[data-player-transcript-error]').textContent = 'This transcript is unavailable.'; return; }
      restoreFocusOnClose = false; closeDialog(); setTimeout(() => result.navigate?.(), 0);
    });
    dialog.addEventListener('close', () => {
      const target = opener; const restore = restoreFocusOnClose;
      dialog.remove(); dialog = null; opener = null;
      if (restore) requestAnimationFrame(() => (target?.isConnected ? target : document.querySelector('.today-listen-pill, .story-listen, .main-nav [aria-current]'))?.focus({ preventScroll: true }));
    });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); closeDialog(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...dialog.querySelectorAll('button:not([disabled]), input:not([disabled])')].filter(element => !element.hidden && element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    dialog.addEventListener('click', event => { if (event.target === dialog) closeDialog(); });
    root.append(dialog); dialog.showModal(); updateDialog(); dialog.querySelector('[data-player-close]').focus();
  }
  function skipBy(seconds) {
    if (recordingMode && recording) {
      chapterSelectionPaused = false; pausedChapterTime = null;
      recording.currentTime = Math.max(0, Math.min(duration(), position() + seconds));
      publishPosition('listen'); render(); return;
    }
    const wasPlaying = phase === 'playing';
    seekTo(position() + seconds);
    if (wasPlaying) speakFromCurrent();
  }
  function jumpToChapter(index, playAfter = false) {
    if (recordingMode && recording) {
      const chapter = chapters()[index];
      if (!chapter || correctedChapter(current, chapter.section)) return;
      chapterSelectionPaused = true;
      pausedChapterTime = Number(chapter.section.start_seconds) || 0;
      chapterIndex = index;
      recording.pause();
      recording.currentTime = pausedChapterTime;
      phase = 'paused'; updateMediaSession(); render();
      if (playAfter) toggleVoice();
      return;
    }
    seekChapter(index);
    // Chapters are a deliberate paused position; queue rows opt into immediate playback.
    if (playAfter) { hiddenPause = false; toggleVoice(); }
    if (dialog?.open) updateDialog();
  }
  function showPlayerMain() {
    if (!dialog?.open) return;
    dialog.querySelector('[data-player-chapters-view]').hidden = true;
    dialog.querySelector('[data-player-main]').hidden = false;
    dialog.querySelector('[data-player-chapters]').focus();
  }
  function showChapters() {
    const list = dialog.querySelector('[data-player-chapter-list]'); list.replaceChildren();
    chapters().forEach((chapter, index) => {
      const li = document.createElement('li'); const button = document.createElement('button');
      button.type = 'button'; button.className = 'player-chapter-row'; button.dataset.chapterIndex = String(index);
      const title = document.createElement('strong'); title.textContent = chapter.title;
      const time = document.createElement('small'); time.textContent = recordingMode
        ? `${formatTime(chapter.section?.start_seconds || 0)}–${formatTime(chapter.section?.end_seconds || 0)}`
        : `about ${chapterDuration(chapter)} min`;
      const check = document.createElement('span'); check.dataset.chapterCheck = ''; check.setAttribute('aria-hidden', 'true');
      button.append(title, time, check);
      button.disabled = recordingMode && correctedChapter(current, chapter.section);
      if (button.disabled) button.setAttribute('aria-label', `${chapter.title}, audio unavailable after correction`);
      button.addEventListener('click', () => { jumpToChapter(index); showPlayerMain(); });
      li.append(button); list.append(li);
    });
    dialog.querySelector('[data-player-main]').hidden = true; dialog.querySelector('[data-player-chapters-view]').hidden = false;
    updateDialog(); list.querySelector(`[data-chapter-index="${chapterIndex}"]`)?.focus();
  }
  function speakFromCurrent() {
    if (!current || materialCorrection(current) || !synth || typeof SpeechSynthesisUtterance !== 'function') { phase = 'error'; error = 'Speech synthesis is unavailable in this browser.'; render(); return; }
    const entries = chapters();
    if (introPending) {
      introPending = false;
      const intro = audioScriptText(currentChapter()?.section) ? null
        : narrationUnits(current).find(unit => unit.intro);
      if (intro) return speakText(intro.text, true);
    }
    const chapter = entries[chapterIndex];
    if (!chapter) { phase = 'complete'; updateMediaSession(); render(); return; }
    if (transitionPending) {
      transitionPending = false;
      if (audioScriptText(chapter.section)) return speakFromCurrent();
      const heading = cleanSpeechText(chapter.title).replace(/[.!?]+$/, '');
      return speakText(chapterIndex ? `Next story: ${heading}.` : `${heading}.`, true);
    }
    chunks = chapterParagraphs(chapter).flatMap((paragraph, paragraphIndex) => splitSpeechText(cleanSpeechText(paragraph)).map(text => ({ text, paragraphIndex })));
    while (chunkIndex < chapterParagraphs(chapter).length && !chunks.some(unit => unit.paragraphIndex === chunkIndex)) chunkIndex += 1;
    if (chunkIndex >= chapterParagraphs(chapter).length) { chapterIndex += 1; chunkIndex = 0; sentenceIndex = 0; transitionPending = true; return speakFromCurrent(); }
    const sentenceUnits = chunks.filter(unit => unit.paragraphIndex === chunkIndex);
    if (sentenceIndex >= sentenceUnits.length) { chunkIndex += 1; sentenceIndex = 0; return speakFromCurrent(); }
    return speakText(sentenceUnits[sentenceIndex].text);
  }
  function speakText(text, isTransition = false) {
    const token = ++generation;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = speed;
    const voice = activeVoice(); if (voice) utterance.voice = voice;
    utterance.onstart = () => { if (token !== generation) return; phase = 'playing'; startedAt = Date.now(); error = ''; publishPosition('listen'); updateMediaSession(); render(); };
    utterance.onend = () => {
      if (token !== generation || phase !== 'playing') return;
      interruptedRetries = 0;
      elapsed += (Date.now() - startedAt) / 1000;
      if (!isTransition && !introPending) {
        const body = chapterParagraphs(currentChapter());
        const sentences = splitSpeechText(cleanSpeechText(body[chunkIndex] || ''));
        sentenceIndex += 1;
        if (sentenceIndex >= sentences.length) { sentenceIndex = 0; chunkIndex += 1; savePosition(); }
        if (chunkIndex >= body.length) { chapterIndex += 1; chunkIndex = 0; sentenceIndex = 0; transitionPending = true; savePosition(); }
      }
      if (chapterIndex >= chapters().length) { phase = 'complete'; savePosition(); updateMediaSession(); render(); }
      else speakFromCurrent();
    };
    utterance.onerror = event => {
      if (token !== generation || event.error === 'canceled') return;
      if (event.error === 'interrupted' || event.error === 'audio-busy' || event.error === 'network') {
        phase = 'paused'; hiddenPause = document.hidden;
        if (!hiddenPause && interruptedRetries++ < 3) setTimeout(() => { if (phase === 'paused' && current) speakFromCurrent(); }, 350 * interruptedRetries);
        else { error = 'Tap Resume to continue listening.'; render(); }
        return;
      }
      phase = 'error'; error = 'Listening stopped. Try Listen again.'; updateMediaSession(); render();
    };
    synth.speak(utterance);
  }
  function pauseVoice() {
    if (phase !== 'playing') return;
    elapsed += (Date.now() - startedAt) / 1000; synth.pause(); phase = 'paused'; savePosition(); updateMediaSession(); render();
  }
  function toggleVoice() {
    if (!verified || !current || materialCorrection(current, recordingMode)) return;
    if (recordingMode) {
      if (!recording) { phase = 'error'; error = 'Episode audio could not be loaded. Try again.'; updateMediaSession(); render(); return; }
      if (phase === 'playing') { recording.pause(); phase = 'paused'; updateMediaSession(); render(); return; }
      if (chapterSelectionPaused && Number.isFinite(pausedChapterTime)) recording.currentTime = pausedChapterTime;
      chapterSelectionPaused = false; pausedChapterTime = null;
      if (phase === 'complete') { recording.currentTime = 0; chapterIndex = 0; }
      if (phase === 'error') { recording.src = current.audio.url; recording.currentTime = Number(chapters()[chapterIndex]?.section?.start_seconds) || 0; recording.load(); }
      error = ''; phase = 'loading'; updateMediaSession(); render();
      const promise = recording.play();
      if (promise?.catch) promise.catch(() => { phase = 'error'; error = 'Episode audio could not be played. Try again.'; updateMediaSession(); render(); });
      return;
    }
    if (phase === 'playing') { pauseVoice(); return; }
    if (phase === 'paused' && synth?.paused && typeof synth.resume === 'function' && !hiddenPause) { startedAt = Date.now(); phase = 'playing'; synth.resume(); updateMediaSession(); render(); return; }
    if (phase === 'complete') { onReplay(current, currentChapter()?.id); chapterIndex = 0; chunkIndex = 0; sentenceIndex = 0; elapsed = 0; introPending = true; transitionPending = true; }
    hiddenPause = false;
    speakFromCurrent();
  }
  function updateMediaPosition() {
    const media = navigator.mediaSession;
    if (typeof media?.setPositionState !== 'function') return;
    try {
      if (!current) { media.setPositionState(); return; }
      const total = duration();
      if (!Number.isFinite(total) || total <= 0) return;
      media.setPositionState({ duration: total, playbackRate: recordingMode ? Number(recording?.playbackRate) || speed : speed,
        position: Math.min(total, Math.max(0, position())) });
    } catch { /* Position state is optional and can be rejected by browser implementations. */ }
  }
  function seekFromMediaSession(seconds) {
    if (!current || !Number.isFinite(seconds)) return;
    const wasPlaying = phase === 'playing';
    seekTo(seconds);
    if (wasPlaying && !recordingMode) { hiddenPause = false; speakFromCurrent(); }
    updateMediaPosition();
  }
  function updateMediaSession() {
    const media = navigator.mediaSession;
    if (!media) return;
    try {
      media.metadata = current && typeof MediaMetadata === 'function'
        ? new MediaMetadata({ title: current.title || 'Briefing', artist: 'Briefing', album: current.kind === 'weekly' ? 'Weekly recap' : 'Daily briefing', artwork: MEDIA_ARTWORK.map(image => ({ ...image, src: new URL(image.src, location.origin).href })) }) : null;
      media.playbackState = phase === 'playing' ? 'playing' : phase === 'paused' ? 'paused' : 'none';
      const actions = {
        play: () => { if (phase !== 'playing') toggleVoice(); },
        pause: () => { if (phase === 'playing') pauseVoice(); },
        nexttrack: () => { const resume = phase === 'playing'; if (current && chapterIndex < chapters().length - 1) { seekChapter(chapterIndex + 1); if (resume) toggleVoice(); } },
        previoustrack: () => { const resume = phase === 'playing'; if (current) { seekChapter(Math.max(0, chapterIndex - 1)); if (resume) toggleVoice(); } },
        seekbackward: details => seekFromMediaSession(position() - Number(details?.seekOffset || 15)),
        seekforward: details => seekFromMediaSession(position() + Number(details?.seekOffset || 15)),
        seekto: details => seekFromMediaSession(Number(details?.seekTime)),
      };
      Object.entries(actions).forEach(([action, handler]) => { try { media.setActionHandler(action, handler); } catch { /* Some browsers omit individual actions. */ } });
      if (!current) ['play', 'pause', 'nexttrack', 'previoustrack', 'seekbackward', 'seekforward', 'seekto'].forEach(action => {
        try { media.setActionHandler(action, null); } catch { /* Some browsers omit individual actions. */ }
      });
      updateMediaPosition();
    } catch { /* Media Session actions vary by browser. */ }
  }
  function configureVoiceSettings(_select, speedSelect, _sampleButton) {
    if (!speedSelect) return () => {};
    speedSelect.value = String(speed);
    speedSelect.addEventListener('change', () => {
      const value = Number(speedSelect.value);
      if (![0.75, 1, 1.25, 1.5, 2].includes(value)) return;
      const wasPlaying = phase === 'playing';
      if (wasPlaying) elapsed += (Date.now() - startedAt) / 1000;
      speed = value; savePlaybackSpeed(speed);
      if (wasPlaying || phase === 'paused') { generation += 1; synth?.cancel(); phase = 'paused'; }
      render(); if (wasPlaying) speakFromCurrent();
    });
    return () => {};
  }
  function setNarratorSample(sample) {
    if (!/^[A-F]$/.test(sample || '')) return false;
    narratorSample = sample; localStorage.setItem(NARRATOR_KEY, sample); render(); return true;
  }
  function seekTo(seconds) {
    if (recordingMode && recording) {
      chapterSelectionPaused = false; pausedChapterTime = null;
      const wasPlaying = phase === 'playing';
      recording.currentTime = Math.max(0, Math.min(duration(), seconds));
      if (!wasPlaying) recording.pause();
      phase = wasPlaying ? 'playing' : 'paused'; publishPosition('listen'); updateMediaSession(); render(); return;
    }
    const point = estimatedChapterIndex(Math.max(0, Math.min(duration(), seconds)));
    generation += 1; synth?.cancel();
    elapsed = Math.max(0, Math.min(duration(), seconds)); chapterIndex = point.index; chunkIndex = chunkForTime(point.index, point.within); phase = 'paused';
    savePosition(); render();
  }
  function seekChapter(index) {
    if (recordingMode && recording) {
      const chapter = chapters()[index];
      if (!chapter || correctedChapter(current, chapter.section)) return;
      chapterSelectionPaused = true;
      pausedChapterTime = Number(chapter.section.start_seconds) || 0;
      recording.pause(); chapterIndex = index; recording.currentTime = pausedChapterTime;
      phase = 'paused'; updateMediaSession(); render(); return;
    }
    generation += 1; synth?.cancel(); chapterIndex = index; chunkIndex = 0;
    sentenceIndex = 0; transitionPending = true; elapsed = chapterStartSeconds(index);
    phase = 'paused'; savePosition(); render();
  }
  function select(item, autoplay, startChapterId = null) {
    if (!verified || !item || materialCorrection(item)) return;
    generation += 1; recording?.pause(); recording = null; recordingMode = false; synth?.cancel(); savePosition(); current = item; phase = 'paused'; error = ''; elapsed = 0; chapterIndex = 0; chunkIndex = 0; sentenceIndex = 0; introPending = true; transitionPending = true;
    const stored = getProgress(item.id);
    const chapterId = startChapterId || stored?.storyId || stored?.chapterId;
    if (chapterId) {
      chapterIndex = Math.max(0, chapters().findIndex(chapter => chapter.id === chapterId));
      if (stored?.revisionId === item.revision_id && Number.isInteger(stored.paragraphIndex)) chunkIndex = Math.max(0, stored.paragraphIndex);
      if (stored?.revisionId === item.revision_id && Number.isFinite(stored.seconds)) {
        elapsed = stored.seconds;
        if (!Number.isInteger(stored.paragraphIndex)) chunkIndex = chunkForTime(chapterIndex, Math.max(0, stored.seconds - chapterStartSeconds(chapterIndex)));
      }
    } else if (stored?.revisionId === item.revision_id && Number.isInteger(stored.paragraphIndex)) {
      chunkIndex = Math.max(0, stored.paragraphIndex);
    } else if (stored?.revisionId === item.revision_id && Number.isFinite(stored.seconds)) {
      const point = estimatedChapterIndex(stored.seconds); chapterIndex = point.index; elapsed = stored.seconds; chunkIndex = chunkForTime(point.index, point.within);
    }
    if (chunkIndex > 0 || chapterIndex > 0) introPending = false;
    render(); updateMediaSession(); if (autoplay) toggleVoice();
  }
  function play(item) {
    if (!verified || !item || materialCorrection(item)) return;
    const startChapterId = item.start_section_id || null;
    if (current?.id === item.id && current.revision_id === item.revision_id) {
      if (phase === 'complete') onReplay(current, startChapterId || currentChapter()?.id);
      if (startChapterId) seekChapter(Math.max(0, chapters().findIndex(chapter => chapter.id === startChapterId)));
      toggleVoice(); return;
    }
    select(item, true, startChapterId);
  }
  function playRecording(item, { chapterId = null } = {}) {
    if (!verified || !item || materialCorrection(item, true) || !Array.isArray(item.chapters)) return;
    const session = recordingSession(item, current, chapterId);
    if (!session.playable) return;
    if (correctedChapter(item, { id: item.chapters[session.chapterIndex]?.id })) return;
    if (session.reusable && recordingMode && recording) {
      chapterIndex = session.chapterIndex;
      if (chapterId) recording.currentTime = session.startSeconds;
      toggleVoice(); return;
    }
    generation += 1; synth?.cancel(); recording?.pause();
    current = session.item;
    recordingMode = true; chapterSelectionPaused = false; pausedChapterTime = null; chapterIndex = session.chapterIndex; elapsed = 0; chunkIndex = 0; sentenceIndex = 0; error = ''; phase = 'loading';
    const audio = new Audio(item.audio.url);
    recording = audio;
    audio.preload = 'metadata'; audio.playbackRate = speed;
    audio.addEventListener('loadedmetadata', () => { if (isCurrentRecordingEvent(recording, audio, recordingMode)) { audio.currentTime = session.startSeconds; render(); } });
    audio.addEventListener('canplay', () => { if (isCurrentRecordingEvent(recording, audio, recordingMode) && phase === 'loading') { phase = 'playing'; error = ''; updateMediaSession(); render(); } });
    audio.addEventListener('playing', () => {
      if (!isCurrentRecordingEvent(recording, audio, recordingMode)) return;
      if (chapterSelectionPaused || phase === 'paused') { audio.pause(); return; }
      phase = 'playing'; error = ''; updateMediaSession(); render();
    });
    audio.addEventListener('timeupdate', () => {
      if (!isCurrentRecordingEvent(recording, audio, recordingMode)) return;
      const seconds = audio.currentTime;
      if (!chapterSelectionPaused && phase === 'playing') {
        const active = item.chapters.findIndex((chapter, index) => seconds >= (Number(chapter.start_seconds) || 0) && (seconds < Number(chapter.end_seconds) || index === item.chapters.length - 1));
        if (active >= 0) chapterIndex = active;
      }
      publishPosition('listen'); render();
    });
    audio.addEventListener('seeked', () => {
      if (!isCurrentRecordingEvent(recording, audio, recordingMode)) return;
      const seconds = audio.currentTime;
      if (!chapterSelectionPaused) {
        const active = item.chapters.findIndex((chapter, index) => seconds >= (Number(chapter.start_seconds) || 0) &&
          (seconds < Number(chapter.end_seconds) || index === item.chapters.length - 1));
        if (active >= 0) chapterIndex = active;
      }
      publishPosition('listen'); render();
    });
    audio.addEventListener('ended', () => { if (!isCurrentRecordingEvent(recording, audio, recordingMode)) return; phase = 'complete'; error = ''; updateMediaSession(); render(); });
    audio.addEventListener('error', () => { if (!isCurrentRecordingEvent(recording, audio, recordingMode)) return; phase = 'error'; error = 'Episode audio failed to load. Try again.'; updateMediaSession(); render(); });
    render(); updateMediaSession();
    const promise = audio.play();
    if (promise?.catch) promise.catch(() => { if (!isCurrentRecordingEvent(recording, audio, recordingMode) || phase === 'paused' || chapterSelectionPaused) return; phase = 'error'; error = 'Episode audio could not be played. Try again.'; updateMediaSession(); render(); });
    openDialog(document.activeElement);
  }
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.repeat) return;
    if (document.querySelector('dialog[open]')) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('button,a,input,textarea,select,[contenteditable="true"],[role="textbox"]')) return;
    if (event.code === 'Space' || event.key === ' ') {
      if (!current && !offered) return;
      event.preventDefault();
      if (current) toggleVoice(); else play(offered);
      return;
    }
    if (!current || !chapters().length || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const resume = phase === 'playing';
    const next = event.key === 'ArrowRight' ? Math.min(chapterIndex + 1, chapters().length - 1) : Math.max(0, chapterIndex - 1);
    if (next === chapterIndex) return;
    seekChapter(next);
    if (resume) toggleVoice();
  });
  function setVerified(value) { verified = value; render(); }
  function offer(item) { offered = item; render(); }
  function restore(item) { if (item && !materialCorrection(item)) select(item, false); }
  function invalidate(itemId) {
    const changedOffer = offered?.id === itemId;
    if (changedOffer) offered = { ...offered, audio: { ...offered.audio, superseded: true } };
    if (!current || current.id !== itemId) { if (changedOffer) render(); return false; }
    generation += 1; synth?.cancel(); recording?.pause(); recording = null; recordingMode = false; current = null; phase = 'idle'; chapterIndex = 0; chunkIndex = 0; sentenceIndex = 0; elapsed = 0; updateMediaSession();
    if (dialog?.open) closeDialog(); render(); return true;
  }
  function close() { offered = null; generation += 1; savePosition(); synth?.cancel(); recording?.pause(); recording = null; recordingMode = false; current = null; phase = 'idle'; updateMediaSession(); if (dialog?.open) closeDialog(); render(); }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      // Browser audio elements can keep playing under Media Session in a background tab.
      // Speech synthesis is often suspended by the browser, so pause it and resume on return.
      if (phase === 'playing' && !recordingMode) { elapsed += (Date.now() - startedAt) / 1000; synth?.pause?.(); phase = 'paused'; hiddenPause = true; updateMediaSession(); render(); }
      savePosition();
    }
  });
  window.addEventListener('pagehide', savePosition);
  setInterval(() => { if (phase === 'playing') { render(); updateMediaPosition(); } }, 1000);
  setInterval(() => {
    if (phase === 'playing' && synth && !synth.speaking && !synth.pending && interruptedRetries < 3) {
      interruptedRetries += 1; phase = 'paused'; setTimeout(() => { if (phase === 'paused' && current && !document.hidden) speakFromCurrent(); }, 250 * interruptedRetries);
    } else if (phase === 'playing' && synth && !synth.speaking && !synth.pending) {
      phase = 'error'; error = 'Listening stopped. Tap Listen to try again.'; updateMediaSession(); render();
    }
  }, 5000);
  synth?.addEventListener?.('voiceschanged', () => { if (current && phase === 'playing') updateMediaSession(); });
  function listen(item, { fromParagraph = 0 } = {}) {
    if (!verified || !item || materialCorrection(item)) return;
    select(item, false, item.start_section_id || null);
    chunkIndex = Math.max(0, Math.floor(Number(fromParagraph) || 0)); sentenceIndex = 0; introPending = chapterIndex === 0 && chunkIndex === 0; transitionPending = true;
    toggleVoice();
  }
  function onPositionChange(listener) {
    if (typeof listener !== 'function') return () => {};
    positionListeners.add(listener);
    return () => positionListeners.delete(listener);
  }
  return { play, listen, playRecording, offer, setVerified, estimateListenMinutes, onPositionChange, configureVoiceSettings, setNarratorSample, voiceOptions: voices, restore, close, invalidate, refresh: render, get available() { return available || recordingMode; }, get status() { return recordingMode && phase === 'complete' ? 'finished' : recordingMode && phase === 'error' ? 'failed' : phase; }, get item() { return current; }, get label() { return actionLabel(); }, get speed() { return speed; }, get voice() { return activeVoice(); }, get narratorSample() { return narratorSample; } };
}
