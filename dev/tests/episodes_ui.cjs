require('../test_guard.cjs').enforceTestEnvironment();

// Fictional fixture-only Episodes prototype. No backend or provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8933';
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes.json'), 'utf8'));
const firstEpisode = fixture.episodes[0];
firstEpisode.revision_id = 'fixture-episode-r1';
firstEpisode.script_hash = 'fixture-episode-script-r1';
firstEpisode.audio.revision_id = firstEpisode.revision_id;
firstEpisode.audio.script_hash = firstEpisode.script_hash;

async function capture(page, name) {
  if (!process.env.BRIEFING_SHOT_DIR) return;
  fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.BRIEFING_SHOT_DIR, name + '.png') });
}

async function openPage(browser, hash, enabled = true, audioRoute = null) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' });
  page.setDefaultTimeout(20000);
  let fixtureRequests = 0;
  await page.route('**/dev/tests/fixtures/episodes.json', route => {
    fixtureRequests += 1;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture) });
  });
  if (audioRoute) await page.route('**/episode-tone.wav', audioRoute);
  const query = enabled ? '?episodes=1' : '';
  await page.goto(`${baseURL}/${query}#${hash}`, { waitUntil: 'networkidle' });
  return { page, fixtureRequests: () => fixtureRequests };
}

async function run() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const disabled = await openPage(browser, `episode/${firstEpisode.id}`, false);
    const unavailable = disabled.page.locator('.unavailable-page');
    await disabled.page.getByRole('heading', { name: 'This page is unavailable' }).waitFor();
    assert.match(await unavailable.locator('.unavailable-copy').innerText(), /link may have moved or expired/i);
    assert.equal(await unavailable.getByRole('link').count(), 1, 'Unavailable page offers one clear return action');
    const fallbackGeometry = await disabled.page.evaluate(() => {
      const action = document.querySelector('.unavailable-action').getBoundingClientRect();
      const nav = document.querySelector('.main-nav').getBoundingClientRect();
      return { viewportWidth: innerWidth, scrollWidth: document.documentElement.scrollWidth, actionRight: action.right, navTop: nav.top, actionBottom: action.bottom };
    });
    assert.ok(fallbackGeometry.scrollWidth <= fallbackGeometry.viewportWidth, `Unavailable route fits the viewport: ${JSON.stringify(fallbackGeometry)}`);
    assert.ok(fallbackGeometry.actionRight <= fallbackGeometry.viewportWidth, `Return action stays in view: ${JSON.stringify(fallbackGeometry)}`);
    assert.ok(fallbackGeometry.actionBottom <= fallbackGeometry.navTop, `Return action stays above the tabs: ${JSON.stringify(fallbackGeometry)}`);
    for (const theme of ['light', 'dark']) {
      await disabled.page.emulateMedia({ colorScheme: theme });
      await capture(disabled.page, `unavailable-route-${theme}-390`);
    }
    assert.equal(disabled.fixtureRequests(), 0, 'Fixture routes stay disabled unless explicitly enabled');
    assert.deepEqual(await disabled.page.locator('.main-nav-label').allTextContents(), ['Today', 'On demand', 'Earlier', 'Topics']);
    await disabled.page.close();

    const detail = await openPage(browser, `episode/${firstEpisode.id}`, true, async route => {
      await new Promise(resolve => setTimeout(resolve, 400));
      return route.fulfill({ contentType: 'audio/wav', body: fs.readFileSync(path.join(__dirname, 'fixtures/audio/episode-tone.wav')) });
    });
    const page = detail.page;
    await page.locator('.episode-page').waitFor();
    assert.equal(await page.locator('.episode-back').innerText(), '‹ On demand');
    assert.equal(detail.fixtureRequests(), 1);
    assert.equal(await page.locator('.episode-page h1').innerText(), firstEpisode.title);
    assert.match(await page.locator('.episode-asked').innerText(), /Asked:.*What rules are changing/s);
    assert.equal(await page.locator('.episode-meta').innerText(), '4 sec · 6 sources · 4 chapters · made 17 Nov');
    assert.equal(await page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).count(), 1);
    assert.equal(await page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).isDisabled(), false);
    assert.equal(await page.getByRole('heading', { name: 'Chapters · tap a title to jump' }).count(), 1);
    assert.equal(await page.locator('.episode-page .episode-updated').first().innerText(), 'Updated');
    assert.equal(await page.locator('.episode-chapter-list li').count(), 4);
    assert.equal(await page.locator('.episode-chapter-list').getAttribute('aria-label'), 'Episode chapters');
    assert.equal(await page.locator('.episode-chapter').count(), 4);
    assert.equal(await page.locator('.episode-transcript-note').innerText(), 'Tap a source number to see its publisher and date.');
    assert.ok(await page.locator('.episode-sentence').count() >= 6);
    const citationMap = await page.evaluate(() => Array.from(document.querySelectorAll('.episode-sentence'), paragraph => ({
      markers: Array.from(paragraph.querySelectorAll('.episode-citation'), marker => ({
        number: marker.querySelector('summary')?.textContent,
        outlet: marker.querySelector('.episode-citation-detail strong')?.textContent,
      })),
    })));
    const orderedSources = firstEpisode.sources;
    const expectedCitations = firstEpisode.chapters.flatMap(chapter => chapter.sentences.map(sentence =>
      sentence.source_ids.map(id => {
        const index = orderedSources.findIndex(source => source.id === id);
        assert.notEqual(index, -1, `fixture citation ${id} resolves to a listed source`);
        return { number: String(index + 1), outlet: orderedSources[index].outlet };
      })));
    assert.deepEqual(citationMap.map(row => row.markers), expectedCitations,
      'display citation numbers remain stable and each marker resolves to the matching source');
    assert.ok(citationMap.flatMap(row => row.markers).every(marker =>
      Number(marker.number) >= 1 && Number(marker.number) <= orderedSources.length), 'no displayed citation number dangles');
    await page.getByRole('heading', { name: "What we couldn't confirm" }).waitFor();
    await page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).click();
    const playerDialog = page.getByRole('dialog', { name: firstEpisode.title });
    await playerDialog.waitFor();
    await page.locator('[data-player-phase]').filter({ hasText: /Loading audio/ }).waitFor();
    await page.locator('[data-player-phase]').filter({ hasText: /Playing/ }).waitFor();
    assert.equal(await playerDialog.locator('.player-voice-choice').isHidden(), true, 'Recorded episode uses fixture audio without voice synthesis settings');
    assert.equal(await playerDialog.getByRole('button', { name: 'Back 15 seconds' }).count(), 1);
    assert.equal(await playerDialog.getByRole('button', { name: 'Forward 30 seconds' }).count(), 1);
    assert.equal(await playerDialog.getByRole('button', { name: '1.25×' }).count(), 1);
    await capture(page, 'episode-player-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'episode-player-dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await playerDialog.getByRole('button', { name: '1.25×' }).click();
    assert.equal(await playerDialog.getByRole('button', { name: '1.25×' }).getAttribute('aria-pressed'), 'true');
    const seek = playerDialog.getByRole('slider', { name: 'Playback position' });
    assert.equal(await seek.getAttribute('max'), '4', 'Fixture audio duration drives scrubber range');
    await playerDialog.getByRole('button', { name: 'Chapters' }).click();
    const correctedChapter = playerDialog.locator('.player-chapter-list [data-chapter-index="2"]');
    assert.equal(await playerDialog.locator('.player-chapter-list [data-chapter-index="1"] small').innerText(), '0:01–0:02', 'Chapter list includes readable start and end times');
    assert.equal(await correctedChapter.isDisabled(), true, 'Materially corrected chapter audio cannot be selected');
    assert.equal(await playerDialog.locator('.player-chapter-list [data-chapter-index="1"]').isDisabled(), false, 'Other chapter audio remains available');
    await playerDialog.locator('.player-chapter-list [data-chapter-index="1"]').click();
    await playerDialog.locator('[data-player-phase]').filter({ hasText: /Paused · How banks are responding/ }).waitFor();
    await page.waitForFunction(() => document.querySelector('[data-player-now-title]')?.textContent === 'How banks are responding');
    assert.equal(await playerDialog.locator('[data-player-now-title]').innerText(), 'How banks are responding', 'Chapter jump updates the active chapter');
    assert.equal(await playerDialog.locator('[data-player-elapsed]').innerText(), '0:01', 'Chapter jump seeks to its recorded audio boundary');
    await playerDialog.getByRole('button', { name: 'Play episode audio' }).click();
    await playerDialog.locator('[data-player-phase]').filter({ hasText: /Playing/ }).waitFor();
    await playerDialog.getByRole('button', { name: 'Pause episode audio' }).click();
    await playerDialog.locator('[data-player-phase]').filter({ hasText: /Paused/ }).waitFor();
    await playerDialog.getByRole('button', { name: 'Play episode audio' }).click();
    await playerDialog.locator('[data-player-phase]').filter({ hasText: /Playing/ }).waitFor();
    await seek.evaluate(input => { input.value = '2'; input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); });
    await playerDialog.locator('[data-player-elapsed]').filter({ hasText: '0:02' }).waitFor();
    await playerDialog.locator('[data-player-phase]').filter({ hasText: /Finished/ }).waitFor();
    assert.equal(await playerDialog.getByRole('button', { name: 'Replay episode audio' }).count(), 1, 'Finished audio can be replayed');
    await page.keyboard.press('Escape');
    await playerDialog.waitFor({ state: 'detached' });
    assert.equal(await page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).evaluate(el => document.activeElement === el), true, 'Closing player returns focus to Listen');
    await capture(page, 'episode-page-light');
    await page.emulateMedia({ colorScheme: 'dark' }); await capture(page, 'episode-page-dark');
    await page.emulateMedia({ colorScheme: 'light' });

    await page.locator('.episode-chapter-disclosure').first().evaluate(details => { details.open = true; });
    const marker = page.locator('.episode-citation').first();
    assert.equal(await marker.locator('summary').innerText(), '1');
    await marker.locator('summary').click();
    assert.equal(await marker.locator('.episode-citation-detail strong').innerText(), 'Fictional City Journal');
    assert.equal(await marker.locator('time').innerText(), '17 Nov 2026');
    assert.equal(await marker.getByRole('link', { name: 'Open source' }).getAttribute('href'), 'https://journal.example.test/instant-network');
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    const lateCitation = page.locator('.episode-citation').last();
    await lateCitation.evaluate(details => { details.closest('.episode-chapter-disclosure').open = true; });
    const lateSummary = lateCitation.locator('summary');
    const lateSource = lateCitation.getByRole('link', { name: 'Open source' });
    await lateSummary.focus();
    await page.keyboard.press('Enter');
    assert.equal(await lateCitation.evaluate(el => el.open), true, 'Keyboard activation opens the late transcript citation');
    await page.keyboard.press('Tab');
    assert.equal(await lateSource.evaluate(el => document.activeElement === el), true, 'Tab reaches the late citation source at enlarged text');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await lateSummary.evaluate(el => document.activeElement === el), true, 'Shift+Tab returns from the source to its citation control');
    await page.keyboard.press('Enter');
    assert.equal(await lateCitation.evaluate(el => el.open), false, 'Keyboard activation closes the late citation');
    assert.equal(await lateSummary.evaluate(el => document.activeElement === el), true, 'Closing the citation keeps focus on its summary');
    await page.keyboard.press('Enter');
    assert.equal(await lateCitation.evaluate(el => el.open), true, 'Keyboard activation reopens the late citation at enlarged text');
    assert.equal(await lateSummary.evaluate(el => document.activeElement === el), true, 'Reopening the citation retains focus on its summary');
    await page.keyboard.press('Enter');
    assert.equal(await lateCitation.evaluate(el => el.open), false, 'Citation is closed after the reopen focus check');
    await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
    await page.getByRole('link', { name: 'Why the city is changing its rules' }).click();
    await page.waitForFunction(() => document.querySelector('#episode-chapter-why-change')?.getBoundingClientRect().top >= 0);
    assert.match(page.url(), /#episode\/episode-payment-rules$/, 'Chapter navigation stays on the episode route');
    const visibleControls = page.locator('.episode-page a:visible, .episode-page button:visible, .episode-page summary:visible');
    assert.ok(await visibleControls.evaluateAll(controls => controls.every(control => control.getBoundingClientRect().height >= 44)), 'Visible episode controls meet the 44 px target');
    await page.locator('.episode-report summary').click();
    await page.getByText(/reports are not sent/i).waitFor();
    await page.close();

    const failed = await openPage(browser, `episode/${firstEpisode.id}`, true, route => route.fulfill({ status: 404, body: 'missing fixture audio' }));
    await failed.page.getByRole('button', { name: `Play episode: ${firstEpisode.title}` }).click();
    const failedDialog = failed.page.getByRole('dialog', { name: firstEpisode.title });
    await failedDialog.locator('[data-player-phase]').filter({ hasText: /Failed/ }).waitFor();
    assert.equal(await failedDialog.getByRole('button', { name: 'Retry episode audio' }).count(), 1, 'Failed audio offers a retry');
    await failedDialog.getByRole('button', { name: 'Retry episode audio' }).click();
    await failedDialog.locator('[data-player-phase]').filter({ hasText: /Failed/ }).waitFor();
    await failed.page.keyboard.press('Escape'); await failed.page.close();

    const deck = await openPage(browser, 'episodes');
    const deckPage = deck.page;
    await deckPage.locator('.episode-deck-page').waitFor();
    const cards = deckPage.locator('.episode-card');
    assert.equal(await cards.count(), 2);
    assert.deepEqual(await deckPage.locator('.episode-deck-page').evaluate(el => {
      const rect = el.getBoundingClientRect(); return [rect.x, rect.y, rect.width, rect.height];
    }), [0, 0, 390, 844], 'Deck fills the requested phone viewport');
    assert.equal(await deckPage.locator('.episode-deck-track').evaluate(el => getComputedStyle(el).scrollSnapType), 'x mandatory');
    const previews = deckPage.getByRole('button', { name: '30-second preview, not available in this fixture' });
    assert.equal(await previews.count(), 2);
    assert.equal(await previews.evaluateAll(buttons => buttons.every(button => button.disabled)), true);
    assert.equal(await cards.evaluateAll(items => items.every((card, index) => card.querySelectorAll('.episode-listen').length === 1 && card.querySelector('.episode-listen').disabled === (index !== 0))), true, 'Only the fixture with recorded audio offers Listen');
    const firstCardTitle = await cards.first().locator('h2').innerText();
    await deckPage.getByRole('button', { name: 'Shuffle' }).click();
    assert.equal(await deckPage.locator('.episode-card').count(), 2);
    assert.notEqual(await cards.first().locator('h2').innerText(), firstCardTitle, 'Shuffle changes the visible order');
    assert.equal(await deckPage.locator('.episode-deck-close').evaluate(el => el.getBoundingClientRect().height >= 44), true);
    assert.ok(await deckPage.locator('.episode-deck-page button:visible, .episode-deck-page a:visible').evaluateAll(controls => controls.every(control => control.getBoundingClientRect().height >= 44)));
    await capture(deckPage, 'episode-deck-light');
    await deckPage.emulateMedia({ colorScheme: 'dark' }); await capture(deckPage, 'episode-deck-dark');
    await deckPage.emulateMedia({ colorScheme: 'light' });
    await deckPage.keyboard.press('ArrowRight');
    assert.equal(await deckPage.locator('.episode-deck-position').innerText(), '2 of 2', 'Right arrow advances the deck');
    await deckPage.keyboard.press('ArrowLeft');
    assert.equal(await deckPage.locator('.episode-deck-position').innerText(), '1 of 2', 'Left arrow returns in the deck');
    await deckPage.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await deckPage.locator('.episode-deck-track').evaluate(el => getComputedStyle(el).scrollBehavior), 'auto');
    await deckPage.getByRole('button', { name: 'Close episode cards' }).click();
    await deckPage.locator('.today-page').waitFor();
    assert.equal(new URL(deckPage.url()).hash, '#today');
    await deckPage.close();
    console.log('Episode fixture audio, shared player controls, corrected chapter safety, citations, chapters, updated tag, full-screen deck, keyboard, reduced motion, close and dark/light screenshots passed.');
  } finally { await browser.close(); }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
