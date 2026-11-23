import assert from 'node:assert/strict';
import { rowPreview, hasMaterialCorrection } from '../../src/edition/edition.js';
import { cleanSpeechText } from '../../src/player.js';
import { audioScriptText } from '../../src/audio-script.js';

assert.equal(rowPreview('What happened: The Board changed its service fees. [1] More detail follows. [2]'),
  'The Board changed its service fees.', 'The first real sentence omits its section label and bracketed citations');
assert.equal(rowPreview('What happened: First sentence.\n\nWhy it matters: Second sentence. [1]'),
  'First sentence.', 'The preview uses the first sentence rather than a later section');
assert.equal(rowPreview('WHY IT MATTERS: Evidence follows. [1]'),
  'Evidence follows.', 'The preview removes a case-insensitive section label and its citation marker');
assert.equal(rowPreview('Things to consider: Check the update. [1, 2]'),
  'Check the update.', 'The preview removes the final section label and multiple citation markers');
assert.equal(rowPreview('Limits: Only a headline or feed snippet was available.\n\nWhy it matters: No broader context yet.'),
  'No broader context yet.', 'Limits paragraphs never become Today previews');
assert.equal(rowPreview('Limits: Only a headline or feed snippet was available.'),
  '', 'A legacy Limits line does not create a Today preview');
assert.equal(cleanSpeechText('A useful update.\n\nLimits: Only a headline was available.'),
  'A useful update.', 'Legacy Limits lines are never spoken');
assert.equal(audioScriptText({ audio_script: 'A useful update.\n\nLimits: Only a headline was available.' }),
  'A useful update.', 'Legacy Limits lines are removed from visible audio transcripts too');
assert.equal(rowPreview('What happened: <p>Rates &amp; savings updated.</p> <a href="https://example.test/story">Read details</a>.'),
  'Rates & savings updated.', 'Raw paragraph/link markup is stripped and ampersand entities become plain text in previews');
assert.equal(rowPreview('What happened: <p>Rates &amp; savings updated.</p> <a href="javascript:alert(1)">Read details</a>.'),
  'Rates & savings updated.', 'Untrusted link markup never becomes preview content');
const zelleTitle = 'Zelle Picks Solana for ZelleUSD Stablecoin';
assert.equal(rowPreview('U.S. peer-to-peer payment network Zelle is partnering with a bank. More follows.'),
  'U.S. peer-to-peer payment network Zelle is partnering with a bank.');
assert.equal(rowPreview(`${zelleTitle}. Zelle added international payment support.`, 36, zelleTitle),
  'Zelle added international payment…', 'A preview skips the sentence that repeats the headline');
assert.equal(rowPreview(`${zelleTitle}. [1]`, 36, zelleTitle), '', 'A title-only body has no redundant preview');
const longSentence = 'What happened: The central bank announced a significantly expanded service schedule that will begin in January for the participating institutions. [1] More details follow.';
const preview = rowPreview(longSentence);
const cleanSentence = 'The central bank announced a significantly expanded service schedule that will begin in January for the participating institutions.';
assert.ok(preview.length <= 90 && preview.endsWith('…'), 'Long first sentences are capped with an ellipsis');
assert.ok(cleanSentence.startsWith(preview.slice(0, -1)) && cleanSentence[preview.length - 1] === ' ', 'Long previews end on a word boundary');

const correctionEdition = {
  revision_id: 'r2', sections: [{ id: 'story-1' }, { id: 'story-2' }], corrections: [{
    severity: 'material', previous_revision_id: 'r1', revision_id: 'r2', corrected_at: '2026-11-17T12:00:00Z',
    section_ids: ['story-1'], what_changed: 'A material detail changed.', current_text: 'Current wording.', why_it_matters: 'The distinction matters.',
  }],
};
assert.equal(hasMaterialCorrection(correctionEdition), true, 'A revision-linked material correction is valid');
assert.equal(hasMaterialCorrection(correctionEdition, 'story-2'), false, 'Correction is scoped to affected stories');
const unlinked = structuredClone(correctionEdition);
delete unlinked.corrections[0].revision_id;
assert.equal(hasMaterialCorrection(unlinked), false, 'Unlinked generated correction metadata does not create a Corrected state');
console.log('Edition previews: labels, citations, first sentence, 90-character word-boundary cap and correction validation passed.');
