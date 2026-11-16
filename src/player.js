const formatTime = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  return `${minutes}:${String(value % 60).padStart(2, '0')}`;
};

function chaptersFor(item, duration = 0) {
  const chapters = item?.audio?.chapters;
  if (item?.audio?.state !== 'recorded' || !Number.isFinite(duration) || duration <= 0 ||
      !Array.isArray(chapters) || chapters.length < 2) return [];
  const ids = new Set();
  let previous = -1;
  for (const chapter of chapters) {
    const start = chapter?.start_seconds;
    if (typeof chapter?.id !== 'string' || !chapter.id || ids.has(chapter.id) ||
        typeof chapter.title !== 'string' || !chapter.title.trim() ||
        !Number.isFinite(start) || start < 0 || start <= previous ||
        (duration > 0 && start >= duration)) return [];
    ids.add(chapter.id);
    previous = start;
  }
  return chapters[0].start_seconds === 0 ? chapters : [];
}

export function createPlayer(root, onProgress, getProgress, onChange = () => {}, onTranscript = () => ({ available: false }), transcriptLabel = () => 'Read transcript') {
  const audio = new Audio();
  audio.preload = 'metadata';
  let current = null;
  let phase = 'idle';
  let dialog = null;
  let speed = Number(localStorage.getItem('briefing:playback-speed')) || 1;
  let lastSavedSecond = -1;
  let generation = 0;
  let confirmedChapterSeconds = 0;
  let chapterSeekInFlight = false;
  let chapterError = '';
  if (![0.75, 1, 1.25, 1.5, 2].includes(speed)) speed = 1;

  function savePosition() {
    if (!current || !Number.isFinite(audio.currentTime)) return;
    onProgress(current.id, audio.currentTime, current.revision_id);
    lastSavedSecond = Math.floor(audio.currentTime);
  }

  function actionLabel() {
    if (phase === 'playing') return 'Pause';
    if (phase === 'paused') return `Resume · ${formatTime(audio.currentTime)}`;
    if (phase === 'complete') return 'Replay';
    if (phase === 'loading') return 'Loading audio…';
    if (phase === 'error') return 'Retry audio';
    if (phase === 'pending') return 'Audio being prepared';
    return 'Play';
  }

  function audioUrl(item) {
    if (item.audio?.state !== 'recorded' || !item.audio.url || item.audio.revision_id !== item.revision_id || item.audio.script_hash !== item.script_hash) return null;
    try {
      const url = new URL(item.audio.url, location.href);
      return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  function currentChapter() {
    if (phase === 'pending' || phase === 'error') return { chapters: [], index: -1 };
    const chapters = chaptersFor(current, audio.duration);
    if (!chapters.length) return { chapters, index: -1 };
    let index = 0;
    for (let i = 1; i < chapters.length; i += 1) {
      if (confirmedChapterSeconds < chapters[i].start_seconds) break;
      index = i;
    }
    return { chapters, index };
  }

  function updateChapterRows() {
    if (!dialog) return;
    const { index } = currentChapter();
    dialog.querySelectorAll('[data-chapter-index]').forEach((button, row) => {
      const active = row === index;
      const chapter = current.audio.chapters[row];
      button.setAttribute('aria-label', `${chapter.title}, starts at ${formatTime(chapter.start_seconds)}${active ? ', current chapter' : ''}`);
      if (active) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
      button.querySelector('[data-chapter-check]').textContent = active ? '✓ Current' : '';
    });
  }

  function render() {
    let bar = root.querySelector('.mini-player');
    if (!current) { bar?.remove(); onChange(); return; }
    if (!bar) {
      bar = document.createElement('div'); bar.className = 'mini-player';
      const open = document.createElement('button');
      open.type = 'button'; open.className = 'mini-player-open';
      open.append(document.createElement('strong'), document.createElement('small'));
      open.addEventListener('click', openDialog);
      const toggleButton = document.createElement('button');
      toggleButton.type = 'button'; toggleButton.className = 'mini-player-toggle';
      toggleButton.addEventListener('click', toggle);
      const progress = document.createElement('span'); progress.className = 'mini-player-progress';
      bar.append(open, toggleButton, progress); root.prepend(bar);
    }
    const title = bar.querySelector('strong'); title.textContent = current.title;
    const time = bar.querySelector('small');
    time.textContent = phase === 'pending' ? 'Audio being prepared' : `${formatTime(audio.currentTime)} of ${Number.isFinite(audio.duration) ? formatTime(audio.duration) : '–:––'}`;
    const toggleButton = bar.querySelector('.mini-player-toggle');
    toggleButton.textContent = phase === 'playing' ? 'Ⅱ' : '▶';
    toggleButton.setAttribute('aria-label', `${actionLabel()} ${current.title}`);
    const progress = bar.querySelector('.mini-player-progress');
    progress.style.width = Number.isFinite(audio.duration) && audio.duration > 0 ? `${Math.min(100, 100 * audio.currentTime / audio.duration)}%` : '0%';
    if (dialog?.open) updateDialog();
    onChange();
  }

  function updateDialog() {
    if (!dialog) return;
    dialog.querySelector('[data-player-title]').textContent = current.title;
    const { chapters, index } = currentChapter();
    const subtitle = dialog.querySelector('[data-player-phase]');
    const neutral = current.kind === 'weekly' ? 'Recorded recap' : current.kind === 'deep-dive' ? 'Recorded explanation' : 'Recorded briefing';
    subtitle.textContent = phase === 'pending' ? 'Updated audio is being prepared. You can read the full text now.' : phase === 'error' ? 'Audio couldn’t load. Retry when your connection is available.' : phase === 'loading' ? 'Loading audio…' : chapters.length ? chapters[index].title : phase === 'complete' ? 'Finished' : neutral;
    subtitle.setAttribute('aria-live', phase === 'error' || phase === 'loading' ? 'polite' : 'off');
    dialog.querySelector('[data-player-chapters]').hidden = chapters.length < 2;
    dialog.querySelector('[data-player-transcript]').textContent = transcriptLabel(current);
    dialog.querySelector('[data-player-chapter-error]').textContent = chapterError;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    const seek = dialog.querySelector('[data-player-seek]');
    seek.max = String(duration); seek.value = String(Math.min(duration, audio.currentTime || 0)); seek.disabled = !duration || phase === 'pending';
    dialog.querySelector('[data-player-elapsed]').textContent = formatTime(audio.currentTime);
    dialog.querySelector('[data-player-remaining]').textContent = duration ? `−${formatTime(duration - audio.currentTime)}` : '–:––';
    const toggleButton = dialog.querySelector('[data-player-toggle]');
    toggleButton.textContent = actionLabel(); toggleButton.disabled = phase === 'pending';
    dialog.querySelectorAll('[data-player-skip]').forEach(button => { button.disabled = !duration; });
    dialog.querySelector('[data-player-speed]').value = String(speed);
    updateChapterRows();
  }

  function showPlayerMain() {
    if (!dialog?.open) return;
    dialog.querySelector('[data-player-chapters-view]').hidden = true;
    dialog.querySelector('[data-player-main]').hidden = false;
    dialog.querySelector('[data-player-chapters]').focus();
  }

  function showChapters() {
    const { chapters, index } = currentChapter();
    if (!dialog?.open || chapters.length < 2) return;
    const list = dialog.querySelector('[data-player-chapter-list]');
    list.replaceChildren();
    chapters.forEach((chapter, row) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'player-chapter-row'; button.dataset.chapterIndex = String(row);
      const heading = document.createElement('strong'); heading.textContent = chapter.title;
      const start = document.createElement('small'); start.textContent = formatTime(chapter.start_seconds);
      const check = document.createElement('span'); check.dataset.chapterCheck = ''; check.setAttribute('aria-hidden', 'true');
      button.append(heading, start, check);
      button.addEventListener('click', () => selectChapter(row));
      item.append(button); list.append(item);
    });
    dialog.querySelector('[data-player-main]').hidden = true;
    dialog.querySelector('[data-player-chapters-view]').hidden = false;
    updateChapterRows();
    const selected = list.querySelector(`[data-chapter-index="${index}"]`);
    selected?.scrollIntoView({ block: 'nearest' });
    selected?.focus();
  }

  function finishChapterSeek() {
    if (!chapterSeekInFlight) return;
    chapterSeekInFlight = false;
    confirmedChapterSeconds = audio.currentTime;
    chapterError = '';
    savePosition(); render(); showPlayerMain();
  }

  function selectChapter(index) {
    const { chapters } = currentChapter();
    const target = chapters[index]?.start_seconds;
    if (!Number.isFinite(target) || !Number.isFinite(audio.duration)) return;
    chapterError = '';
    chapterSeekInFlight = true;
    try {
      audio.currentTime = target;
      if (!audio.seeking && Math.abs(audio.currentTime - target) < 0.05) finishChapterSeek();
    } catch {
      chapterSeekInFlight = false;
      chapterError = 'Could not move to that chapter. Try again.';
      updateDialog();
    }
  }

  function openDialog() {
    if (!current || dialog?.open) return;
    dialog = document.createElement('dialog'); dialog.className = 'player-dialog';
    dialog.innerHTML = `<div class="player-sheet">
      <div data-player-main>
        <div class="player-sheet-top"><button type="button" data-player-close>Minimize</button><span>Now listening</span></div>
        <p class="player-kind"></p><h2 data-player-title></h2><p class="player-phase" data-player-phase></p>
        <label class="player-seek-label" for="player-seek">Playback position</label>
        <input id="player-seek" type="range" min="0" max="0" step="1" value="0" data-player-seek>
        <div class="player-times"><span data-player-elapsed>0:00</span><span data-player-remaining>–:––</span></div>
        <div class="player-controls"><button type="button" data-player-skip="-15" aria-label="Back 15 seconds">↺ 15</button><button type="button" class="primary" data-player-toggle>Play</button><button type="button" data-player-skip="15" aria-label="Forward 15 seconds">15 ↻</button></div>
        <div class="player-secondary"><label>Speed <select data-player-speed><option value="0.75">0.75×</option><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label></div>
        <div class="player-links"><button type="button" data-player-chapters hidden>Chapters</button><button type="button" data-player-transcript>Read transcript</button></div>
      </div>
      <div data-player-chapters-view hidden>
        <button type="button" data-player-back>Back to player</button><h2>Chapters</h2>
        <ul class="player-chapter-list" data-player-chapter-list></ul>
      </div>
        <p class="player-chapter-error" data-player-chapter-error role="alert"></p>
        <p class="player-transcript-error" data-player-transcript-error role="status"></p>
    </div>`;
    dialog.querySelector('.player-kind').textContent = current.kind === 'weekly' ? 'Weekly recap' : current.kind === 'deep-dive' ? 'Topic explanation' : 'Today’s briefing';
    dialog.querySelector('[data-player-close]').addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-player-toggle]').addEventListener('click', toggle);
    dialog.querySelector('[data-player-seek]').addEventListener('change', event => { audio.currentTime = Number(event.target.value); savePosition(); render(); });
    dialog.querySelectorAll('[data-player-skip]').forEach(button => button.addEventListener('click', () => {
      audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + Number(button.dataset.playerSkip)));
      savePosition(); render();
    }));
    dialog.querySelector('[data-player-speed]').addEventListener('change', event => {
      speed = Number(event.target.value); audio.playbackRate = speed;
      localStorage.setItem('briefing:playback-speed', String(speed)); render();
    });
    dialog.querySelector('[data-player-chapters]').addEventListener('click', showChapters);
    dialog.querySelector('[data-player-back]').addEventListener('click', showPlayerMain);
    dialog.querySelector('[data-player-transcript]').addEventListener('click', () => {
      const selected = current;
      const result = onTranscript(selected) || {};
      if (!result.available) {
        dialog.querySelector('[data-player-transcript-error]').textContent = 'This transcript is unavailable.';
        return;
      }
      if (result.correctedItem) select(result.correctedItem, false, false);
      dialog?.close();
      setTimeout(() => result.navigate?.(), 0);
    });
    dialog.addEventListener('close', () => { dialog.remove(); dialog = null; });
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    root.append(dialog); dialog.showModal(); updateDialog();
  }

  async function toggle() {
    if (!current) return;
    if (phase === 'pending') { openDialog(); return; }
    if (phase === 'playing' || phase === 'loading') { generation += 1; audio.pause(); phase = 'paused'; savePosition(); render(); return; }
    if (phase === 'complete') audio.currentTime = 0;
    const request = ++generation;
    try { phase = 'loading'; render(); await audio.play(); if (request === generation) { phase = 'playing'; render(); } }
    catch { if (request === generation) { phase = 'error'; render(); } }
  }

  function select(item, autoplay, openPendingPlayer = true) {
    generation += 1;
    if (current && audioUrl(current)) savePosition();
    audio.pause(); audio.removeAttribute('src'); audio.load();
    if (dialog?.open) dialog.close();
    current = item;
    confirmedChapterSeconds = 0;
    chapterSeekInFlight = false;
    chapterError = '';
    const url = audioUrl(item);
    phase = url ? 'paused' : 'pending';
    if (url) {
      audio.src = url; audio.playbackRate = speed;
      const saved = getProgress(item.id);
      audio.addEventListener('loadedmetadata', function resumePosition() {
        audio.removeEventListener('loadedmetadata', resumePosition);
        if (current?.id !== item.id || current?.revision_id !== item.revision_id) return;
        if (saved?.revisionId === item.revision_id && Number.isFinite(saved.seconds)) audio.currentTime = Math.min(saved.seconds, Math.max(0, audio.duration - 1));
        render();
        if (autoplay) toggle();
      });
      audio.load(); render();
    } else { render(); if (openPendingPlayer) openDialog(); }
  }

  function play(item) {
    if (!item) return;
    if (current?.id === item.id && current.revision_id === item.revision_id) { toggle(); return; }
    select(item, true);
  }
  function restore(item) {
    if (item && audioUrl(item)) select(item, false);
  }

  function close() { generation += 1; savePosition(); audio.pause(); audio.removeAttribute('src'); audio.load(); current = null; phase = 'idle'; confirmedChapterSeconds = 0; chapterSeekInFlight = false; if (dialog?.open) dialog.close(); render(); }
  audio.addEventListener('timeupdate', () => { if (!chapterSeekInFlight && !audio.seeking) confirmedChapterSeconds = audio.currentTime; if (Math.floor(audio.currentTime) - lastSavedSecond >= 5) savePosition(); render(); });
  audio.addEventListener('seeked', () => { if (chapterSeekInFlight) finishChapterSeek(); else { confirmedChapterSeconds = audio.currentTime; render(); } });
  audio.addEventListener('ended', () => { phase = 'complete'; savePosition(); render(); });
  audio.addEventListener('error', () => { if (chapterSeekInFlight) { chapterSeekInFlight = false; chapterError = 'Could not move to that chapter. Try again.'; } if (current?.audio?.state === 'recorded') { phase = 'error'; render(); } });
  audio.addEventListener('waiting', () => { if (phase === 'playing') { phase = 'loading'; render(); } });
  audio.addEventListener('playing', () => { if (!audio.paused && phase === 'loading') { phase = 'playing'; render(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(); });
  window.addEventListener('pagehide', savePosition);
  return { play, restore, close, refresh: render, get status() { return phase; }, get item() { return current; }, get label() { return actionLabel(); } };
}
