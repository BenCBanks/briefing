import { audioScriptParagraphs, audioScriptText, withoutCitationNotes } from '../audio-script.js';

const CHUNK_LIMIT = 180;

export function chapterText(chapter) {
  const script = audioScriptText(chapter.section);
  if (script) return script;
  return withoutCitationNotes(chapter.section?.body)
    .replace(/\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, '')
    .replace(/(^|\n\s*)(?:What happened|Why it matters|Things to consider)\s*:\s*/gim, '$1');
}

export function chapterParagraphs(chapter) {
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

