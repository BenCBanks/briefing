const esc = (value = '') => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const prettyDate = value => {
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
};
const coverageLabels = { covered: 'Update', update: 'Update', context: 'Context', quiet: 'Quiet today', limited: 'Coverage limited' };
const correctionDate = value => {
  const date = new Date(String(value || '').slice(0, 10) + 'T12:00:00Z');
  return Number.isNaN(date.getTime()) ? 'Material' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', timeZone: 'UTC' }).format(date);
};

export function renderEdition(container, edition, topics, actions = {}) {
  if (!edition?.sections?.length) { container.innerHTML = '<p>This edition is unavailable.</p>'; return; }
  const weekly = edition.kind === 'weekly';
  const topicById = new Map(topics.map(topic => [topic.id, topic]));
  const sourceById = new Map((actions.sources || []).map(source => [source.id, source]));
  const readingMinutes = Math.max(1, Math.ceil(edition.sections.reduce((count, section) => count + section.body.split(/\s+/).length, 0) / 225));
  const date = weekly ? `Week of ${prettyDate(edition.week_start)}` : prettyDate(edition.local_date);
  const audioReady = edition.audio?.state === 'recorded' && Boolean(edition.audio.url) && edition.audio.revision_id === edition.revision_id && edition.audio.script_hash === edition.script_hash;
  const audioLabel = weekly ? 'Play recap' : 'Play briefing';
  const correction = edition.corrections?.find(item => item.severity === 'material');
  const affectedSection = correction && edition.sections.find(item => correction.section_ids?.includes(item.id));
  const correctionNotice = correction ? `<aside class="correction-notice" aria-label="Material correction"><strong>Correction · ${esc(correctionDate(correction.corrected_at))}</strong><p>${esc(correction.what_changed)} ${esc(correction.why_it_matters)}</p>${affectedSection ? `<button type="button" data-correction-section="${esc(affectedSection.id)}">Go to corrected section: ${esc(affectedSection.heading)}</button>` : ''}${!audioReady ? '<p>Updated audio is unavailable.</p>' : ''}${actions.correctionStopped ? `<p>${esc(actions.correctionStopped)}</p>` : ''}</aside>` : '';
  container.innerHTML = `<article class="edition" data-edition="${esc(edition.id)}">
    <header class="edition-header">
      <div class="edition-topline"><span>${esc(date)}</span><span class="sample-label">Sample · Fictional content</span></div>
      <p class="edition-type">${weekly ? 'Your week in perspective' : 'Today’s briefing'}</p>
      <h1>${esc(edition.title)}</h1>
      <p class="edition-meta">About ${readingMinutes} min read</p>
      ${correctionNotice}
      <div class="edition-actions">${audioReady ? `<button class="primary edition-play" type="button" data-audio-action="${esc(edition.id)}" data-audio-default="${esc(audioLabel)}">${esc(audioLabel)}</button>` : correction ? '' : '<span class="audio-pending">Audio pending approval</span>'}<span class="edition-reading-status"></span></div>
      <p class="edition-overview">${esc(edition.summary || '')}</p>
      <button class="coverage-open" type="button">${edition.coverage.length} topics · View coverage <span aria-hidden="true">›</span></button>
    </header>
    <div class="edition-story">${edition.sections.map(section => `<section class="edition-section" id="${esc(section.id)}" aria-labelledby="heading-${esc(section.id)}">
      ${section.topic_ids?.length ? `<p class="section-kicker">${section.topic_ids.map(id => esc(topicById.get(id)?.label || id)).join(' · ')}</p>` : ''}
      <h2 id="heading-${esc(section.id)}" tabindex="-1">${esc(section.heading)}</h2>
      ${String(section.body || '').split(/\n\s*\n/).map(paragraph => `<p>${esc(paragraph)}</p>`).join('')}
      <div class="section-references">${(section.source_ids || []).map(id => `<button type="button" data-source="${esc(id)}">Source: ${esc(sourceById.get(id)?.title || id)}</button>`).join('')}${(section.topic_ids || []).filter(id => actions.hasDeepDive?.(id)).map(id => `<button type="button" data-topic="${esc(id)}" data-section="${esc(section.id)}">Read deeper: ${esc(topicById.get(id)?.label || id)}</button>`).join('')}</div>
    </section>`).join('')}</div>
    <footer class="edition-finish"><h2>You’re up to date for this ${weekly ? 'week' : 'edition'}.</h2><div class="button-row"><button type="button" data-finish ${actions.isFinished?.(edition.id, edition.revision_id) ? 'disabled' : ''}>${actions.isFinished?.(edition.id, edition.revision_id) ? 'Finished' : 'Mark finished'}</button><button type="button" data-save-edition>${actions.isSaved?.(edition.id) ? 'Saved briefing' : 'Save briefing'}</button></div><p class="edition-end-note">This sample is fictional and does not report current events.</p></footer>
  </article>`;

  const place = actions.readingPlace?.(edition.id, edition.revision_id);
  const section = edition.sections.find(item => item.id === place);
  const reading = container.querySelector('.edition-reading-status');
  if (section) {
    const resume = document.createElement('button'); resume.type = 'button'; resume.className = 'edition-continue';
    resume.textContent = 'Continue reading'; resume.addEventListener('click', () => actions.continueReading?.(section.id));
    reading.replaceWith(resume);
  } else reading.remove();

  const coverageButton = container.querySelector('.coverage-open');
  coverageButton.addEventListener('click', () => {
    const dialog = document.createElement('dialog'); dialog.className = 'coverage-dialog';
    dialog.innerHTML = `<div class="coverage-sheet"><div class="sheet-heading"><h2>Coverage in this edition</h2><button type="button" data-close aria-label="Close coverage">Close</button></div><p>Every selected subject, including quiet or limited coverage.</p><ul>${edition.coverage.map(row => `<li><strong>${esc(topicById.get(row.topic_id)?.label || row.topic_id)}</strong><span>${esc(coverageLabels[row.state] || row.state)}</span><p>${esc(row.reason || '')}</p><button type="button" data-topic="${esc(row.topic_id)}">Explore ${esc(topicById.get(row.topic_id)?.label || row.topic_id)}</button></li>`).join('')}</ul></div>`;
    dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
    dialog.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => { dialog.close(); actions.openTopic?.(button.dataset.topic); }));
    dialog.addEventListener('close', () => dialog.remove());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    container.append(dialog); dialog.showModal();
  });
  container.querySelector('.edition-play')?.addEventListener('click', () => actions.play?.({
    id: edition.id, title: edition.title, kind: edition.kind, revision_id: edition.revision_id,
    script_hash: edition.script_hash, audio: edition.audio, sections: edition.sections,
  }));
  container.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => actions.openTopic?.(button.dataset.topic, button.dataset.section)));
  container.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => actions.openSource?.(button.dataset.source)));
  container.querySelector('[data-correction-section]')?.addEventListener('click', event => actions.continueReading?.(event.currentTarget.dataset.correctionSection));
  container.querySelector('[data-save-edition]').addEventListener('click', event => { actions.save?.(edition.id, edition.revision_id); event.target.textContent = actions.isSaved?.(edition.id) ? 'Saved briefing' : 'Save briefing'; });
  container.querySelector('[data-finish]').addEventListener('click', event => { actions.finish?.(edition.id, edition.revision_id); event.target.textContent = 'Finished'; event.target.disabled = true; });
}
