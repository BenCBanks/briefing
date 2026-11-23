const EDITORIAL_NOTE = /^\s*(?:limits?|citations?|coverage(?:\s+(?:note|status|today|reviewed|limited(?:\s+today)?))?|source\s+status|evidence)\s*:/i;
const INLINE_NOTE = /\s+(?:limits?|citations?|coverage(?:\s+(?:note|status|today|reviewed|limited(?:\s+today)?))?|source\s+status|evidence)\s*:.*$/i;
const SOURCE_LINE = /^\s*(?:(?:source|sources)\s*:\s*|reporting\s+from\s+)/i;
const EXCLUDED_SECTIONS = new Set(['limits', 'coverage', 'citations', 'source', 'sources', 'source_status', 'evidence']);

// Keep note removal consistent for transcript rendering and every speech path.
export function withoutCitationNotes(value) {
  let citationBlock = false;
  return String(value || '').split(/\n\s*\n/).map(paragraph => {
    if (/^\s*(?:limits?|citations?|sources?|source\s+status|evidence)\s*[:.]?\s*$/i.test(paragraph)) {
      citationBlock = true;
      return '';
    }
    if (/^\s*(?:what happened|why it matters|things to consider|read|source)\s*:/i.test(paragraph)) citationBlock = false;
    if (citationBlock || EDITORIAL_NOTE.test(paragraph)) return '';
    return paragraph.replace(INLINE_NOTE, '').trim();
  }).filter(Boolean).join('\n\n');
}

function cleanLines(text, { collectSources = false } = {}) {
  const lines = [];
  const sources = [];
  let citationBlock = false;
  for (let line of String(text || '').split(/\r?\n/)) {
    line = line.trim();
    if (!line) continue;
    if (/^(?:limits?|citations?|sources?|source\s+status|evidence)\s*[:.]?\s*$/i.test(line)) {
      citationBlock = true;
      continue;
    }
    if (/^(?:what happened|why it matters|things to consider|read)\s*:/i.test(line)) citationBlock = false;
    if (citationBlock || EDITORIAL_NOTE.test(line)) continue;
    if (SOURCE_LINE.test(line)) {
      if (collectSources) sources.push(line);
      continue;
    }
    line = line.replace(INLINE_NOTE, '').trim();
    if (line) lines.push(line);
  }
  return { text: lines.join(' ').trim(), sources };
}

function sourceAttribution(script, candidates) {
  const raw = typeof script?.source_attribution === 'string' ? script.source_attribution
    : Array.isArray(script?.source_names) ? script.source_names[0] || '' : candidates[0] || '';
  const source = cleanLines(raw, { collectSources: true });
  const value = source.sources[0] || source.text;
  const name = value.replace(SOURCE_LINE, '').split(/[,;]|\s+and\s+/i, 1)[0].replace(/[.!?]+\s*$/, '').trim();
  return name ? `Source: ${name}.` : '';
}

function partsFromText(text, sourceCandidates) {
  const parts = [];
  let citationBlock = false;
  for (const raw of String(text || '').split(/\n\s*\n/)) {
    if (/^\s*(?:limits?|citations?|sources?|source\s+status|evidence)\s*[:.]?\s*$/i.test(raw)) {
      citationBlock = true;
      continue;
    }
    if (/^\s*(?:what happened|why it matters|things to consider|read)\s*:/i.test(raw)) citationBlock = false;
    if (citationBlock) continue;
    const cleaned = cleanLines(raw, { collectSources: true });
    sourceCandidates.push(...cleaned.sources);
    if (cleaned.text) parts.push(cleaned.text);
  }
  return parts;
}

export function audioScriptText(section) {
  const script = section?.audio_script;
  if (!script) return '';
  const sourceCandidates = [];
  const parts = [];
  const paragraphs = Array.isArray(script?.paragraphs) ? script.paragraphs : Array.isArray(script) ? script : null;
  if (typeof script === 'string') {
    parts.push(...partsFromText(script, sourceCandidates));
  } else if (paragraphs?.length) {
    if (typeof script.orientation === 'string') parts.push(...partsFromText(script.orientation, sourceCandidates));
    let previousSection = null;
    for (const paragraph of paragraphs) {
      const section = typeof paragraph === 'object' ? String(paragraph?.section || '').toLocaleLowerCase() : '';
      if (EXCLUDED_SECTIONS.has(section)) continue;
      const value = typeof paragraph === 'string' ? paragraph : paragraph?.text;
      const narrative = partsFromText(value, sourceCandidates);
      if (!narrative.length) continue;
      if (section && section !== previousSection && section !== 'what_happened') {
        const label = section === 'why_it_matters' ? 'Why it matters.'
          : section === 'things_to_consider' ? 'Things to consider.' : '';
        if (label) parts.push(label);
      }
      parts.push(...narrative);
      previousSection = section;
    }
  } else {
    parts.push(...partsFromText(script.text, sourceCandidates));
  }
  const attribution = sourceAttribution(script, sourceCandidates);
  if (attribution) parts.push(attribution);
  return parts.join('\n\n').trim();
}

export function audioScriptParagraphs(section) {
  return audioScriptText(section).split(/\n\s*\n/).map(paragraph => paragraph.trim()).filter(Boolean);
}

export function narrationParagraphs(text) {
  const sourceCandidates = [];
  const parts = partsFromText(text, sourceCandidates);
  const attribution = sourceAttribution(null, sourceCandidates);
  if (attribution) parts.push(attribution);
  return parts.filter(Boolean);
}
