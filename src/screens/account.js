// Account UI is deliberately isolated from the single-reader screens. The
// account flag gates mounting; API calls are injected so the flow can be
// exercised with fixtures before the server contract is enabled.

export function accountApi(fetcher = fetch) {
  const SESSION_MARKER = 'briefing:account-session';
  let csrfToken = '';
  async function request(path, options) {
    const response = await fetcher(path, { cache: 'no-store', credentials: 'same-origin', ...options });
    let body = {};
    try { body = await response.json(); } catch { /* An empty response is handled below. */ }
    return { response, body };
  }
  async function csrf() {
    const { response, body } = await request('/api/account/csrf');
    if (!response.ok || typeof body.csrf_token !== 'string') throw Error('session unavailable');
    csrfToken = body.csrf_token;
  }
  async function mutate(path, payload = {}) {
    if (!csrfToken) await csrf();
    return fetcher(path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, body: JSON.stringify(payload) });
  }
  return {
    async sessionState() {
      const { response, body } = await request('/api/account/me');
      if (response.status === 401) return { expired: localStorage.getItem(SESSION_MARKER) === '1' };
      if (!response.ok) throw Error('session unavailable');
      localStorage.setItem(SESSION_MARKER, '1');
      await csrf();
      return { reader: body.account };
    },
    async signIn(details) {
      const { response, body } = await request('/api/account/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account: details.account, password: details.password }) });
      if (response.ok) { csrfToken = body.csrf_token; localStorage.setItem(SESSION_MARKER, '1'); }
      return response.ok ? { ok: true, reader: body.account } : { ok: false, reason: body.error };
    },
    async createAccount(details) {
      const { response, body } = await request('/api/account/signup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ invite_code: details.inviteCode, account: details.account, password: details.password }) });
      if (response.ok) { csrfToken = body.csrf_token; localStorage.setItem(SESSION_MARKER, '1'); }
      return response.ok ? { ok: true, reader: body.account } : { ok: false, reason: body.error };
    },
    async signOut() {
      const response = await mutate('/api/account/logout');
      if (!response.ok) throw Error('sign out failed');
      csrfToken = ''; localStorage.removeItem(SESSION_MARKER);
    },
    async loadTopics() { const { response, body } = await request('/api/profile'); if (response.status === 404) return null; if (!response.ok) throw Error('topics unavailable'); return body.profile; },
    async loadHistory() { const { response, body } = await request('/api/episodes'); if (response.status === 404) return []; if (!response.ok) throw Error('history unavailable'); return body.jobs || []; },
    async downloadData() { const { response, body } = await request('/api/account/export'); if (!response.ok) throw Error('export unavailable'); return body; },
    async deleteAccount() {
      const response = await mutate('/api/account/delete', { confirm: 'DELETE' });
      if (!response.ok) throw Error('delete failed');
      csrfToken = ''; localStorage.removeItem(SESSION_MARKER);
    },
  };
}
export async function mountAccountSettings({ content, node, api = accountApi(), fetcher = fetch, onAccountChange, enabled }) {
  if (enabled !== true) {
    if (enabled === false) return null;
    try {
      const response = await fetcher('/api/flags', { cache: 'no-store' });
      const data = await response.json();
      if (data?.flags?.accounts?.value !== true) return null;
    } catch { return null; }
  }
  if (content.querySelector('h1')?.textContent !== 'Settings') return null;
  return renderAccountAccess({ content, node, api, onAccountChange });
}

function loadAccountStyles() {
  if (document.querySelector('link[data-account-styles]')) return;
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet'; stylesheet.href = '/src/accounts/accounts.css';
  stylesheet.dataset.accountStyles = 'true'; document.head.append(stylesheet);
}

export function renderAccountAccess({ content, node, api, gateRoute = '', onAccountChange = () => {} }) {
  loadAccountStyles();
  const section = node('section', null, `account-access${gateRoute ? ' account-gate' : ''}`);
  section.setAttribute('aria-labelledby', 'account-heading');
  const title = gateRoute === 'ask-episode' || gateRoute === 'ask'
    ? 'Sign in to make an episode' : gateRoute ? 'Sign in to see your briefing' : 'Account';
  const heading = node(gateRoute ? 'h1' : 'h2', title, 'account-heading'); heading.id = 'account-heading';
  section.append(heading);
  if (gateRoute) section.append(node('p', 'Your topics and listening history are saved with your account.'));
  const status = node('p', '', 'account-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.tabIndex = -1;
  const formHost = node('div', null, 'account-form-host');
  section.append(status, formHost);
  content.append(section);

  let signedIn = false;
  let signedInName = '';
  let readerData = {};
  let requestInFlight = false;
  let pendingDelete = false;

  function announce(message, { focus = false } = {}) {
    status.textContent = message;
    if (focus) status.focus();
  }

  function button(label, action, className = 'account-action') {
    const control = node('button', label, className);
    control.type = 'button';
    control.addEventListener('click', action);
    return control;
  }

  function field(labelText, type, autocomplete) {
    const label = node('label', labelText, 'account-field');
    const input = node('input');
    input.type = type;
    input.autocomplete = autocomplete;
    input.required = true;
    label.append(input);
    return { label, input };
  }

  function busy(value) {
    requestInFlight = value;
    formHost.querySelectorAll('button, input').forEach(control => { control.disabled = value; });
  }

  function focusError(message) {
    busy(false);
    announce(message, { focus: true });
    const invalid = formHost.querySelector('input:invalid') || formHost.querySelector('input');
    invalid?.focus();
  }

  function showSignIn({ message = '', mode = 'sign-in' } = {}) {
    signedIn = false;
    pendingDelete = false;
    formHost.replaceChildren();
    const title = mode === 'create' ? 'Create an account' : 'Sign in';
    const form = node('form', null, 'account-form');
    form.append(node('h3', title));
    const account = field('Account name', 'text', 'username');
    const password = field('Password', 'password', mode === 'create' ? 'new-password' : 'current-password');
    form.append(account.label, password.label);
    let invite;
    if (mode === 'create') {
      invite = field('Invite code', 'text', 'off');
      invite.input.autocapitalize = 'characters';
      form.append(invite.label);
    }
    const submit = node('button', mode === 'create' ? 'Create account' : 'Sign in', 'account-primary');
    submit.type = 'submit';
    form.append(submit);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (requestInFlight) return;
      if (!account.input.value.trim() || !password.input.value) {
        focusError('Enter your account name and password.');
        return;
      }
      if (mode === 'create' && !invite.input.value.trim()) {
        focusError('Enter your invite code.');
        return;
      }
      busy(true);
      announce('');
      try {
        const result = mode === 'create'
          ? await api.createAccount({ account: account.input.value.trim(), password: password.input.value, inviteCode: invite.input.value.trim() })
          : await api.signIn({ account: account.input.value.trim(), password: password.input.value });
        if (!result?.ok) {
          if (['rate-limited', 'login_temporarily_locked'].includes(result?.reason)) {
            focusError('Too many tries, wait a few minutes.');
          } else if (['invalid_credentials', 'wrong-password', 'invalid_invite_code', 'invalid-invite'].includes(result?.reason)) {
            focusError(mode === 'create' ? 'That invite code could not be used. Check it and try again.' : 'That account name and password do not match. Try again.');
          } else {
            focusError('We could not sign you in just now. Try again.');
          }
          return;
        }
        signedIn = true;
        signedInName = result.reader?.name || account.input.value.trim();
        const loadedReaderData = await loadReaderData();
        // Retain the reader-specific sections while moving between account
        // details and the sign-in screen.
        readerData = loadedReaderData;
        onAccountChange(result.reader, readerData);
        showSignedIn(readerData);
      } catch {
        focusError('We could not connect. Check your connection and try again.');
      } finally {
        busy(false);
      }
    });
    formHost.append(form);
    const switches = node('div', null, 'account-switches');
    if (mode === 'sign-in') {
      switches.append(node('p', 'Have an invite?'));
      switches.append(button('Create an account', () => showSignIn({ mode: 'create' })));
    } else {
      switches.append(node('p', 'Already have an account?'));
      switches.append(button('Sign in', () => showSignIn()));
    }
    formHost.append(switches);
    if (message) announce(message);
    if (message) status.focus();
  }

  async function loadReaderData() {
    // Account-specific topics and history load only after authentication.
    const [topics, history] = await Promise.all([api.loadTopics(), api.loadHistory()]);
    return { topics, history };
  }

  function showSignedIn(data = {}) {
    formHost.replaceChildren();
    const heading = node('h3', `Signed in as ${signedInName}`);
    const row = node('div', null, 'account-signed-in-actions');
    row.append(button('Sign out', async () => {
      if (requestInFlight) return;
      busy(true);
      try {
        await api.signOut();
        signedIn = false;
        readerData = {};
        onAccountChange(null, {});
        showSignIn({ message: 'You are signed out.' });
      } catch {
        announce('We could not sign you out. Try again.', { focus: true });
      } finally { busy(false); }
    }));
    row.append(button('Your data', showData));
    formHost.append(heading, row);
    const topics = Array.isArray(data.topics?.topics) ? data.topics.topics : Array.isArray(data.topics) ? data.topics : [];
    const history = Array.isArray(data.history?.items) ? data.history.items : Array.isArray(data.history) ? data.history : [];
    const topicSection = node('section', null, 'account-topics');
    topicSection.append(node('h4', 'Your topics'));
    if (topics.length) {
      const list = node('ul');
      topics.forEach(topic => list.append(node('li', typeof topic === 'string' ? topic : topic.label || '')));
      topicSection.append(list);
    } else topicSection.append(node('p', 'No topics saved yet.'));
    formHost.append(topicSection);
    const historySection = node('section', null, 'account-history');
    historySection.append(node('h4', 'Your listening history'));
    if (history.length) {
      const list = node('ul');
      history.slice(0, 5).forEach(item => list.append(node('li', typeof item === 'string' ? item : item.title || item.prompt || item.question || '')));
      historySection.append(list);
    } else historySection.append(node('p', 'No on-demand episodes yet.'));
    formHost.append(historySection);
    announce('Your account is ready.');
  }

  function showData() {
    formHost.replaceChildren();
    const heading = node('h3', 'Your data');
    const description = node('p', 'You can download a copy of your data or delete your account.');
    const download = button('Download my data', async () => {
      try {
        const data = await api.downloadData();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = node('a', 'Download my data');
        anchor.href = url;
        anchor.download = 'my-briefing-data.json';
        document.body.append(anchor);
        anchor.click();
        setTimeout(() => { anchor.remove(); URL.revokeObjectURL(url); }, 1000);
        announce('Your data download is ready.');
      } catch { announce('We could not prepare your download. Try again.'); }
    });
    const remove = button('Delete my account', () => showDeleteConfirmation(1), 'account-danger');
    const back = button('Back to account', () => showSignedIn(readerData));
    formHost.append(heading, description, download, remove, back);
  }

  function showDeleteConfirmation(step) {
    pendingDelete = true;
    formHost.replaceChildren();
    const heading = node('h3', step === 1 ? 'Delete your account?' : 'Confirm account deletion');
    const detail = node('p', step === 1
      ? 'This permanently deletes your account, topics and listening history.'
      : 'Your account and its data will be deleted. This cannot be undone.');
    const actions = node('div', null, 'account-delete-actions');
    if (step === 1) {
      actions.append(button('Continue', () => showDeleteConfirmation(2), 'account-danger'));
      actions.append(button('Keep my account', showData));
    } else {
      actions.append(button('Delete account', async () => {
        if (!pendingDelete || requestInFlight) return;
        busy(true);
        try {
          await api.deleteAccount();
          signedIn = false;
          pendingDelete = false;
          readerData = {};
          onAccountChange(null, {});
          showSignIn({ message: 'Your account has been deleted.' });
        } catch { announce('We could not delete your account. Try again.', { focus: true }); }
        finally { busy(false); }
      }, 'account-danger'));
      actions.append(button('Go back', () => showDeleteConfirmation(1)));
    }
    formHost.append(heading, detail, actions);
  }

  api.sessionState?.().then(result => {
    if (result?.expired) showSignIn({ message: 'Your session ended. Sign in again to continue. Your place is saved.' });
    else if (result?.reader) {
      signedIn = true;
      signedInName = result.reader.name || 'reader';
      loadReaderData().then(data => {
        readerData = data; onAccountChange(result.reader, readerData); showSignedIn(readerData);
      }).catch(() => showSignedIn(readerData));
    } else showSignIn();
  }).catch(() => showSignIn());

  // Leave a hook for a future session-expiry event without coupling the
  // single-reader app to the account service before its contract is ready.
  section.addEventListener('account-session-expired', () => {
    showSignIn({ message: 'Your session ended. Sign in again to continue. Your place is saved.' });
  });
  return { section, showSignIn, get signedIn() { return signedIn; } };
}
