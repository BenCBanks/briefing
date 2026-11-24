require('../test_guard.cjs').enforceTestEnvironment();

const assert = require('node:assert/strict');
const { chromium, devices } = require('playwright');

const baseURL = process.env.BRIEFING_URL || process.argv[2] || 'http://127.0.0.1:8935';
async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ ...devices['iPhone 13'], timezoneId: 'America/New_York' });
  page.setDefaultTimeout(5000);
  await page.goto(`${baseURL}/#interests/describe`, { waitUntil: 'domcontentloaded' });
  const button = page.getByRole('button', { name: 'Build my briefing' });
  await button.waitFor({ state: 'visible' });
  assert.equal(await button.isEnabled(), true, 'Build is enabled for empty text');
  const buttonBox = await button.boundingBox();
  assert.ok(buttonBox.y + buttonBox.height <= 844, 'Build button is in the first phone viewport');
  assert.equal(await page.locator('#interest-counter').isVisible(), false, 'counter is hidden below 1,800 characters');
  await page.locator('#interest-words').focus();
  assert.equal(await page.locator('#interest-words').getAttribute('aria-label'), 'Add any topic or describe what matters to you');
  const viewportScale = await page.evaluate(() => window.visualViewport?.scale || 1);
  assert.equal(viewportScale, 1, 'iPhone field focus does not zoom the viewport');
  const fieldStyle = await page.locator('#interest-words').evaluate(el => ({ fontSize: getComputedStyle(el).fontSize, family: getComputedStyle(el).fontFamily }));
  assert.ok(parseFloat(fieldStyle.fontSize) >= 16, `textarea is at least 16px (${fieldStyle.fontSize})`);
  assert.doesNotMatch(fieldStyle.family, /monospace/i, 'textarea uses the UI font');
  await button.click();
  assert.match(await page.locator('#interest-guidance').innerText(), /Add a few words first/);
  await page.locator('#interest-words').fill('I follow payments and payments. I also follow local water policy and local water policy.');
  assert.deepEqual(await page.locator('.interest-starter-topic strong').allTextContents(), ['Payments', 'Local Water Policy'],
    'starter preview combines repeated known and custom topic mentions');
  assert.match(await page.locator('.interest-starter-note').innerText(), /not in the sample edition yet/i,
    'unsupported custom topics are labelled honestly before building');
  await page.getByRole('button', { name: 'Add a little detail' }).click();
  assert.equal(await page.locator('#interest-words').evaluate(el => document.activeElement === el && el.selectionStart === el.value.length), true,
    'the clarification action returns focus to the free-text field');
  await page.locator('#interest-words').fill('I follow payments.');
  await page.getByRole('button', { name: 'Payments & banking' }).click();
  assert.match(await page.locator('#interest-words').inputValue(), /^I follow payments\.[\s\S]*I follow payments and banking/);
  await page.locator('#interest-words').fill('a'.repeat(1799));
  assert.equal(await page.locator('#interest-counter').isVisible(), false, 'counter stays hidden at 1,799 characters');
  await page.locator('#interest-words').fill('a'.repeat(1800));
  assert.equal(await page.locator('#interest-counter').isVisible(), true, 'counter appears at 1,800 characters');
  const onboardingWords = 'Payments and banking news, health insurance and finance policy, AI in finance. Skip crypto hype.';
  await page.locator('#interest-words').fill(onboardingWords);
  await page.route('**/api/interpret', route => route.fulfill({ json: { profile: {
    version: 1, words: onboardingWords, minutes: 12, background: null, muted: [{ label: 'Crypto' }, { label: 'Crypto' }], topics: [
      { id: 'payments-banking', label: 'Payments and Banking News', scope: 'Payment and banking news', include: ['payments', 'banking'], exclude: ['crypto hype'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Payments and banking news' },
      { id: 'health-insurance-finance-policy', label: 'Health Insurance and Finance Policy', scope: 'insurance policy', include: ['health insurance', 'finance policy'], exclude: [], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'health insurance and finance policy' },
      { id: 'ai-in-finance', label: 'AI in Finance', scope: 'Artificial intelligence in finance', include: ['AI', 'finance'], exclude: ['crypto hype'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'AI in finance' },
      { id: 'crypto-hype', label: 'Crypto Hype', scope: 'Promotion', include: [], exclude: ['Skip crypto hype'], depth: 'standard', cadence: 'daily', mustFollow: false, sourcePhrase: 'Skip crypto hype' },
    ] }, question: null } }));
  await page.route('**/api/profile', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/generate', () => new Promise(() => {}));
  await button.click();
  await page.getByRole('heading', { name: 'Building your briefing' }).waitFor();
  await page.getByText('Payments and banking', { exact: true }).waitFor();
  await page.getByText('Health insurance and finance policy', { exact: true }).waitFor();
  await page.getByText('AI in finance', { exact: true }).waitFor();
  await page.getByText('Crypto', { exact: true }).waitFor();
  assert.deepEqual(await page.locator('.build-interest-chip > span').allTextContents(), [
    'Payments and banking', 'Health insurance and finance policy', 'AI in finance', 'Crypto']);
  assert.equal(await page.getByRole('heading', { name: 'Not covering' }).count(), 1);
  for (const label of ['Understanding your words', 'Finding today’s stories', 'Writing your briefing']) await page.getByText(label, { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Edit my words' }).isVisible(), true);
  const removePayments = page.getByRole('button', { name: 'Remove Payments and banking' });
  const removeBox = await removePayments.boundingBox();
  assert.ok(removeBox.width >= 44 && removeBox.height >= 44, 'chip removal has a 44 px touch target');
  await removePayments.click();
  assert.equal(await page.getByRole('button', { name: 'Remove Payments and banking' }).count(), 0, 'removed chip disappears from the interpreted profile');
  await page.getByRole('button', { name: 'Edit my words' }).click();
  assert.match(await page.locator('#interest-words').inputValue(), /Payments and banking news/i, 'Edit returns to A with words preserved');
  await page.setViewportSize({ width: 390, height: 420 });
  const shortButton = await button.boundingBox();
  assert.ok(shortButton.y + shortButton.height <= 420, 'sticky Build button stays visible in a short viewport');
  assert.ok((await page.evaluate(() => document.documentElement.scrollWidth)) <= 390, 'first-run layout has no horizontal page overflow');
  await page.unroute('**/api/interpret');
  await page.route('**/api/interpret', route => route.fulfill({ status: 503, json: { error: 'offline' } }));
  await page.getByRole('button', { name: 'Build my briefing' }).click();
  await page.getByText('I could not pick out topics. Try naming a few, like payment rules.', { exact: true }).waitFor();
  assert.match(await page.locator('#interest-words').inputValue(), /Payments and banking news/i, 'Interpreter failure returns to A with the words intact');
  await browser.close();
  console.log('First-run A/B: sticky enabled action, empty guidance, appending example, iPhone focus zoom, compact chips, progress labels, preserved edit text and short viewport passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
