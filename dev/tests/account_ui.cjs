require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const shots = process.env.BRIEFING_SHOT_DIR;
const shot = async (page, name) => {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, name), fullPage: true, animations: 'disabled' });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: path.join(shots, name.replace(/\.png$/, '-dark.png')), fullPage: true, animations: 'disabled' });
  await page.emulateMedia({ colorScheme: 'light' });
};

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
  const page = await context.newPage();
  let mode = 'signed-out';
  let attempts = 0;
  let loaded = [];
  const topics = [{ label: 'AI chips' }, { label: 'Climate policy' }];
  const history = [{ title: 'A fictional story about chip design' }];

  await page.route('**/api/flags', route => route.fulfill({ json: { flags: { accounts: { value: true } } } }));
  await page.route('**/api/account**', async route => {
    const url = new URL(route.request().url());
    const pathName = url.pathname;
    if (pathName.endsWith('/session')) return route.fulfill({ json: mode === 'expired'
      ? { expired: true } : mode === 'signed-in' ? { reader: { email: 'reader@example.test' } } : {} });
    if (pathName.endsWith('/sign-in')) {
      attempts += 1;
      const data = route.request().postDataJSON();
      if (data.password === 'wrong') return route.fulfill({ status: 401, json: { reason: 'wrong-password' } });
      if (attempts === 2) return route.fulfill({ status: 429, json: { reason: 'rate-limited' } });
      mode = 'signed-in';
      return route.fulfill({ json: { reader: { email: data.email } } });
    }
    if (pathName.endsWith('/topics')) { loaded.push('topics'); return route.fulfill({ json: { topics } }); }
    if (pathName.endsWith('/history')) { loaded.push('history'); return route.fulfill({ json: { items: history } }); }
    if (pathName.endsWith('/sign-out')) { mode = 'signed-out'; return route.fulfill({ json: {} }); }
    if (pathName.endsWith('/data')) return route.fulfill({ json: { topics, history } });
    if (pathName.endsWith('/account') && route.request().method() === 'POST') {
      mode = 'signed-in'; return route.fulfill({ json: { reader: { email: 'new@example.test' } } });
    }
    if (pathName.endsWith('/account') && route.request().method() === 'DELETE') {
      mode = 'signed-out'; return route.fulfill({ json: {} });
    }
    return route.fulfill({ status: 404, json: { reason: 'not-found' } });
  });
  await page.route('**/api/profile', route => route.fulfill({ status: 404, json: { error: 'profile_not_found' } }));
  await page.route('**/api/voice', route => route.fulfill({ json: { letter: 'A' } }));

  await page.goto(`${baseURL}/#preferences`, { waitUntil: 'domcontentloaded' });
  const account = page.locator('.account-access');
  await account.waitFor();
  await page.getByRole('heading', { name: 'Sign in' }).waitFor();
  await shot(page, 'account-sign-in-light.png');

  await page.getByLabel('Email address').fill('reader@example.test');
  await page.getByLabel('Password').fill('wrong');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('That email and password do not match. Try again.').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'status', 'errors receive focus');
  await shot(page, 'account-wrong-password.png');

  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Too many tries, wait a few minutes.').waitFor();
  await shot(page, 'account-rate-limited.png');
  await page.getByLabel('Password').fill('correct');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByRole('heading', { name: 'Signed in as reader@example.test' }).waitFor();
  await page.getByText('AI chips').waitFor();
  await page.getByText('A fictional story about chip design').waitFor();
  assert.deepEqual([...new Set(loaded)].sort(), ['history', 'topics'], 'reader-specific topics and history load after sign-in');
  await shot(page, 'account-signed-in-light.png');

  await page.getByRole('button', { name: 'Your data' }).click();
  await page.getByRole('heading', { name: 'Your data' }).waitFor();
  await shot(page, 'account-your-data-light.png');
  await page.getByRole('button', { name: 'Delete my account' }).click();
  await page.getByRole('heading', { name: 'Delete your account?' }).waitFor();
  await shot(page, 'account-delete-step-one-light.png');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('heading', { name: 'Confirm account deletion' }).waitFor();
  await page.getByRole('button', { name: 'Keep my account' }).count().then(count => assert.equal(count, 0));
  await page.getByRole('button', { name: 'Go back' }).click();
  await page.getByRole('button', { name: 'Keep my account' }).click();
  await page.getByRole('button', { name: 'Back to account' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.getByText('You are signed out.').waitFor();

  mode = 'expired';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('Your session ended. Sign in again to continue. Your place is saved.').waitFor();
  await shot(page, 'account-session-expired.png');

  mode = 'signed-out';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Create an account' }).click();
  await page.getByRole('heading', { name: 'Create an account' }).waitFor();
  await shot(page, 'account-create.png');
  await page.getByLabel('Email address').fill('new@example.test');
  await page.getByLabel('Password').fill('long-fictional-password');
  await page.getByLabel('Invite code').fill('FICTIONAL-INVITE');
  await page.getByRole('button', { name: 'Create account' }).click();
  await page.getByRole('heading', { name: 'Signed in as new@example.test' }).waitFor();
  const targetSizes = await page.locator('.account-access button').evaluateAll(buttons =>
    buttons.map(button => ({ label: button.textContent, height: button.getBoundingClientRect().height })));
  targetSizes.forEach(target => assert.ok(target.height >= 44, `${target.label} target is at least 44px`));
  await browser.close();
  console.log('Account UI: invite signup, sign-in errors, rate limit, session expiry, sign-out, reader data, deletion confirmation and touch targets passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
