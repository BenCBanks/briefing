// Own the Topics tab's page structure; the app supplies profile state and actions.
export function renderTopicsScreen({
  content,
  document,
  node,
  link,
  renderTopics,
  profile,
  profileTopics,
  deepDives,
  selectedIds,
  dailyCoverage,
  actions,
}) {
  const body = node('div', null, 'topics-page');
  content.append(body);
  renderTopics(body, profileTopics, deepDives, selectedIds, dailyCoverage, actions);

  const mutedLabels = new Set((profile?.muted || []).map(item => item.label.toLocaleLowerCase()));
  const coveredLabels = profileTopics.filter(topic => !mutedLabels.has(topic.label.toLocaleLowerCase()))
    .map(topic => topic.label);
  const dailySummary = node('p', `Your daily briefing covers: ${coveredLabels.join(', ') || 'no topics yet'}.`, 'topics-daily-summary');
  body.querySelector('.topics-heading')?.after(dailySummary);
  const settings = link('⚙', '#preferences', 'settings-link topics-settings');
  settings.setAttribute('aria-label', 'Settings');
  settings.title = 'Settings';
  body.querySelector('.topics-heading')?.append(settings);
  return body;
}
