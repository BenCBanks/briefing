const esc = (value = '') => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const prettyDate = value => {
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
};
const coverageLabels = { covered: 'Covered', update: 'Covered', context: 'Context', quiet: 'Quiet today', limited: 'Coverage limited' };
const correctionDate = value => {
  const date = new Date(String(value || '').slice(0, 10) + 'T12:00:00Z');
  return Number.isNaN(date.getTime()) ? 'Material' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', timeZone: 'UTC' }).format(date);
};
export const visibleSections = item => {
  const quiet = new Set((item.coverage || []).filter(row => ['quiet', 'limited'].includes(row.state)).map(row => row.topic_id));
  const visible = item.sections.filter(section => !(section.topic_ids || []).length || section.topic_ids.some(id => !quiet.has(id)));
  return visible.length ? visible : item.sections;
};
const readingMinutes = item => Math.max(1, Math.ceil(visibleSections(item).reduce((count, section) => count + section.body.split(/\s+/).length, 0) / 225));

export function renderToday(container, edition, topics, actions = {}) {
  if (!edition?.sections?.length) { container.innerHTML = '<p>Today’s edition is unavailable.</p>'; return; }
  const topicById = new Map(topics.map(topic => [topic.id, topic]));
  const sectionForTopic = new Map();
  visibleSections(edition).forEach((section, index) => (section.topic_ids || []).forEach(id => {
    if (!sectionForTopic.has(id)) sectionForTopic.set(id, { section, index });
  }));
  const covered = edition.coverage.filter(row => !['quiet', 'limited'].includes(row.state) && sectionForTopic.has(row.topic_id));
  const other = edition.coverage.filter(row => ['quiet', 'limited'].includes(row.state) || !sectionForTopic.has(row.topic_id));
  const place = actions.readingPlace?.(edition.id, edition.revision_id);
  const sections = visibleSections(edition);
  const currentIndex = sections.findIndex(section => section.id === place);
  const currentSection = currentIndex >= 0 ? sections[currentIndex] : null;
  const currentTopic = currentSection?.topic_ids?.map(id => topicById.get(id)?.label).filter(Boolean)[0] || 'Today';
  const weekly = actions.weekly;
  container.innerHTML = `<article class="today-page">
    <div class="edition-topline"><span>${esc(prettyDate(edition.local_date))}</span><span class="sample-label">Sample · Fictional content</span></div>
    <h1>${esc(edition.title)}</h1>
    <p class="today-intro">${esc(edition.summary || '')}</p>
    <p class="today-meta">About ${readingMinutes(edition)} min read · ${covered.length} topics</p>
    <div class="today-primary">${currentSection ? `<button type="button" class="primary" data-continue>Continue reading</button><p class="today-continue-story">${esc(currentTopic)} · Section ${currentIndex + 1} of ${sections.length}<br><strong>${esc(currentSection.heading)}</strong></p><button type="button" class="today-read-link" data-read-start>Read from the beginning</button>` : `<button type="button" class="primary" data-read-start>Read today’s briefing</button>`}</div>
    <section class="today-contents" aria-labelledby="today-contents-title"><h2 id="today-contents-title">In this edition</h2><ol>${covered.map(row => {
      const { section, index } = sectionForTopic.get(row.topic_id);
      return `<li><button type="button" data-read-section="${esc(section.id)}"><span class="today-story-number">${String(index + 1).padStart(2, '0')}</span><span><small>${esc(topicById.get(row.topic_id)?.label || row.topic_id)}</small><strong>${esc(section.heading)}</strong></span><span aria-hidden="true">›</span></button></li>`;
    }).join('')}</ol></section>
    ${other.length ? `<details class="today-other-topics"><summary>Other followed topics (${other.length})</summary><ul>${other.map(row => `<li><strong>${esc(topicById.get(row.topic_id)?.label || row.topic_id)}</strong><span>${esc(coverageLabels[row.state] || row.state)}${row.reason ? ` · ${esc(row.reason)}` : ''}</span></li>`).join('')}</ul></details>` : ''}
    ${weekly ? `<a class="today-weekly-link" href="#weekly"><small>The week in perspective · About ${readingMinutes(weekly)} min read</small><strong>${esc(weekly.title)}</strong><span>Open recap →</span></a>` : ''}
  </article>`;
  container.querySelector('[data-read-start]')?.addEventListener('click', () => actions.openReader?.(null));
  container.querySelector('[data-continue]')?.addEventListener('click', () => actions.openReader?.(place));
  container.querySelectorAll('[data-read-section]').forEach(button => button.addEventListener('click', () => actions.openReader?.(button.dataset.readSection)));
}

export function renderEdition(container, edition, topics, actions = {}) {
  if (!edition?.sections?.length) { container.innerHTML = '<p>This edition is unavailable.</p>'; return; }
  const weekly = edition.kind === 'weekly';
  const sections = visibleSections(edition);
  const topicById = new Map(topics.map(topic => [topic.id, topic]));
  const sourceById = new Map((actions.sources || []).map(source => [source.id, source]));
  const reading = readingMinutes(edition);
  const date = weekly ? `Week of ${prettyDate(edition.week_start)}` : prettyDate(edition.local_date);
  const audioReady = edition.audio?.state === 'recorded' && Boolean(edition.audio.url) && edition.audio.revision_id === edition.revision_id && edition.audio.script_hash === edition.script_hash;
  const audioLabel = weekly ? 'Play recap' : 'Play briefing';
  const correction = edition.corrections?.find(item => item.severity === 'material');
  const affectedSection = correction && edition.sections.find(item => correction.section_ids?.includes(item.id));
  const correctionNotice = correction ? `<aside class="correction-notice" aria-label="Material correction"><strong>Correction · ${esc(correctionDate(correction.corrected_at))}</strong><p>${esc(correction.what_changed)} ${esc(correction.why_it_matters)}</p>${affectedSection ? `<button type="button" data-correction-section="${esc(affectedSection.id)}">Go to corrected section: ${esc(affectedSection.heading)}</button>` : ''}${!audioReady ? '<p>Updated audio is unavailable.</p>' : ''}${actions.correctionStopped ? `<p>${esc(actions.correctionStopped)}</p>` : ''}</aside>` : '';
  const contents = sections.map((section, index) => `<li><button type="button" data-content-target="${esc(section.id)}"><span>${String(index + 1).padStart(2, '0')}</span><strong>${esc(section.heading)}</strong></button></li>`).join('');
  container.innerHTML = `<article class="edition" data-edition="${esc(edition.id)}">
    <header class="edition-header">
      <div class="edition-topline"><span>${esc(date)}</span><span class="sample-label">Sample · Fictional content</span></div>
      <p class="edition-type">${weekly ? 'Weekly recap' : 'Daily briefing'} · About ${reading} min read</p>
      <h1>${esc(edition.title)}</h1>
      <p class="edition-overview">${esc(edition.summary || '')}</p>
      ${correctionNotice}
      ${audioReady ? `<div class="edition-actions"><button class="primary edition-play" type="button" data-audio-action="${esc(edition.id)}" data-audio-default="${esc(audioLabel)}">${esc(audioLabel)}</button><span class="edition-reading-status"></span></div>` : ''}
    </header>
    <div class="edition-story">${sections.map((section, index) => {
      const topicId = section.topic_ids?.[0];
      const topic = topicById.get(topicId);
      const dive = (actions.deepDives || []).find(item => item.topic_id === topicId);
      const question = dive?.title || topic?.label;
      return `<section class="edition-section" id="${esc(section.id)}" aria-labelledby="heading-${esc(section.id)}">
        <p class="section-kicker"><span>${String(index + 1).padStart(2, '0')} / ${String(sections.length).padStart(2, '0')}</span>${topic ? `<span>${esc(topic.label)}</span>` : ''}</p>
        <h2 id="heading-${esc(section.id)}" tabindex="-1">${esc(section.heading)}</h2>
        ${String(section.body || '').split(/\n\s*\n/).map(paragraph => `<p>${esc(paragraph)}</p>`).join('')}
        ${topicId && actions.hasDeepDive?.(topicId) ? `<button class="section-explanation" type="button" data-topic="${esc(topicId)}" data-section="${esc(section.id)}">Read explanation: ${esc(question)} <span aria-hidden="true">→</span></button>` : ''}
        ${(section.source_ids || []).length ? `<details class="section-sources"><summary>Sources (${section.source_ids.length})</summary><ul>${section.source_ids.map(id => `<li><button type="button" data-source="${esc(id)}"><strong>${esc(sourceById.get(id)?.publisher || 'Source')}</strong><span>${esc(sourceById.get(id)?.title || id)}</span></button></li>`).join('')}</ul></details>` : ''}
      </section>`;
    }).join('')}</div>
    <footer class="edition-finish"><h2>End of ${weekly ? 'this week’s recap' : 'today’s briefing'}</h2><div class="button-row"><button type="button" data-save-edition>${actions.isSaved?.(edition.id) ? 'Saved edition' : 'Save edition'}</button><a class="button-link" href="${weekly ? '#today' : '#weekly'}">${weekly ? 'Back to Today' : 'Read the weekly recap'}</a></div><p class="edition-end-note">This sample is fictional and does not report current events.</p></footer>
  </article>`;

  if (audioReady) container.querySelector('.edition-play')?.addEventListener('click', () => actions.play?.({
    id: edition.id, title: edition.title, kind: edition.kind, revision_id: edition.revision_id,
    script_hash: edition.script_hash, audio: edition.audio, sections: edition.sections,
  }));
  container.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => actions.openTopic?.(button.dataset.topic, button.dataset.section)));
  container.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => actions.openSource?.(button.dataset.source)));
  container.querySelectorAll('[data-content-target]').forEach(button => button.addEventListener('click', () => actions.continueReading?.(button.dataset.contentTarget)));
  container.querySelector('[data-correction-section]')?.addEventListener('click', event => actions.continueReading?.(event.currentTarget.dataset.correctionSection));
  container.querySelector('[data-save-edition]')?.addEventListener('click', event => { actions.save?.(edition.id, edition.revision_id); event.target.textContent = actions.isSaved?.(edition.id) ? 'Saved edition' : 'Save edition'; });
}
