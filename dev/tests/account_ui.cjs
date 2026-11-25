require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const shots = process.env.BRIEFING_SHOT_DIR;
let browser;
const shot = async (page, name) => {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.locator('.account-access').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(shots, name), animations: 'disabled' });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: path.join(shots, name.replace(/\.png$/, '-dark.png')), animations: 'disabled' });
  await page.emulateMedia({ colorScheme: 'light' });
};

async function main() {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light', acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  let mode = 'signed-out';
  let attempts = 0;
  let loaded = [];
  const topics = [
    { id: 'digital', label: 'AI chips', scope: 'AI chip design', include: ['AI chips'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'AI chips' },
    { id: 'finance', label: 'Climate policy', scope: 'Climate rules', include: ['Climate policy'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Climate policy' },
  ];
  const profile = { version: 1, words: 'AI chips and climate policy', minutes: 12, background: null, topics, muted: [] };
  const history = [{ title: 'A fictional story about chip design' }];

  await page.route('**/api/flags', route => route.fulfill({ json: { flags: { accounts: { value: true } } } }));
  await page.route('**/api/account**', async route => {
    const url = new URL(route.request().url());
    const pathName = url.pathname;
    if (pathName.endsWith('/me')) return route.fulfill({ status: ['signed-in', 'signed-in-empty'].includes(mode) ? 200 : 401,
      json: ['signed-in', 'signed-in-empty'].includes(mode) ? { account: { id: mode === 'signed-in-empty' ? 'fixture-new-id' : 'fixture-reader-id', name: mode === 'signed-in-empty' ? 'new-reader' : 'reader-example' } } : { error: 'authentication_required' } });
    if (pathName.endsWith('/csrf')) return route.fulfill({ json: { csrf_token: 'fixture-csrf-token' } });
    if (pathName.endsWith('/login')) {
      attempts += 1;
      const data = route.request().postDataJSON();
      assert.equal(typeof data.account, 'string');
      assert.equal(typeof data.password, 'string');
      if (data.password === 'wrong') return route.fulfill({ status: 401, json: { error: 'invalid_credentials' } });
      if (attempts === 2) return route.fulfill({ status: 429, json: { error: 'login_temporarily_locked' } });
      mode = 'signed-in';
      return route.fulfill({ json: { account: { id: 'fixture-reader-id', name: data.account }, csrf_token: 'fixture-csrf-token' } });
    }
    if (pathName.endsWith('/logout')) {
      assert.equal(route.request().headers()['x-csrf-token'], 'fixture-csrf-token');
      mode = 'signed-out'; return route.fulfill({ status: 204, body: '' });
    }
    if (pathName.endsWith('/export')) return route.fulfill({ json: { topics, history } });
    if (pathName.endsWith('/signup')) {
      const data = route.request().postDataJSON();
      assert.equal(data.invite_code, 'FICTIONAL-INVITE');
      assert.equal(data.account, 'new-reader');
      mode = 'signed-in-empty'; return route.fulfill({ status: 201, json: { account: { id: 'fixture-new-id', name: 'new-reader' }, csrf_token: 'fixture-csrf-token' } });
    }
    if (pathName.endsWith('/delete')) {
      assert.equal(route.request().postDataJSON().confirm, 'DELETE', 'deletion uses the service confirmation string');
      assert.equal(route.request().headers()['x-csrf-token'], 'fixture-csrf-token');
      mode = 'signed-out'; return route.fulfill({ status: 204, body: '' });
    }
    return route.fulfill({ status: 404, json: { reason: 'not-found' } });
  });
  await page.route('**/api/profile', route => { loaded.push('topics'); return mode === 'signed-in-empty'
    ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
    : route.fulfill({ json: { profile } }); });
  await page.route('**/api/episodes', route => { loaded.push('history'); return mode === 'signed-in-empty'
    ? route.fulfill({ status: 404, json: { error: 'profile_not_found' } })
    : route.fulfill({ json: { jobs: history.map(item => ({ prompt: item.title })) } }); });
  await page.route('**/api/voice', route => route.fulfill({ json: { letter: 'A' } }));

  await page.goto(`${baseURL}/#preferences`, { waitUntil: 'domcontentloaded' });
  const account = page.locator('.account-access');
  await account.waitFor();
  await page.getByRole('heading', { name: 'Sign in' }).waitFor();
  await shot(page, 'account-sign-in.png');

  await page.getByLabel('Account name').fill('reader-example');
  await page.getByLabel('Password').fill('wrong');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('That account name and password do not match. Try again.').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.matches('input')), true, 'the input receives focus after an error');
  assert.equal(await account.locator('[role="status"]').getAttribute('aria-live'), 'polite', 'the error is announced');
  await shot(page, 'account-wrong-password.png');

  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Too many tries, wait a few minutes.').waitFor();
  await shot(page, 'account-rate-limited.png');
  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByRole('heading', { name: 'Signed in as reader-example' }).waitFor();
  await page.getByText('AI chips').waitFor();
  await page.getByText('A fictional story about chip design').waitFor();
  assert.deepEqual([...new Set(loaded)].sort(), ['history', 'topics'], 'reader-specific topics and history load after sign-in');
  await shot(page, 'account-signed-in.png');

  await page.getByRole('button', { name: 'Your data' }).click();
  await page.getByRole('heading', { name: 'Your data' }).waitFor();
  await shot(page, 'account-your-data.png');
  const download = page.waitForEvent('download', { timeout: 8000 });
  await page.getByRole('button', { name: 'Download my data' }).click();
  assert.equal((await download).suggestedFilename(), 'my-briefing-data.json');
  await page.getByRole('button', { name: 'Delete my account' }).click();
  await page.getByRole('heading', { name: 'Delete your account?' }).waitFor();
  await shot(page, 'account-delete-step-one.png');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('heading', { name: 'Confirm account deletion' }).waitFor();
  await shot(page, 'account-delete-step-two.png');
  await page.getByRole('button', { name: 'Keep my account' }).count().then(count => assert.equal(count, 0));
  await page.getByRole('button', { name: 'Go back' }).click();
  await page.getByRole('button', { name: 'Keep my account' }).click();
  await page.getByRole('button', { name: 'Back to account' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.getByText('You are signed out.').waitFor();
  await shot(page, 'account-signed-out.png');

  mode = 'expired';
  await page.evaluate(() => localStorage.setItem('briefing:account-session', '1'));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('Your session ended. Sign in again to continue. Your place is saved.').waitFor();
  await shot(page, 'account-session-expired.png');

  mode = 'signed-out';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Create an account' }).click();
  await page.getByRole('heading', { name: 'Create an account' }).waitFor();
  await shot(page, 'account-create.png');
  await page.getByLabel('Account name').fill('new-reader');
  await page.getByLabel('Password').fill('long-fictional-password');
  await page.getByLabel('Invite code').fill('FICTIONAL-INVITE');
  await page.getByRole('button', { name: 'Create account' }).click();
  await page.getByRole('heading', { name: 'Signed in as new-reader' }).waitFor();
  await page.getByText('No topics saved yet.').waitFor();
  await page.getByText('No on-demand episodes yet.').waitFor();
  await shot(page, 'account-empty-reader.png');
  const targetSizes = await page.locator('.account-access button').evaluateAll(buttons =>
    buttons.map(button => ({ label: button.textContent, height: button.getBoundingClientRect().height })));
  targetSizes.forEach(target => assert.ok(target.height >= 44, `${target.label} target is at least 44px`));

  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.goto(`${baseURL}/#today`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Sign in to see your briefing' }).waitFor();
  await shot(page, 'account-signed-out-today.png');
  mode = 'expired';
  await page.evaluate(() => localStorage.setItem('briefing:account-session', '1'));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('Your session ended. Sign in again to continue. Your place is saved.').waitFor();
  await shot(page, 'account-session-expired-today.png');
  await page.getByLabel('Account name').fill('reader-example');
  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.account-gate') && document.querySelector('#content').children.length > 0);
  assert.equal(await page.evaluate(() => history.state?.routeContext?.root), 'today', 'a session restored from Today keeps the Today route context');
  await page.goto(`${baseURL}/#preferences`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.goto(`${baseURL}/#ask-episode`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Sign in to make an episode' }).waitFor();
  await shot(page, 'account-signed-out-on-demand.png');
  await page.getByLabel('Account name').fill('reader-example');
  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.locator('.episode-request-page h1').waitFor();
  assert.equal(new URL(page.url()).hash, '#ask-episode', 'sign-in returns to the requested screen');
  await browser.close();
  console.log('Account UI: invite signup, sign-in errors, rate limit, session expiry, sign-out, reader data, deletion confirmation and touch targets passed.');
}

main().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
