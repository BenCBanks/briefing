// The first run and interest confirmation screens are rendered here; app state
// and navigation stay in app.js and enter through the callbacks below.
export function renderInterestDescribe({ content, node, draft, profile, dailyTime, onDailyTimeChange,
  interpret, topics, minutes, errorMessage, onBuild }) {
  document.querySelector('[data-page-title]').textContent = 'Interests';
  content.classList.add('first-run-describe');
  content.append(node('h1', 'What do you want to follow?', 'describe-title'));
  content.append(node('p', 'Say it like you would tell a friend. Topics, what to skip, how often.', 'interest-intro'));
  const delivery = node('div', null, 'interest-delivery-note');
  delivery.append(
    node('p', 'Your daily 5-minute briefing is ready by 6 am ET.', 'interest-delivery-daily'),
    node('p', 'Ask for an episode on anything, any time.', 'interest-delivery-episodes'),
  );
  content.append(delivery);
  const form = node('form', null, 'interest-describe-form');
  form.id = 'interest-describe-form';
  const field = node('div', null, 'interest-word-field');
  const label = node('label', 'Add any topic or describe what matters to you', 'interest-text-label'); label.htmlFor = 'interest-words';
  const words = node('textarea'); words.id = 'interest-words'; words.name = 'words'; words.maxLength = 2000; words.rows = 6;
  words.placeholder = 'For example: I follow local transit and water policy. Skip celebrity coverage.';
  words.setAttribute('aria-label', 'Add any topic or describe what matters to you');
  words.setAttribute('aria-describedby', 'interest-guidance interest-counter');
  words.value = draft?.profile.words || profile?.words || '';
  field.append(label, words);
  form.append(field);
  const timeField = node('div', null, 'interest-time-field');
  const dailyTimeLabel = node('label', 'Preferred time', 'interest-time-label'); dailyTimeLabel.htmlFor = 'interest-daily-time';
  const dailyTimeInput = node('input'); dailyTimeInput.id = 'interest-daily-time'; dailyTimeInput.name = 'daily-time'; dailyTimeInput.type = 'time'; dailyTimeInput.value = dailyTime;
  dailyTimeInput.setAttribute('aria-describedby', 'interest-time-note');
  dailyTimeInput.addEventListener('change', () => onDailyTimeChange(dailyTimeInput.value));
  const timeNote = node('p', 'Your briefing is ready by 6 am ET. Your preferred time is saved on this device.', 'interest-time-note'); timeNote.id = 'interest-time-note';
  timeField.append(dailyTimeLabel, dailyTimeInput, timeNote); form.append(timeField);
  const guidance = node('p', '', 'interest-guidance'); guidance.id = 'interest-guidance'; guidance.setAttribute('role', 'status'); guidance.setAttribute('aria-live', 'polite');
  form.append(guidance);
  const counter = node('p', '', 'interest-counter'); counter.id = 'interest-counter';
  form.append(counter);
  const examples = node('div', null, 'interest-examples');
  examples.append(node('p', 'Try a suggestion, then edit it in your own words.'));
  const examplesRow = node('div', null, 'interest-example-chips');
  const sampleWords = [
    ['Payments & banking', 'I follow payments and banking, especially real-time payment rules and savings rates.'],
    ['Tech & privacy', 'I follow technology and privacy, with practical changes explained clearly.'],
    ['Premier League', 'I follow Premier League matches and news on weekends.'],
  ];
  sampleWords.forEach(([title, sentence]) => {
    const chip = node('button', title, 'interest-example-chip'); chip.type = 'button';
    chip.addEventListener('click', () => { words.value = `${words.value.trim()}${words.value.trim() ? ' ' : ''}${sentence}`; words.dispatchEvent(new Event('input')); words.focus(); });
    examplesRow.append(chip);
  });
  examples.append(examplesRow); form.append(examples);
  const starterPreview = node('section', null, 'interest-starter-preview');
  starterPreview.setAttribute('aria-label', 'Starter profile preview');
  starterPreview.append(node('h2', 'Starter profile preview'));
  const starterSummary = node('p', 'Your topics will appear here as you type.', 'interest-starter-summary');
  starterSummary.setAttribute('role', 'status'); starterSummary.setAttribute('aria-live', 'polite');
  starterPreview.append(starterSummary);
  const starterTopics = node('ul', null, 'interest-starter-topics');
  starterPreview.append(starterTopics);
  const starterNote = node('p', '', 'interest-starter-note'); starterPreview.append(starterNote);
  const clarifyTopic = node('button', 'Add a little detail', 'interest-starter-clarify'); clarifyTopic.type = 'button'; clarifyTopic.hidden = true;
  clarifyTopic.addEventListener('click', () => { words.focus(); words.setSelectionRange(words.value.length, words.value.length); });
  starterPreview.append(clarifyTopic); form.append(starterPreview);
  const error = node('p', '', 'error interest-form-error'); error.setAttribute('role', 'alert'); form.append(error);
  error.textContent = errorMessage || '';
  const actions = node('div', null, 'interest-form-actions');
  const stickyActions = node('div', null, 'interest-sticky-actions');
  const interpretButton = node('button', 'Build my briefing', 'primary'); interpretButton.type = 'submit'; interpretButton.setAttribute('form', 'interest-describe-form');
  actions.append(interpretButton);
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (typeof SpeechRecognition === 'function') {
    const dictate = node('button', null, 'interest-dictate-icon'); dictate.type = 'button'; dictate.setAttribute('aria-label', 'Dictate'); dictate.title = 'Dictate';
    dictate.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8"/></svg>';
    dictate.addEventListener('click', () => { const recognition = new SpeechRecognition(); recognition.lang = 'en-US'; recognition.interimResults = false; recognition.onresult = event => { const spoken = event.results?.[0]?.[0]?.transcript || ''; words.value = `${words.value.trim()}${words.value.trim() ? ' ' : ''}${spoken}`; words.dispatchEvent(new Event('input')); }; recognition.onerror = () => { guidance.textContent = 'Dictation could not start. You can type instead.'; }; recognition.start(); });
    actions.append(dictate);
  }
  stickyActions.append(actions); content.append(stickyActions);
  const updateInputState = () => {
    const length = words.value.length;
    counter.textContent = `${length.toLocaleString()} / 2,000 characters`;
    counter.hidden = length < 1800;
    words.rows = Math.min(10, Math.max(6, (words.value.match(/\n/g) || []).length + 6));
    guidance.textContent = length > 2000 ? 'Please shorten this to 2,000 characters or fewer.' : '';
    const starter = interpret(words.value, { topics, minutes });
    starterTopics.replaceChildren(); starterNote.textContent = ''; clarifyTopic.hidden = true;
    const count = starter.profile.topics.length;
    starterSummary.textContent = !words.value.trim() ? 'Your topics will appear here as you type.' : count
      ? `${count} topic${count === 1 ? '' : 's'} in your starter profile. Repeated mentions are combined.`
      : 'We have not picked out a topic yet. Try naming a field, place, team, or question.';
    starter.profile.topics.forEach(topic => {
      const row = node('li', null, 'interest-starter-topic');
      row.append(node('strong', topic.label), node('span', topic.scope)); starterTopics.append(row);
    });
    if (starter.profile.muted.length) starterNote.textContent = `Not covering: ${starter.profile.muted.map(item => item.label).join(', ')}.`;
    const unsupported = starter.profile.topics.find(topic => topic.id.startsWith('custom-'));
    if (unsupported) {
      starterNote.textContent = `${starterNote.textContent ? starterNote.textContent + ' ' : ''}${unsupported.label} is not in the sample edition yet. Add a little context if you mean a specific place, league, or field.`;
      clarifyTopic.hidden = false;
    }
  };
  words.addEventListener('input', () => { error.textContent = ''; updateInputState(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!words.value.trim()) { guidance.textContent = 'Add a few words first'; words.focus(); return; }
    if (words.value.trim().split(/\s+/).length < 3) { guidance.textContent = 'Tell me a little more, for example a topic and how often.'; words.focus(); return; }
    if (words.value.length > 2000) { updateInputState(); words.focus(); return; }
    error.textContent = '';
    onBuild(words.value);
  });
  content.append(form);
  updateInputState();
}

export function renderInterestConfirm({ content, node, draft, busy, errorMessage, flags, renderTopbar,
  onCancel, onRetry, onBasic, onUpdate, onEdit, onSave }) {
  content.replaceChildren();
  document.querySelector('[data-page-title]').textContent = 'Interests';
  renderTopbar('2 of 2');
  content.append(node('p', 'YOUR BRIEFING', 'eyebrow'), node('h1', 'What I heard'));
  content.append(node('p', 'These are suggestions from your words. Change anything, or edit the words and start again.', 'interest-intro'));
  if (busy) {
    const progress = node('div', 'Interpreting…', 'interest-progress');
    progress.setAttribute('role', 'status'); progress.setAttribute('aria-live', 'polite');
    const cancel = node('button', 'Cancel', 'interest-cancel'); cancel.type = 'button'; cancel.addEventListener('click', onCancel);
    progress.append(cancel); content.append(progress);
  }
  if (errorMessage) {
    const notice = node('aside', null, 'interest-interpreter-error'); notice.setAttribute('role', 'alert');
    notice.append(node('p', errorMessage));
    const retry = node('button', 'Retry', 'interest-retry'); retry.type = 'button'; retry.addEventListener('click', onRetry);
    const basic = node('button', 'Use basic matching', 'interest-basic'); basic.type = 'button'; basic.addEventListener('click', onBasic);
    notice.append(retry, basic); content.append(notice);
  }
  if (flags?.length) {
    const note = node('aside', null, 'interest-interpreter-flags'); note.setAttribute('role', 'status');
    note.append(node('p', `Interpretation note: ${flags.join('; ')}`)); content.append(note);
  }
  content.append(node('blockquote', draft.profile.words || 'No words entered yet.', 'interest-original-words'));
  const list = node('div', null, 'interest-topic-list');
  for (const topic of draft.profile.topics) {
    const card = node('section', null, 'interest-topic');
    const title = node('div', null, 'interest-topic-title');
    const heading = node('div', null, 'interest-topic-heading'); heading.append(node('h2', topic.label));
    const remove = node('button', 'Remove', 'interest-remove'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${topic.label}`);
    remove.addEventListener('click', () => onUpdate(() => { draft.profile.topics = draft.profile.topics.filter(item => item.id !== topic.id); }));
    title.append(heading, remove);
    card.append(title, node('p', `You said: “${topic.sourcePhrase}”`, 'interest-source-phrase'), node('p', topic.scope, 'interest-scope'));
    const include = !(topic.exclude.length && !topic.mustFollow);
    const choices = node('div', null, 'interest-include-choices');
    for (const [value, label] of [[true, 'Include'], [false, 'Skip']]) {
      const button = node('button', label, 'interest-choice'); button.type = 'button'; button.setAttribute('aria-pressed', String(include === value));
      button.addEventListener('click', () => onUpdate(() => {
        topic.include = value ? [topic.sourcePhrase] : []; topic.exclude = value ? [] : [topic.sourcePhrase];
        if (!value) topic.mustFollow = false;
      })); choices.append(button);
    }
    card.append(choices);
    const depths = node('div', null, 'interest-depth-choices'); depths.setAttribute('role', 'group'); depths.setAttribute('aria-label', `${topic.label} depth`);
    for (const [value, label] of [['headline', 'Headlines'], ['standard', 'Standard'], ['deeper', 'Deeper']]) {
      const button = node('button', label, 'interest-depth-chip'); button.type = 'button'; button.setAttribute('aria-pressed', String(topic.depth === value));
      button.addEventListener('click', () => onUpdate(() => { topic.depth = value; }, false)); depths.append(button);
    }
    card.append(depths);
    card.append(node('p', `Cadence · ${topic.cadence === 'change' ? 'Only when it changes' : topic.cadence === 'weekends' ? 'Weekends' : topic.cadence === 'weekly' ? 'Weekly' : 'Every day'}`, 'interest-cadence'));
    if (topic.mustFollow) card.append(node('p', 'Must follow', 'interest-must-follow'));
    list.append(card);
  }
  content.append(list);
  if (draft.profile.muted.length) {
    const muted = node('section', null, 'interest-muted-list'); muted.append(node('h2', 'Muted topics'));
    for (const item of draft.profile.muted) {
      const row = node('div', null, 'interest-muted-row'); row.append(node('span', item.label));
      const restore = node('button', 'Include', 'interest-choice'); restore.type = 'button';
      restore.addEventListener('click', () => onUpdate(() => {
        const topic = draft.profile.topics.find(candidate => candidate.label === item.label);
        if (topic) { topic.exclude = []; topic.include = [topic.sourcePhrase]; }
      }));
      row.append(restore); muted.append(row);
    }
    content.append(muted);
  }
  if (draft.question) {
    const question = node('aside', null, 'interest-question'); question.append(node('h2', 'One question'), node('p', draft.question)); content.append(question);
  }
  const edit = node('button', 'Edit words', 'interest-edit-words'); edit.type = 'button'; edit.addEventListener('click', onEdit); content.append(edit);
  const save = node('button', 'Save interests', 'primary interest-save'); save.type = 'button'; save.addEventListener('click', () => onSave(draft.profile)); content.append(save);
}

export function renderInterestBuild({ content, node, profile, question, buildChipLabel, onRemoveTopic, onEditWords, onGoToday, scheduleTimeout }) {
  content.replaceChildren();
  document.querySelector('[data-page-title]').textContent = 'Building';
  content.classList.add('interest-build-screen');
  content.append(node('h1', 'Building your briefing'));
  const from = node('section', null, 'build-profile'); from.append(node('h2', 'From your words'));
  if (profile?.words) from.append(node('p', profile.words, 'build-original-words'));
  const chips = node('div', null, 'build-interest-chips');
  const includedChips = [];
  const excludedChips = [];
  (profile?.topics || []).forEach(topic => {
    const chip = node('span', null, 'build-interest-chip'); chip.append(node('span', buildChipLabel(topic)));
    const remove = node('button', '×', 'build-chip-remove'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${topic.label}`);
    remove.addEventListener('click', () => onRemoveTopic(topic.id)); chip.append(remove); includedChips.push(chip);
  });
  const mutedByKey = new Map();
  for (const item of profile?.muted || []) {
    const label = String(item?.label || '').trim();
    if (label && !mutedByKey.has(label.toLocaleLowerCase())) mutedByKey.set(label.toLocaleLowerCase(), label);
  }
  [...mutedByKey.values()].forEach(label => {
    const chip = node('span', null, 'build-interest-chip is-excluded'); chip.append(node('span', label)); excludedChips.push(chip);
  });
  includedChips.forEach(chip => chips.append(chip));
  if (excludedChips.length) {
    const excluded = node('section', null, 'build-excluded-group'); excluded.append(node('h3', 'Not covering'));
    const row = node('div', null, 'build-interest-chips'); excludedChips.forEach(chip => row.append(chip)); excluded.append(row); chips.append(excluded);
  }
  from.append(chips);
  if (question) from.append(node('p', question, 'build-clarifying-question'));
  const edit = node('button', 'Edit my words', 'build-edit-words'); edit.type = 'button'; edit.addEventListener('click', onEditWords);
  from.append(edit); content.append(from);
  const progress = node('ol', null, 'build-progress'); progress.setAttribute('aria-label', 'Briefing progress');
  ['Understanding your words', 'Finding today’s stories', 'Writing your briefing'].forEach((label, index) => {
    const row = node('li', label, 'build-progress-step'); row.dataset.step = String(index); row.setAttribute('aria-current', index === 0 ? 'step' : 'false'); progress.append(row);
  });
  progress.setAttribute('role', 'status'); progress.setAttribute('aria-live', 'polite'); content.append(progress);
  scheduleTimeout(() => {
    if (location.hash !== '#interests/build') return;
    const note = node('p', 'Still working. It will be on Today when ready.', 'build-still-working');
    const go = node('button', 'Go to Today', 'button-link'); go.type = 'button'; go.addEventListener('click', onGoToday); note.append(go); content.append(note);
  });
}
