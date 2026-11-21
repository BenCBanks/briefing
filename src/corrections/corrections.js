const el = (tag, className = '', text = '') => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== '') node.textContent = text;
  return node;
};

function correctionDate(item) {
  const date = new Date(item.corrected_at || item.date || '');
  if (Number.isNaN(date.getTime())) return { label: 'Date not listed', value: '' };
  return {
    label: new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date),
    value: date.toISOString(),
  };
}

export function renderCorrections(container, items = [], { loading = false, error = '', onRetry } = {}) {
  container.replaceChildren();
  const page = el('section', 'corrections-page');
  const back = el('a', 'corrections-back', '‹ Back to Today'); back.href = '#today'; page.append(back);
  page.append(el('h1', '', 'Corrections'));
  page.append(el('p', 'corrections-intro', 'When we correct a story or episode, we record what changed and why.'));
  if (loading) {
    const status = el('div', 'corrections-loading');
    status.setAttribute('role', 'status'); status.setAttribute('aria-label', 'Loading corrections'); status.setAttribute('aria-busy', 'true');
    for (let index = 0; index < 3; index++) status.append(el('span', 'corrections-skeleton'));
    page.append(status); container.append(page); return;
  }
  if (error) {
    const message = el('p', 'corrections-error', error); message.setAttribute('role', 'alert'); page.append(message);
    const retry = el('button', 'corrections-retry', 'Retry'); retry.type = 'button'; retry.addEventListener('click', () => onRetry?.());
    page.append(retry); container.append(page); return;
  }
  const ordered = [...items].sort((a, b) => String(b.corrected_at || b.date || '').localeCompare(String(a.corrected_at || a.date || '')) || String(a.id || '').localeCompare(String(b.id || '')));
  if (!ordered.length) {
    page.append(el('h2', 'corrections-empty-title', 'No corrections yet'));
    page.append(el('p', 'corrections-empty-copy', 'If we correct a story or episode, the date and the changes will appear here.'));
    container.append(page); return;
  }
  const list = el('ol', 'corrections-list');
  for (const item of ordered) {
    const entry = el('li', 'correction-entry');
    const article = el('article', 'correction-card');
    const header = el('header', 'correction-card-header');
    const date = correctionDate(item);
    const time = el('time', 'correction-date', date.label);
    if (date.value) time.dateTime = date.value;
    header.append(time);
    const updated = el('a', 'correction-updated', 'Corrected');
    updated.href = item.target_hash || '#today';
    updated.setAttribute('aria-label', `Corrected: open ${item.title || (item.target_type === 'episode' ? 'corrected episode' : 'corrected story')}`);
    header.append(updated);
    article.append(header);
    article.append(el('h2', 'correction-title', item.title || (item.target_type === 'episode' ? 'Episode' : 'Daily story')));
    article.append(el('p', 'correction-summary', item.what_changed || 'The wording was corrected.'));
    if (item.why_it_matters) article.append(el('p', 'correction-reason', item.why_it_matters));
    const details = el('details', 'correction-change-details');
    details.append(el('summary', '', 'See what changed'));
    if (item.before && item.after) {
      const diff = el('div', 'correction-diff');
      const before = el('p', 'correction-before'); before.append(el('strong', '', 'Before'), el('del', '', item.before));
      const after = el('p', 'correction-after'); after.append(el('strong', '', 'Now'), el('ins', '', item.after));
      diff.append(before, after); details.append(diff);
    } else details.append(el('p', 'correction-diff-unavailable', 'The earlier wording is not available in this copy.'));
    article.append(details); entry.append(article); list.append(entry);
  }
  page.append(list); container.append(page);
}
