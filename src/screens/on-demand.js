// Own the On demand tab's request form. Job state and actions remain app-owned.
export function renderOnDemandScreen({
  node, document, content, draft, getRequestState, setRequestError, setRequestBlocked,
  rememberLength, startEpisode, loadEpisodeCards, defaultMode,
}) {
  const page = node('section', null, 'episode-request-page');
  page.append(node('h1', 'Make an episode'));
  page.append(node('p', 'A short listen, with sources.', 'ask-page-intro'));

  const form = node('div', null, 'episode-request-form');
  const label = node('label', 'What would you like to understand?', 'episode-request-prompt-label');
  label.htmlFor = 'episode-request-prompt';
  const prompt = document.createElement('textarea');
  prompt.id = 'episode-request-prompt'; prompt.setAttribute('aria-label', 'What would you like to understand?');
  prompt.placeholder = 'For example, how a floating town could get fresh water';
  prompt.rows = 4; prompt.maxLength = 500; prompt.value = draft.prompt;
  label.append(prompt);

  const promptLimit = node('p', '', 'episode-request-character-count');
  promptLimit.id = 'episode-request-character-count'; promptLimit.hidden = true;
  const updatePromptLimit = () => {
    const atLimit = prompt.value.length >= prompt.maxLength;
    promptLimit.textContent = atLimit ? `${prompt.maxLength}-character limit reached` : '';
    promptLimit.classList.toggle('is-at-limit', atLimit); promptLimit.hidden = !atLimit;
    if (atLimit) prompt.setAttribute('aria-describedby', promptLimit.id);
    else prompt.removeAttribute('aria-describedby');
  };

  const initialState = getRequestState();
  const start = node('button', initialState.blocked ? 'Try later' : 'Make episode', 'primary episode-request-start');
  start.type = 'button'; start.id = 'episode-request-start';
  start.setAttribute('aria-label', initialState.blocked ? 'Try again later' : 'Make episode');
  start.disabled = initialState.busy || initialState.blocked || !prompt.value.trim();
  const promptError = node('p', initialState.error, 'episode-request-error'); promptError.setAttribute('role', 'status');
  prompt.addEventListener('input', () => {
    draft.prompt = prompt.value;
    if (getRequestState().blocked) {
      setRequestBlocked(false); start.textContent = 'Make episode'; start.setAttribute('aria-label', 'Make episode');
    }
    setRequestError(''); promptError.textContent = '';
    prompt.style.height = 'auto'; prompt.style.height = `${prompt.scrollHeight}px`;
    const state = getRequestState(); start.disabled = state.busy || state.blocked || !prompt.value.trim();
    updatePromptLimit();
  });
  start.addEventListener('click', () => {
    draft.prompt = prompt.value;
    if (!prompt.value.trim()) { promptError.textContent = 'Add a topic or question to make an episode.'; prompt.focus(); return; }
    setRequestError(''); promptError.textContent = ''; void startEpisode(defaultMode);
  });
  form.append(label, promptLimit, promptError);

  const lengths = node('fieldset', null, 'episode-request-group on-demand-length-group');
  lengths.append(node('legend', 'Episode length'));
  const choices = node('div', null, 'episode-request-chips');
  for (const minutes of [3, 5, 10]) {
    const choice = node('button', `${minutes} min`, 'episode-request-chip'); choice.type = 'button';
    choice.setAttribute('aria-pressed', String(minutes === draft.minutes));
    choice.addEventListener('click', () => {
      rememberLength(minutes);
      choices.querySelectorAll('button').forEach(chip => chip.setAttribute('aria-pressed', String(chip === choice)));
    });
    choices.append(choice);
  }
  lengths.append(choices);
  const preferences = node('details', null, 'episode-request-preferences');
  preferences.append(node('summary', 'Optional preferences'));
  preferences.append(node('p', 'You can choose your narrator in Settings.', 'episode-request-preferences-copy'));

  form.append(lengths, preferences, start); page.append(form);
  const episodes = node('section', null, 'ask-episodes'); episodes.append(node('h2', 'Your episodes'));
  const cards = node('div', null, 'ask-episode-cards'); cards.setAttribute('aria-label', 'Your episodes');
  episodes.append(cards); page.append(episodes);
  content.append(page); void loadEpisodeCards(cards, null, prompt);
  updatePromptLimit();
  return page;
}
