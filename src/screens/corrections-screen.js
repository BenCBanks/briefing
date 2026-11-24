export function createCorrectionsScreenController({ content, location, renderCorrections }) {
  function correctionPassage(revision, correction, targetType) {
    if (targetType === 'story') {
      const section = revision?.sections?.find(item => item.id === correction.target_id);
      if (!section) return { title: 'Daily story', text: '' };
      const paragraphs = String(section.body || '').split(/\n\s*\n/);
      const paragraph = paragraphs[correction.paragraph_index] || '';
      if (!Number.isInteger(correction.sentence_index)) return { title: section.heading, text: paragraph };
      const sentences = paragraph.match(/\S.*?(?:[.!?](?=\s|$)|$)/g) || [];
      return { title: section.heading, text: sentences[correction.sentence_index] || '' };
    }
    const episode = revision?.episode || revision;
    const chapter = episode?.chapters?.find(item => item.id === correction.chapter_id);
    const sentence = chapter?.sentences?.[correction.sentence_index];
    return { title: episode?.title || episode?.topic_label || 'Episode', text: sentence?.text || '' };
  }

  async function correctionRevision(targetType, targetId, revisionId) {
    if (!targetId || !revisionId) return null;
    const response = await fetch(`/api/revisions/${targetType === 'story' ? 'edition' : 'episode'}/${encodeURIComponent(targetId)}/${encodeURIComponent(revisionId)}`, { cache: 'no-store' });
    if (!response.ok) return null;
    const result = await response.json().catch(() => ({}));
    return result[targetType === 'story' ? 'edition' : 'episode'] || null;
  }

  async function correctionViewModels(records) {
    return Promise.all(records.map(async correction => {
      const targetType = correction.target_type === 'episode' ? 'episode' : 'story';
      const targetId = targetType === 'story' ? correction.edition_id : correction.target_id;
      const [beforeRevision, afterRevision] = await Promise.all([
        correctionRevision(targetType, targetId, correction.previous_revision_id),
        correctionRevision(targetType, targetId, correction.revision_id),
      ]);
      const before = correctionPassage(beforeRevision, correction, targetType);
      const after = correctionPassage(afterRevision, correction, targetType);
      const embedded = (afterRevision?.corrections || []).find(item => item.id === correction.id);
      const afterText = embedded?.current_text || after.text;
      const title = after.title || before.title;
      const targetHash = targetType === 'story'
        ? `#edition/${encodeURIComponent(afterRevision?.kind || 'daily')}/${encodeURIComponent(correction.edition_id)}/${encodeURIComponent(correction.target_id)}`
        : `?episodes=1#episode/${encodeURIComponent(correction.target_id)}`;
      return { ...correction, target_type: targetType, title, target_hash: targetHash, before: before.text, after: afterText };
    }));
  }

  async function renderCorrectionsPage() {
    const renderError = () => renderCorrections(content, [], {
      error: "Corrections couldn't load. Please try again.", onRetry: renderCorrectionsPage,
    });
    renderCorrections(content, [], { loading: true });
    try {
      const response = await fetch('/api/corrections', { cache: 'no-store' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !Array.isArray(result.corrections)) throw new Error('corrections_unavailable');
      const items = await correctionViewModels(result.corrections);
      if (location.hash === '#corrections') renderCorrections(content, items);
    } catch {
      if (location.hash === '#corrections') renderError();
    }
  }

  return { renderCorrectionsPage };
}
