const chapters = episode => Array.isArray(episode?.chapters) ? episode.chapters : [];
const cleanText = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const isWithdrawn = item => item?.withdrawn === true || cleanText(item?.status).toLocaleLowerCase() === 'withdrawn';
const isInactive = item => isWithdrawn(item) || item?.superseded === true ||
  cleanText(item?.status).toLocaleLowerCase() === 'superseded';
function displayDate(value) {
  const text = cleanText(value);
  if (!/\b\d{4}\b/u.test(text)) return text;
  const date = new Date(text);
  return Number.isNaN(date.getTime())
    ? text
    : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}
function correctionForChapter(episode, chapter) {
  return (Array.isArray(episode?.corrections) ? episode.corrections : []).find(correction => {
    if (correction?.severity !== 'material' || !correction.corrected_at ||
        (episode.revision_id && correction.revision_id && correction.revision_id !== episode.revision_id) ||
        (episode.revision_id && correction.previous_revision_id === episode.revision_id)) return false;
    const sectionId = chapter.section_id || chapter.id;
    return correction.chapter_id === chapter.id ||
      (Array.isArray(correction.section_ids) && correction.section_ids.includes(sectionId));
  }) || null;
}
const sourceName = source => {
  if (!source || isInactive(source)) return '';
  const name = cleanText(source?.outlet || source?.publisher || source?.name);
  return name || (cleanText(source?.title) ? 'Source' : '');
};

const citationNumbers = (sentence, sources) => {
  const sourceIds = Array.isArray(sentence.source_ids) ? sentence.source_ids : [];
  const numbers = sourceIds.length
    ? sourceIds.map(id => {
      const source = sources.find(row => row.id === id);
      return sourceName(source) ? Number(source.number) || sources.indexOf(source) + 1 : 0;
    }).filter(number => number > 0)
    : (Array.isArray(sentence.source_numbers) ? sentence.source_numbers.filter(raw => {
      const number = Number(raw);
      return Number.isInteger(number) && sources.some((source, index) =>
        (Number(source?.number) || index + 1) === number && sourceName(source));
    }) : []);
  return [...new Set(numbers.map(Number).filter(number => Number.isInteger(number) && number > 0))].sort((a, b) => a - b);
};

export function buildPlainTranscript(episode) {
  const blocks = [];
  const title = cleanText(episode?.title || episode?.topic_label || episode?.topic);
  if (title) blocks.push(title);
  const sources = Array.isArray(episode?.sources) ? episode.sources : [];
  const citedNumbers = new Set();
  for (const chapter of chapters(episode)) {
    if (isInactive(chapter)) {
      if (isWithdrawn(chapter)) blocks.push('Story withdrawn');
      continue;
    }
    const rows = [cleanText(chapter?.title)];
    const correction = correctionForChapter(episode, chapter);
    if (correction) rows.push(`Corrected ${displayDate(correction.corrected_at)}`);
    for (const sentence of Array.isArray(chapter?.sentences) ? chapter.sentences : []) {
      if (isInactive(sentence)) continue;
      const text = cleanText(sentence?.text);
      if (!text) continue;
      const numbers = citationNumbers(sentence, sources);
      numbers.forEach(number => citedNumbers.add(number));
      rows.push(numbers.length ? `${text} [${numbers.join(', ')}]` : text);
    }
    if (rows.length > 1 || rows[0]) blocks.push(rows.filter(Boolean).join('\n'));
  }
  const sourceLines = sources.map((source, index) => {
    const name = sourceName(source);
    const label = cleanText(source?.title);
    const number = Number(source?.number) || index + 1;
    const published = source.published_at || source.published || source.date;
    const updated = source.updated_at;
    const dateLabel = published ? ` (Published ${displayDate(published)})`
      : updated ? ` (Updated ${displayDate(updated)})` : ' (Date not listed)';
    return { number, line: name ? `[${number}] ${name}${label ? ` — ${label}` : ''}${dateLabel}` : '' };
  }).filter(row => row.line && citedNumbers.has(row.number))
    .sort((a, b) => a.number - b.number).map(row => row.line);
  if (sourceLines.length) blocks.push(`Sources\n${sourceLines.join('\n')}`);
  return blocks.join('\n\n');
}

function seconds(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new TypeError('Caption times must be finite non-negative seconds.');
  return number;
}

function timestamp(value) {
  const millis = Math.round(seconds(value) * 1000);
  const hours = Math.floor(millis / 3_600_000);
  const minutes = Math.floor((millis % 3_600_000) / 60_000);
  const wholeSeconds = Math.floor((millis % 60_000) / 1000);
  const fraction = millis % 1000;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(wholeSeconds).padStart(2, '0')}.${String(fraction).padStart(3, '0')}`;
}

function sentenceTiming(sentence, chapterOffset) {
  const absoluteStart = sentence.audio_start_seconds;
  const absoluteEnd = sentence.audio_end_seconds;
  if (absoluteStart != null || absoluteEnd != null) {
    if (absoluteStart == null || absoluteEnd == null) throw new TypeError('Each caption sentence needs both timing values.');
    return [seconds(absoluteStart), seconds(absoluteEnd)];
  }
  const start = sentence.start_seconds ?? sentence.start;
  const end = sentence.end_seconds ?? sentence.end;
  if (start == null || end == null) throw new TypeError('Each caption sentence needs both timing values.');
  return [chapterOffset + seconds(start), chapterOffset + seconds(end)];
}

export function buildWebVtt(episode) {
  const cues = [];
  let previousEnd = -1;
  for (const chapter of chapters(episode)) {
    const offset = seconds(chapter.audio_start_seconds ?? chapter.start_seconds ?? 0);
    const sentences = Array.isArray(chapter.sentences) ? chapter.sentences : [];
    if (!sentences.length) throw new TypeError('Every caption chapter needs timed transcript sentences.');
    for (const sentence of sentences) {
      const text = cleanText(sentence?.text);
      if (!text) throw new TypeError('Every caption sentence needs spoken text.');
      const [start, end] = sentenceTiming(sentence, offset);
      if (end <= start || start < previousEnd) throw new TypeError('Caption timings must be ordered and have positive duration.');
      previousEnd = end;
      cues.push(`${timestamp(start)} --> ${timestamp(end)}\n${text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}`);
    }
  }
  if (!cues.length) throw new TypeError('Episode has no timed transcript sentences.');
  return `WEBVTT\n\n${cues.join('\n\n')}\n`;
}

export async function copyEpisodeTranscript(episode, clipboard = globalThis.navigator?.clipboard) {
  if (!clipboard || typeof clipboard.writeText !== 'function') throw new Error('Clipboard access is unavailable.');
  const text = buildPlainTranscript(episode);
  await clipboard.writeText(text);
  return text;
}

export function downloadEpisodeTranscript(episode, {
  documentRef = globalThis.document,
  urlApi = globalThis.URL,
  BlobConstructor = globalThis.Blob,
} = {}) {
  if (!documentRef?.createElement || !documentRef?.body?.append) throw new Error('A document is required to download the transcript.');
  if (typeof urlApi?.createObjectURL !== 'function' || typeof urlApi?.revokeObjectURL !== 'function' ||
      typeof BlobConstructor !== 'function') throw new Error('Transcript downloads are unavailable.');

  const text = buildPlainTranscript(episode);
  const url = urlApi.createObjectURL(new BlobConstructor([text], { type: 'text/plain;charset=utf-8' }));
  const link = documentRef.createElement('a');
  link.href = url;
  link.download = 'episode-transcript.txt';
  try {
    documentRef.body.append(link);
    link.click();
    return text;
  } finally {
    link.remove();
    urlApi.revokeObjectURL(url);
  }
}

export function createTranscriptCopyControl(episode, {
  documentRef = globalThis.document,
  clipboard = globalThis.navigator?.clipboard,
} = {}) {
  if (!documentRef) throw new Error('A document is required to create the copy control.');
  const group = documentRef.createElement('div');
  group.className = 'episode-transcript-export';
  const button = documentRef.createElement('button');
  button.type = 'button';
  button.className = 'primary episode-copy-transcript';
  button.textContent = 'Copy transcript';
  const status = documentRef.createElement('span');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  button.addEventListener('click', async () => {
    try {
      await copyEpisodeTranscript(episode, clipboard);
      status.textContent = 'Transcript copied.';
    } catch {
      status.textContent = 'Copy unavailable. Select the transcript text to copy it.';
    }
  });
  group.append(button, status);
  return group;
}
