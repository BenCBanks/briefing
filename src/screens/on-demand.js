// Own the On demand tab's request form. Job state and actions remain app-owned.
export function renderOnDemandScreen({
  node,
  document,
  content,
  draft,
  getRequestState,
  setRequestError,
  setRequestBlocked,
  rememberLength,
  startEpisode,
  loadEpisodeCards,
  defaultMode,
}) {
  const page = node('section', null, 'episode-request-page');
  page.append(node('h1', 'Brief me on anything'));
  page.append(node('p', 'Any topic, any time. You choose the length. Your episode includes a transcript and audio when recording is ready.', 'ask-page-intro'));
  const promptBox = node('div', null, 'ask-prompt-box');
  const prompt = document.createElement('textarea');
  prompt.id = 'episode-request-prompt'; prompt.setAttribute('aria-label', 'What do you want to understand?');
  prompt.placeholder = 'e.g. U.S. payments regulation this month'; prompt.rows = 2; prompt.maxLength = 500; prompt.value = draft.prompt;
  const promptLimit = node('p', '', 'episode-request-character-count');
  promptLimit.id = 'episode-request-character-count'; promptLimit.hidden = true;
  const updatePromptLimit = () => {
    const atLimit = prompt.value.length >= prompt.maxLength;
    promptLimit.textContent = atLimit ? `${prompt.maxLength}-character limit reached` : '';
    promptLimit.classList.toggle('is-at-limit', atLimit);
    promptLimit.hidden = !atLimit;
    if (atLimit) prompt.setAttribute('aria-describedby', promptLimit.id);
    else prompt.removeAttribute('aria-describedby');
  };
  const suggestions = node('section', null, 'episode-topic-suggestions');
  suggestions.hidden = true; suggestions.setAttribute('aria-label', 'Sharper topic suggestions');
  const paintSuggestions = () => {
    const options = episodeTopicSuggestions(prompt.value);
    suggestions.replaceChildren();
    if (!options.length) { suggestions.hidden = true; return; }
    suggestions.hidden = false;
    suggestions.append(node('p', 'Make this broad topic more specific', 'episode-topic-suggestions-label'));
    const chips = node('div', null, 'episode-topic-suggestion-chips');
    for (const option of options) {
      const chip = node('button', option, 'episode-topic-suggestion-chip'); chip.type = 'button';
      chip.addEventListener('click', () => {
        prompt.value = option; draft.prompt = option;
        const state = getRequestState();
        start.disabled = state.busy || state.blocked || !prompt.value.trim();
        updatePromptLimit(); prompt.focus();
        suggestions.hidden = true; suggestions.replaceChildren();
      });
      chips.append(chip);
    }
    suggestions.append(chips);
  };
  const initialState = getRequestState();
  const start = node('button', initialState.blocked ? 'Try later' : 'Make episode', 'episode-request-start');
  start.type = 'button'; start.id = 'episode-request-start';
  start.setAttribute('aria-label', initialState.blocked ? 'Try again later' : 'Make episode');
  start.disabled = initialState.busy || initialState.blocked || !prompt.value.trim();
  prompt.addEventListener('input', () => {
    draft.prompt = prompt.value;
    if (getRequestState().blocked) {
      setRequestBlocked(false);
      start.textContent = 'Make episode';
      start.setAttribute('aria-label', 'Make episode');
    }
    setRequestError(''); promptError.textContent = '';
    prompt.style.height = 'auto'; prompt.style.height = `${prompt.scrollHeight}px`;
    const state = getRequestState();
    start.disabled = state.busy || state.blocked || !prompt.value.trim();
    updatePromptLimit(); paintSuggestions();
  });
  updatePromptLimit();
  const promptError = node('p', initialState.error, 'episode-request-error'); promptError.setAttribute('role', 'status');
  start.addEventListener('click', () => {
    draft.prompt = prompt.value;
    if (!prompt.value.trim()) { promptError.textContent = 'Add a topic or question. Then send it to make an episode.'; prompt.focus(); return; }
    setRequestError(''); promptError.textContent = ''; void startEpisode(defaultMode);
  });
  promptBox.append(prompt, start); page.append(promptBox, promptLimit, promptError, suggestions);
  paintSuggestions();
  const lengths = node('fieldset', null, 'episode-request-group on-demand-length-group');
  lengths.append(node('legend', 'Choose a length'));
  const chips = node('div', null, 'episode-request-chips');
  for (const minutes of [3, 5, 10, 15]) {
    const choice = node('button', `${minutes} min`, 'episode-request-chip'); choice.type = 'button';
    choice.setAttribute('aria-pressed', String(minutes === draft.minutes));
    choice.addEventListener('click', () => {
      rememberLength(minutes);
      chips.querySelectorAll('button').forEach(chip => chip.setAttribute('aria-pressed', String(chip === choice)));
    });
    chips.append(choice);
  }
  lengths.append(chips); page.append(lengths);
  const examples = node('div', null, 'episode-request-examples');
  examples.append(node('p', 'Try an example', 'episode-request-examples-label'));
  for (const example of ['How do contactless payments settle?', 'Why do storms strengthen over warm water?', 'How do vaccines train the immune system?']) {
    const chip = node('button', example, 'episode-request-example'); chip.type = 'button';
    chip.addEventListener('click', () => {
      prompt.value = example; draft.prompt = example;
      const state = getRequestState();
      start.disabled = state.busy || state.blocked || !prompt.value.trim();
      updatePromptLimit(); suggestions.hidden = true; suggestions.replaceChildren(); prompt.focus();
    });
    examples.append(chip);
  }
  page.append(examples);
  const episodes = node('section', null, 'ask-episodes'); episodes.append(node('h2', 'Your on-demand briefings'));
  const cards = node('div', null, 'ask-episode-cards'); cards.setAttribute('aria-label', 'Your episodes'); episodes.append(cards); page.append(episodes);
  content.append(page); void loadEpisodeCards(cards, examples, prompt);
  return page;
}

export function episodeTopicSuggestions(value) {
  const topic = String(value || '').trim().replace(/[?.!]+$/, '').replace(/^(?:tell me about|what is happening with|what is|learn about)\s+/i, '').trim();
  const words = topic.split(/\s+/).filter(Boolean);
  if (!topic || topic.length > 64 || words.length > 4) return [];
  const lower = topic.toLocaleLowerCase();
  const byTopic = [
    [/^(?:ai|artificial intelligence)$/, [`${topic} in everyday life`, `Rules and policy for ${topic}`, `Research and evidence about ${topic}`]],
    [/^(?:climate|climate change|global warming)$/, [`How heat affects local communities`, `Climate rules and household costs`, `What climate research says about extreme weather`]],
    [/^(?:health|healthcare|health care)$/, [`New treatments and clinical evidence`, `Health coverage and out-of-pocket costs`, `Prevention and public health guidance`]],
    [/^(?:the )?economy|^(?:economics|inflation)$/, [`Prices and household budgets`, `Jobs, wages, and hiring`, `Interest rates and the wider economy`]],
    [/^(?:housing|real estate)$/, [`Rents and housing supply`, `Home prices and mortgage costs`, `Local rules and new construction`]],
    [/^(?:energy|electricity)$/, [`Electric bills and household use`, `Grid reliability and new power supply`, `Energy rules and local projects`]],
    [/^(?:education|schools)$/, [`Classroom learning and student outcomes`, `School funding and local decisions`, `College costs and financial aid`]],
    [/^(?:technology|tech)$/, [`Technology at work and in daily life`, `Privacy, safety, and regulation`, `New research and practical uses`]],
  ];
  const match = byTopic.find(([pattern]) => pattern.test(lower));
  if (match) return match[1];
  if (words.length > 2) return [];
  return [`${topic} in everyday life`, `Rules and decisions about ${topic}`, `Research and evidence on ${topic}`];
}
