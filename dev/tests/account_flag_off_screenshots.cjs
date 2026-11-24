require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { DEFAULT_PROFILE, builtProfileKey } = require('./helpers/profile.cjs');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8932';
const output = process.env.BRIEFING_OFF_SHOT_DIR;
const routes = [
  ['today', '#today'], ['on-demand', '#ask-episode'], ['earlier', '#library'],
  ['topics', '#topics'], ['settings', '#preferences'],
];

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.addInitScript(({ profile, key }) => {
    localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(profile));
    localStorage.setItem('briefing:v1:built-profile-key', key);
    localStorage.setItem('briefing:v1:edition-mode', '{}');
  }, { profile: DEFAULT_PROFILE, key: builtProfileKey(DEFAULT_PROFILE) });
  await page.route('**/api/flags', route => route.fulfill({ json: { flags: { accounts: { value: false } } } }));

  for (const [name, hash] of routes) {
    await page.goto(`${baseURL}/${hash}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => {
      const content = document.querySelector('#content');
      return content && content.textContent.trim() && content.textContent.trim() !== 'Preparing';
    });
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.account-access').count(), 0, `accounts stay hidden on ${name} when the flag is off`);
    if (!output) continue;
    fs.mkdirSync(output, { recursive: true });
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme });
      await page.screenshot({ path: path.join(output, `${name}-${theme}.png`), animations: 'disabled' });
    }
    await page.emulateMedia({ colorScheme: 'light' });
  }
  await browser.close();
  console.log('Account flag off: no account controls are mounted; Today, On demand, Earlier, Topics and Settings screenshots captured.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
