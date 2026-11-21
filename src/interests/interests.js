const el = (tag, text = '', className = '') => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
};

const optionLabel = value => ({ headline: 'Headline', standard: 'Standard', deeper: 'Deeper', daily: 'Daily', change: 'When it changes', weekly: 'Weekly', weekends: 'Weekends' }[value] || value);

function selectField(labelText, value, options, onChange) {
  const label = el('label', '', 'interest-field'); label.append(el('span', labelText));
  const select = el('select');
  for (const option of options) {
    const item = el('option', optionLabel(option)); item.value = option; item.selected = value === option; select.append(item);
  }
  select.addEventListener('change', () => onChange(select.value)); label.append(select); return label;
}

export function renderInterests(container, profile, suggestions, catalog, actions) {
  container.replaceChildren();
  container.classList.add('interests-page');
  container.append(el('p', 'YOUR INTERESTS', 'eyebrow'), el('h1', 'What should we follow?'),
    el('p', 'Use your own words. You can change them whenever your interests shift.', 'interests-intro'));

  const wordsForm = el('form', '', 'interest-words-form');
  const wordsLabel = el('label', '', 'interest-words-label'); wordsLabel.append(el('span', 'Write it the way you would tell a friend'));
  const words = el('textarea'); words.name = 'words'; words.rows = 4; words.maxLength = 2000; words.value = profile.words;
  words.setAttribute('aria-label', 'Your interests in your own words'); wordsLabel.append(words);
  const interpret = el('button', 'Update my interests', 'primary'); interpret.type = 'submit';
  const interpretStatus = el('p', '', 'interest-interpret-status'); interpretStatus.setAttribute('role', 'status'); interpretStatus.setAttribute('aria-live', 'polite');
  wordsForm.append(wordsLabel, interpret, interpretStatus);
  wordsForm.addEventListener('submit', async event => {
    event.preventDefault();
    interpret.disabled = true; interpretStatus.textContent = 'Updating your interests…';
    try { await actions.interpret(words.value); }
    catch { interpretStatus.textContent = 'Could not update these interests. Please try again.'; interpret.disabled = false; }
  });
  container.append(wordsForm);

  const learned = el('section', '', 'interest-learned');
  learned.append(el('div', '', 'interest-section-heading'));
  learned.querySelector('.interest-section-heading').append(el('h2', 'Here is what I heard'), el('p', 'Based on your words and the changes you choose.'));
  const activeTopics = profile.topics.filter(topic => !profile.muted.some(item => item.label === topic.label));
  if (!activeTopics.length) {
    learned.append(el('p', 'No topics are active yet. Add a subject in your own words above.'));
  }
  const list = el('ul', '', 'interest-topic-list');
  for (const topic of activeTopics) {
    const row = el('li', '', 'interest-topic-row');
    row.append(el('h3', topic.label), el('p', topic.scope, 'interest-topic-scope'),
      el('p', `From your words: “${topic.sourcePhrase}”`, 'interest-source-phrase'));
    if (topic.include.length) row.append(el('p', `Includes: ${topic.include.join(' · ')}`, 'interest-terms'));
    if (topic.exclude.length) row.append(el('p', `Skips: ${topic.exclude.join(' · ')}`, 'interest-terms'));
    const controls = el('div', '', 'interest-controls');
    controls.append(selectField('Depth', topic.depth, ['headline', 'standard', 'deeper'], value => actions.updateTopic(topic.id, { depth: value })),
      selectField('Cadence', topic.cadence, ['daily', 'change', 'weekly', 'weekends'], value => actions.updateTopic(topic.id, { cadence: value })));
    const remove = el('button', 'Remove topic', 'interest-remove'); remove.type = 'button'; remove.addEventListener('click', () => actions.removeTopic(topic.id)); controls.append(remove);
    row.append(controls); list.append(row);
  }
  learned.append(list);
  if (profile.muted.length) {
    const muted = el('section', '', 'interest-muted'); muted.append(el('h2', 'Muted topics'), el('p', 'These stay muted until you add them back yourself.'));
    const mutedList = el('ul');
    profile.muted.forEach(item => {
      const row = el('li'); row.append(el('span', item.label));
      const restore = el('button', 'Add back', 'interest-remove'); restore.type = 'button'; restore.addEventListener('click', () => actions.unmute(item.label)); row.append(restore); mutedList.append(row);
    });
    muted.append(mutedList); learned.append(muted);
  }
  container.append(learned);

  const suggestionSection = el('section', '', 'interest-suggestions');
  suggestionSection.append(el('h2', 'Possible adjustments'), el('p', 'Suggestions come from what you choose and how you use the sample. Nothing changes until you accept it.'));
  if (!suggestions.length) suggestionSection.append(el('p', 'No suggestions yet. Your choices stay as they are.'));
  const suggestionList = el('ul', '', 'suggestion-list');
  for (const suggestion of suggestions) {
    const row = el('li', '', 'suggestion-row');
    row.append(el('h3', suggestion.text), el('p', suggestion.reason, 'suggestion-reason'));
    const actionsRow = el('div', '', 'suggestion-actions');
    const accept = el('button', 'Accept', 'suggestion-accept'); accept.type = 'button'; accept.addEventListener('click', () => actions.accept(suggestion));
    const later = el('button', 'Not now', 'suggestion-later'); later.type = 'button'; later.addEventListener('click', () => actions.snooze(suggestion));
    actionsRow.append(accept, later); row.append(actionsRow); suggestionList.append(row);
  }
  suggestionSection.append(suggestionList); container.append(suggestionSection);
  const learnedTools = el('section', '', 'interest-data-tools');
  learnedTools.append(el('h2', 'Learning data'), el('p', 'Raw reading signals and suggestions stay on this device. A bounded topic weight can be synced so the scheduled briefing can balance topics.'));
  const clear = el('button', 'Clear what has been learned', 'interest-clear'); clear.type = 'button'; clear.addEventListener('click', actions.clearSignals);
  learnedTools.append(clear); container.append(learnedTools);
  container.append(el('p', `Daily edition target: about ${profile.minutes} minutes. Changes affect future briefings.`, 'local-note'));
}
