const element = (tag, className = '', text = '') => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== '') item.textContent = text;
  return item;
};

function safeHref(value) {
  const raw = String(value || '').trim();
  if (!raw || /[\u0000-\u001f\u007f]/u.test(raw)) return null;
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && url.hostname && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function inactive(item) {
  const status = String(item?.status || '').trim().toLocaleLowerCase();
  return item?.withdrawn === true || item?.superseded === true || status === 'withdrawn' || status === 'superseded';
}

function withdrawn(item) {
  return item?.withdrawn === true || String(item?.status || '').trim().toLocaleLowerCase() === 'withdrawn';
}

function sourceDateLabel(source) {
  const publication = String(source?.date || source?.published_at || '').trim();
  const updated = String(source?.updated_at || '').trim();
  const raw = publication || updated;
  if (!raw) return 'Date not listed';
  const iso = /^(\d{4}-\d{2}-\d{2})(?:T|$)/u.exec(raw)?.[1];
  const formatted = iso
    ? (() => {
      const date = new Date(`${iso}T12:00:00Z`);
      return Number.isNaN(date.getTime())
        ? raw
        : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
    })()
    : raw;
  return publication ? formatted : `Updated ${formatted}`;
}

function episodeMeta(episode) {
  const sources = episode.sources?.length || 0;
  const chapters = episode.chapters?.length || 0;
  return `${episode.duration_minutes} min · ${sources} sources · ${chapters} chapters · made ${episode.made_date}`;
}

function updatedTag(episode) {
  if (!episode.updated) return null;
  const tag = element('a', 'episode-updated', 'Updated');
  tag.href = '#corrections';
  tag.setAttribute('aria-label', 'Updated · See corrections');
  tag.title = `${episode.updated.date}: ${episode.updated.note}`;
  return tag;
}

function listenControl(episode, onListen = null) {
  const group = element('div', 'episode-listen-group');
  const listen = element('button', 'primary episode-listen', 'Listen');
  listen.type = 'button';
  if (!episode.audio_recorded) {
    const statusId = `audio-unavailable-${episode.id}`;
    const status = element('span', 'episode-audio-unavailable', 'Audio not recorded yet'); status.id = statusId;
    listen.disabled = true;
    listen.setAttribute('aria-describedby', statusId);
    group.append(listen, status);
  } else {
    listen.setAttribute('aria-label', `Listen to ${episode.title}`);
    if (typeof onListen === 'function') listen.addEventListener('click', () => onListen(episode));
    group.append(listen);
  }
  return group;
}

function sourceMarker(source, number) {
  const details = element('details', 'episode-citation');
  const summary = element('summary', '', String(number));
  const outlet = String(source.outlet || 'Source').trim();
  const title = String(source.title || '').replace(/\s+/gu, ' ').trim();
  summary.setAttribute('aria-label', title ? `Source ${number}, ${outlet} · ${title}` : `Source ${number}, ${outlet}`);
  details.append(summary);
  const body = element('span', 'episode-citation-detail');
  body.append(element('strong', '', outlet));
  if (title) body.append(element('span', 'episode-citation-title', title));
  body.append(element('time', '', sourceDateLabel(source)));
  const href = safeHref(source.url);
  if (href) {
    const link = element('a', '', 'Open source');
    link.setAttribute('aria-label', title ? `Open source: ${outlet}, ${title}` : `Open source: ${outlet}`);
    link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    body.append(link);
  }
  details.append(body);
  return details;
}

function renderSentence(sentence, sourceNumbers, sourcesById) {
  if (inactive(sentence)) return null;
  const paragraph = element('p', 'episode-sentence', sentence.text);
  for (const id of sentence.source_ids || []) {
    const source = sourcesById.get(id);
    if (source) paragraph.append(sourceMarker(source, sourceNumbers.get(id)));
  }
  return paragraph;
}

export function renderEpisodePage(container, episode, { onListen = null, inDailyBriefing = false, onDailyBriefingChange = null } = {}) {
  const page = element('article', 'episode-page');
  const withdrawnEpisode = withdrawn(episode);
  const currentChapters = (episode.chapters || []).filter(chapter => withdrawn(chapter) || !inactive(chapter));
  const back = element('a', 'episode-back', '‹ On demand'); back.href = '#episodes'; page.append(back);
  if (episode.updated) {
    const updated = element('aside', 'episode-updated-note');
    const history = element('a', 'episode-correction-history-link', 'See all corrections'); history.href = '#corrections';
    updated.append(updatedTag(episode), element('span', '', `${episode.updated.date} · ${episode.updated.note}`), history);
    page.append(updated);
  }
  page.append(element('p', 'episode-topic', episode.topic_label));
  const title = element('h1', '', withdrawnEpisode ? 'Story withdrawn' : episode.title); title.tabIndex = -1; page.append(title);
  if (!withdrawnEpisode) {
    const asked = element('blockquote', 'episode-asked');
    asked.append(element('strong', '', 'Asked:'), element('p', '', episode.asked)); page.append(asked);
  }
  page.append(element('p', 'episode-meta', episodeMeta(episode)));
  if (typeof onDailyBriefingChange === 'function') {
    const section = element('section', 'episode-daily-briefing');
    const label = element('label', 'episode-daily-briefing-toggle');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox'; toggle.checked = Boolean(inDailyBriefing);
    toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-label', 'Add to my daily briefing');
    const copy = element('span', '', 'Add to my daily briefing');
    label.append(toggle, copy);
    const status = element('p', 'episode-daily-briefing-status', inDailyBriefing ? 'Already in your daily briefing.' : '');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('aria-atomic', 'true');
    toggle.addEventListener('change', async () => {
      const requested = toggle.checked;
      toggle.disabled = true; status.textContent = requested ? 'Adding topic…' : 'Updating your topics…';
      try {
        await onDailyBriefingChange(episode, requested);
        status.textContent = requested ? 'Added to your daily briefing.' : 'Removed from your daily briefing.';
      } catch (error) {
        toggle.checked = !requested;
        status.textContent = error?.message || 'Could not update your daily briefing. Try again.';
      } finally { toggle.disabled = false; }
    });
    section.append(label, status); page.append(section);
  }
  if (!withdrawnEpisode) page.append(listenControl(episode, onListen));

  const chaptersHeading = element('h2', '', 'Chapters');
  page.append(chaptersHeading);
  const chapterList = element('ol', 'episode-chapter-list');
  chapterList.setAttribute('aria-label', 'Episode chapters');
  if (withdrawnEpisode) chapterList.append(element('li', '', 'Story withdrawn'));
  for (const chapter of withdrawnEpisode ? [] : currentChapters) {
    const item = element('li');
    const link = element('a', '', withdrawn(chapter) ? 'Story withdrawn' : chapter.title); link.href = `#episode-chapter-${encodeURIComponent(chapter.id)}`;
    link.addEventListener('click', event => {
      event.preventDefault();
      const target = document.getElementById(`episode-chapter-${chapter.id}`);
      target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
      target?.querySelector('h3')?.focus({ preventScroll: true });
    });
    item.append(link); chapterList.append(item);
  }
  page.append(chapterList);

  const currentSources = (episode.sources || []).filter(source => !inactive(source));
  const sourceNumbers = new Map(currentSources.map((source, index) => [source.id, index + 1]));
  const sourcesById = new Map(currentSources.map(source => [source.id, source]));
  const transcript = element('section', 'episode-transcript');
  transcript.append(element('h2', '', 'Transcript'));
  transcript.append(element('p', 'episode-transcript-note', 'Tap a source number to see its publisher and date.'));
  if (withdrawnEpisode) {
    const section = element('section', 'episode-chapter');
    const chapterHeading = element('h3', '', 'Story withdrawn'); chapterHeading.tabIndex = -1;
    section.append(chapterHeading); transcript.append(section);
  }
  for (const chapter of withdrawnEpisode ? [] : currentChapters) {
    const section = element('section', 'episode-chapter');
    section.id = `episode-chapter-${chapter.id}`;
    const chapterHeading = element('h3', '', withdrawn(chapter) ? 'Story withdrawn' : chapter.title); chapterHeading.tabIndex = -1;
    section.append(chapterHeading);
    if (!withdrawn(chapter)) {
      for (const sentence of chapter.sentences || []) {
        const paragraph = renderSentence(sentence, sourceNumbers, sourcesById);
        if (paragraph) section.append(paragraph);
      }
    }
    transcript.append(section);
  }
  page.append(transcript);
  if (!withdrawnEpisode && episode.could_not_confirm?.length && !episode.chapters?.some(chapter => chapter.id === 'evidence-notes')) {
    const uncertain = element('section', 'episode-uncertain');
    uncertain.append(element('h2', '', "What we couldn't confirm"));
    const list = element('ul');
    for (const item of episode.could_not_confirm) list.append(element('li', '',
      typeof item === 'string' ? item : item.question || item.unverified_claim || ''));
    uncertain.append(list); page.append(uncertain);
  }
  const report = element('details', 'episode-report');
  report.append(element('summary', '', 'Report a mistake'), element('p', '', 'In this fixture preview, reports are not sent. Note the episode title and the passage that needs a correction.'));
  page.append(report);
  container.replaceChildren(page);
  title.focus({ preventScroll: true });
}

export function renderEpisodeDeck(container, episodes, { onClose, onOpen, onListen = null }) {
  const controller = new AbortController();
  const page = element('section', 'episode-deck-page');
  page.setAttribute('role', 'dialog'); page.setAttribute('aria-modal', 'true');
  page.setAttribute('aria-labelledby', 'episode-deck-heading');
  page.tabIndex = -1;
  const dialogHeading = element('h1', 'visually-hidden', 'Episode cards');
  dialogHeading.id = 'episode-deck-heading';
  const topbar = element('header', 'episode-deck-topbar');
  const shuffle = element('button', 'episode-deck-shuffle', 'Shuffle'); shuffle.type = 'button';
  const close = element('button', 'episode-deck-close', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Close episode cards');
  close.addEventListener('click', onClose);
  topbar.append(shuffle, close);
  const track = element('div', 'episode-deck-track');
  track.setAttribute('role', 'region'); track.setAttribute('aria-label', 'Swipe through episodes'); track.tabIndex = 0;
  const controls = element('div', 'episode-deck-controls');
  const previous = element('button', '', 'Previous'); previous.type = 'button'; previous.setAttribute('aria-label', 'Previous episode');
  const position = element('p', 'episode-deck-position', ''); position.setAttribute('aria-live', 'polite');
  const next = element('button', '', 'Next'); next.type = 'button'; next.setAttribute('aria-label', 'Next episode');
  controls.append(previous, position, next);
  page.append(dialogHeading, topbar, track, controls); container.replaceChildren(page);

  let ordered = [...episodes];
  let activeIndex = 0;
  const paint = () => {
    track.replaceChildren();
    for (const [index, episode] of ordered.entries()) {
      const card = element('article', 'episode-card');
      card.setAttribute('aria-label', `Episode ${index + 1} of ${ordered.length}`);
      card.inert = index !== activeIndex;
      const top = element('div', 'episode-card-topline');
      top.append(element('span', 'episode-topic', episode.topic_label));
      const changed = updatedTag(episode); if (changed) top.append(changed);
      card.append(top, element('h2', '', episode.title), element('p', 'episode-meta', episodeMeta(episode)), element('p', 'episode-hook', episode.hook));
      const asked = element('blockquote', 'episode-asked');
      asked.append(element('strong', '', 'Asked:'), element('p', '', episode.asked)); card.append(asked);
      const preview = element('button', 'episode-preview', '30-second preview');
      preview.type = 'button'; preview.disabled = true; preview.setAttribute('aria-label', '30-second preview, not available in this fixture'); card.append(preview);
      card.append(listenControl(episode, onListen));
      const read = element('a', 'button-link episode-open', 'Read episode'); read.href = `#episode/${encodeURIComponent(episode.id)}`;
      read.addEventListener('click', event => { event.preventDefault(); onOpen(episode.id); }); card.append(read);
      track.append(card);
    }
    updatePosition(false);
    track.scrollTo({ left: activeIndex * track.clientWidth, behavior: 'auto' });
  };
  function updatePosition(fromScroll = true) {
    if (fromScroll && track.clientWidth) activeIndex = Math.max(0, Math.min(ordered.length - 1, Math.round(track.scrollLeft / track.clientWidth)));
    position.textContent = `${activeIndex + 1} of ${ordered.length}`;
    [...track.children].forEach((card, index) => { card.inert = index !== activeIndex; });
    previous.disabled = activeIndex === 0; next.disabled = activeIndex >= ordered.length - 1;
  }
  const moveTo = index => {
    activeIndex = Math.max(0, Math.min(ordered.length - 1, index));
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    track.scrollTo({ left: activeIndex * track.clientWidth, behavior: reduceMotion ? 'auto' : 'smooth' });
    updatePosition(false);
  };
  track.addEventListener('scroll', updatePosition, { passive: true });
  previous.addEventListener('click', () => moveTo(activeIndex - 1));
  next.addEventListener('click', () => moveTo(activeIndex + 1));
  shuffle.addEventListener('click', () => {
    const previousOrder = ordered.map(episode => episode.id).join('|');
    for (let index = ordered.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1)); [ordered[index], ordered[swap]] = [ordered[swap], ordered[index]];
    }
    if (ordered.length > 1 && ordered.map(episode => episode.id).join('|') === previousOrder) ordered.push(ordered.shift());
    activeIndex = 0; paint();
  });
  document.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || (event.target instanceof Element && event.target.closest('input,textarea,select,[contenteditable="true"]'))) return;
    if (event.key === 'Tab') {
      const focusable = [...page.querySelectorAll('button:not([disabled]),a[href],summary:not([aria-disabled="true"]),[tabindex]:not([tabindex="-1"])')]
        .filter(item => !item.closest('[inert]') && item.getClientRects().length > 0);
      if (event.shiftKey && (document.activeElement === page || document.activeElement === focusable[0])) { event.preventDefault(); focusable.at(-1)?.focus(); }
      else if (!event.shiftKey && (document.activeElement === page || document.activeElement === focusable.at(-1))) { event.preventDefault(); focusable[0]?.focus(); }
      return;
    }
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key === 'ArrowRight' && activeIndex < ordered.length - 1) { event.preventDefault(); moveTo(activeIndex + 1); }
    if (event.key === 'ArrowLeft' && activeIndex > 0) { event.preventDefault(); moveTo(activeIndex - 1); }
  }, { signal: controller.signal });
  paint(); page.focus({ preventScroll: true });
  return () => controller.abort();
}
