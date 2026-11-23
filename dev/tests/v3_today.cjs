require('../test_guard.cjs').enforceTestEnvironment();

// V3 uses fictional editorial copy and blocks every external request and API mutation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { seedInterestProfile, DEFAULT_PROFILE } = require('./helpers/profile.cjs');
const editionRendererSource = fs.readFileSync(path.join(__dirname, '../../src/edition/edition.js'), 'utf8');
const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8949';
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/legacy-state/generated', name), 'utf8'));
const edition = fixture('editions.json').editions[0];
const sources = fixture('sources.json').sources;
sources.push({...sources[0],id:'v3-source-two',publisher:'Cedar News',url:'https://cedar.example.test/report'});
const profile = {...DEFAULT_PROFILE,words:'Payments, health policy, and AI in finance.',topics:['Payments','Health policy','AI in finance'].map((label,i)=>({...DEFAULT_PROFILE.topics[0],id:['payments','health-policy','ai-finance'][i],label,include:[label],sourcePhrase:label}))};
const headlines = ['Cedar Bank opens instant transfers for local businesses', 'Harbor extends health enrollment by Six Weeks', 'Northstar tests an AI review team: Staff keep control'];
edition.sections = [0, 1, 2].map((i) => ({ ...edition.sections[i % edition.sections.length], id: `v3-story-${i}`, topic_ids:[profile.topics[i].id], heading: headlines[i], headline: headlines[i], lede: ['U.S. payment network Cedar opened transfers on Monday. Its pilot includes 12 branches.', 'Harbor extended enrollment by six weeks. Residents can apply until December.', 'Northstar assigned eight staff to its pilot. They will review each AI recommendation.'][i], so_what: ['Customers can move money after bank hours.', 'Residents have more time to compare plans.', 'Staff retain the final decision.'][i], more: ['The fictional pilot will be reviewed after three months.'], watch: 'The next published review will report the results.', source_ids: sources.slice(0, i === 0 ? 2 : 1).map(s => s.id) }));
edition.generated_at = '2026-11-18T11:20:00Z';
edition.audio = { ...edition.audio, duration_seconds: 180 };
edition.greeting = 'Unbounded server greeting '.repeat(30);
edition.coverage = profile.topics.map(topic=>({topic_id:topic.id,state:'covered'}));
edition.quick_hits = [{ title: 'Unfiltered item must never appear', url: 'https://example.test/one' }, { title: 'Another unfiltered item', url: 'https://example.test/two' }];
edition.also_today = { label: 'Also today · 4 more', items: [
 { title:'Cedar expands instant payment access', outlet:'Cedar News', url:'https://cedar.example.test/quick-1' },
 { title:'Harbor publishes enrollment guidance', outlet:'Harbor Journal', url:'https://harbor.example.test/quick-2' },
 { title:'Northstar reports AI pilot results', outlet:'Northstar Review', url:'https://northstar.example.test/quick-3' },
 { title:'River County updates its transit plan', outlet:'River County', url:'https://river.example.test/quick-4' },
] };
const historyEditions = ['2026-11-16','2026-11-15','2026-11-12'].map((date,index)=>({...edition,
 id:`fictional-history-${date}`,revision_id:`fictional-history-${date}-r1`,local_date:date,title:`Briefing · ${date}`,
 generated:true,sections:edition.sections.map((section,i)=>({...section,id:`history-${index}-${i}`}))}));
let statusFixture = { daily_run: { local_date: edition.local_date, daily_briefing: { duration_seconds: 127, spoken_words: 318, target_minutes: 5, supply_ladder: [] }, daily_audio: { audio_status: 'ready', ready_at: '2026-11-17T10:02:00Z', duration_seconds: 120 } } };
async function capture(page, name) { if (process.env.BRIEFING_SHOT_DIR) { await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))); fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true }); await page.screenshot({ animations: 'disabled', path: path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`) }); } }
async function checkBottomTabs(page, active) {
 const nav = page.getByRole('navigation', { name: 'Main', exact: true });
 for (const name of ['Today', 'On demand', 'Earlier', 'Topics']) {
  const tab = nav.getByRole('link', { name, exact: true });
  assert.equal(await tab.count(), 1, 'Navigation label is accessible');
   assert.equal(await tab.getAttribute('aria-current'), name === (active === 'Ask' ? 'On demand' : active) ? 'page' : null);
  const metrics = await tab.evaluate(el => {
   const icon = el.querySelector('svg'), label = el.querySelector('.main-nav-label');
   const i = icon.getBoundingClientRect(), l = label.getBoundingClientRect(), t = el.getBoundingClientRect();
   const style = getComputedStyle(el), underline = getComputedStyle(el, '::after');
   const token = style.getPropertyValue(el.hasAttribute('aria-current') ? '--accent' : '--muted').trim();
   const probe = document.createElement('span'); probe.style.color = token; el.append(probe);
   const expectedColor = getComputedStyle(probe).color; probe.remove();
   return { width:i.width, height:i.height, above:i.bottom <= l.top, inside:l.bottom <= t.bottom,
    target:t.height, hidden:icon.getAttribute('aria-hidden'), focusable:icon.getAttribute('focusable'),
    weight:icon.getAttribute('stroke-width'), stroke:icon.getAttribute('stroke'),
    matchesColor:getComputedStyle(icon).stroke === expectedColor && getComputedStyle(label).color === expectedColor,
    underlineHeight:underline.height, underlineColor:underline.backgroundColor, expectedColor };
  });
  assert.deepEqual([metrics.width, metrics.height, metrics.weight, metrics.stroke, metrics.hidden, metrics.focusable], [24,24,'1.8','currentColor','true','false']);
  assert.ok(metrics.above && metrics.inside && metrics.target >= 48 && metrics.matchesColor, JSON.stringify(metrics));
  if (name === active) assert.deepEqual([metrics.underlineHeight, metrics.underlineColor], ['3px', metrics.expectedColor]);
 }
}
async function captureBottomBar(page, name) {
 if (!process.env.BRIEFING_SHOT_DIR) return;
 const nav = await page.locator('.main-nav').boundingBox();
 const player = await page.locator('.mini-player').count() ? await page.locator('.mini-player').boundingBox() : nav;
 fs.mkdirSync(process.env.BRIEFING_SHOT_DIR, { recursive: true });
 await page.screenshot({ animations:'disabled', path:path.join(process.env.BRIEFING_SHOT_DIR, `${name}.png`), clip:{x:0,y:player.y,width:390,height:844-player.y} });
}
async function checkContrast(page) {
 await page.evaluate(()=>Promise.all(document.getAnimations().map(animation=>animation.finished)));
 const results = await page.evaluate(() => {
  const rgba = value => value.match(/[\d.]+/g).map(Number);
  const blend = (top,bottom) => top.slice(0,3).map((v,i)=>v*(top[3] ?? 1)+bottom[i]*(1-(top[3] ?? 1)));
  const lum = color => color.slice(0,3).map(c=>c/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4).reduce((v,c,i)=>v+c*[.2126,.7152,.0722][i],0);
  return [...document.querySelectorAll('main *, .main-nav *')].filter(el=>el.getClientRects().length && [...el.childNodes].some(n=>n.nodeType===3 && n.textContent.trim())).map(el=>{
   const ancestors=[]; for(let a=el;a;a=a.parentElement) ancestors.unshift(a);
   let bg=[255,255,255]; for(const a of ancestors) bg=blend(rgba(getComputedStyle(a).backgroundColor),bg);
   const fg=blend(rgba(getComputedStyle(el).color),bg), values=[lum(fg),lum(bg)].sort((a,b)=>b-a);
   return {text:el.textContent.trim().slice(0,65),ratio:(values[0]+.05)/(values[1]+.05)};
  });
 });
 assert.deepEqual(results.filter(r=>r.ratio<4.5), [], 'Every visible Today text style reaches 4.5:1');
 console.log(`Today ${await page.evaluate(()=>matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light')} minimum contrast: ${Math.min(...results.map(r=>r.ratio)).toFixed(2)}:1`);
}
(async () => {
 const browser = await chromium.launch({ args: ['--no-sandbox'] });
 try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'America/New_York' }); page.setDefaultTimeout(6000);
  const errors = []; page.on('pageerror', e => { errors.push(e.message); console.error(e.message); });
  await page.route('**/*', route => new URL(route.request().url()).origin === baseURL && !['POST','PUT','DELETE'].includes(route.request().method()) ? route.continue() : route.abort());
  await page.route('**/api/profile', r => r.fulfill({ json: { profile } }));
  await page.route('**/api/edition**', r => r.fulfill({ json: { generated: true, edition, sources,
   ...(new URL(r.request().url()).searchParams.get('history') === '1' ? { history: historyEditions.map(edition=>({edition,sources})) } : {}) } }));
  await page.route('**/api/status', r => r.fulfill({ json: statusFixture }));
  await page.route('**/api/episodes', r => r.fulfill({ json: { jobs: [] } }));
  await seedInterestProfile(page,profile);
  await page.addInitScript(() => {
   window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
   Object.defineProperty(window, 'speechSynthesis', { value: { speaking:false,paused:false,getVoices:()=>[],speak(u){this.speaking=true;queueMicrotask(()=>u.onstart?.());},pause(){this.paused=true;},resume(){this.paused=false;},cancel(){this.speaking=false;} } });
  });
  await page.goto(`${baseURL}/#today`); await page.locator('.today-story-row').first().waitFor().catch(async e => { console.log(await page.locator('body').innerText()); throw e; });
  assert.equal(await page.locator('.site-header').isVisible(),true,'Phone shell shows a route title above the content');
  assert.equal(await page.locator('[data-page-title]').innerText(),'Today');
  await page.locator('.today-run-summary').waitFor();
  assert.equal(await page.locator('.today-title').innerText(), 'Your daily briefing');
  assert.equal(await page.locator('.today-run-meta').innerText(), '3 stories · about 2 min');
  assert.equal(await page.locator('.today-completion strong').innerText(), "You're caught up for today.");
  assert.equal(await page.locator('.today-completion span').innerText(), 'Next briefing tomorrow morning');
  const navIcons = await page.locator('.main-nav-icon').evaluateAll(icons=>icons.map(icon=>[icon.getBoundingClientRect().width,icon.getBoundingClientRect().height,getComputedStyle(icon).strokeWidth]));
  assert.deepEqual(navIcons,Array.from({length:4},()=>[24,24,'1.8px']),'Navigation icons share a 24px box and 1.8px stroke');
  const saveIcon = await page.locator('.story-bookmark svg').first().evaluate(icon=>[icon.getBoundingClientRect().width,icon.getBoundingClientRect().height,getComputedStyle(icon).strokeWidth]);
  assert.deepEqual(saveIcon,[24,24,'1.8px'],'Save icon matches the navigation outline system');
  assert.equal(await page.locator('.today-story-row').count(), 3);
  assert.deepEqual(await page.locator('.today-chapter-number').allTextContents(), ['1','2','3']);
  assert.doesNotMatch(await page.locator('.today-run-summary').innerText(), /sorry|apolog|regret|of 5 minutes/i);
  assert.deepEqual(await page.locator('.main-nav-label').allTextContents(), ['Today','On demand','Earlier','Topics']);
  const initialNav = await page.locator('.main-nav').boundingBox();
  assert.equal(await page.locator('.mini-player').count(),0,'Idle has no player bar');
  assert.equal(await page.locator('.today-listen-pill').innerText(),'Play · about 2 min');
  assert.equal(await page.locator('.today-listen-pill').getAttribute('aria-label'), "Play today's briefing, about 2 minutes");
  for(const theme of ['light','dark']){
   await page.emulateMedia({colorScheme:theme});
   await capture(page,`nav-header-today-${theme}`);
   await page.locator('[data-nav="on-demand"]').click(); await page.locator('.episode-request-page').waitFor();
   assert.equal(await page.locator('[data-page-title]').innerText(),'On demand','Header title follows the active route');
   await checkBottomTabs(page,'On demand');
   await capture(page,`nav-header-on-demand-${theme}`);
   await page.locator('[data-nav="today"]').click(); await page.locator('.today-page').waitFor();
  }
  await page.emulateMedia({colorScheme:'light'});
  const originalSections = edition.sections;
  const originalAudio = edition.audio;
  const originalRunSummary = structuredClone(statusFixture.daily_run.daily_briefing);
  edition.sections = Array.from({length:6},(_,i)=>({...originalSections[i%originalSections.length],id:`v3-six-story-${i}`,
   heading:`${headlines[i%headlines.length]} · update ${i+1}`,headline:`${headlines[i%headlines.length]} · update ${i+1}`}));
  edition.audio = {...originalAudio,duration_seconds:300};
  statusFixture.daily_run.daily_briefing = {...originalRunSummary,duration_seconds:300,spoken_words:750};
  await page.reload({waitUntil:'domcontentloaded'}); await page.locator('.today-story-row').nth(5).waitFor();
  assert.equal(await page.locator('.today-story-row').count(),6,'six-story edition renders each story row');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'six-story phone layout has no horizontal overflow');
  assert.equal(await page.locator('.inline-story-heading').evaluateAll(items=>items.every(item=>item.scrollWidth<=item.clientWidth && item.scrollHeight<=item.clientHeight)),true,'six-story phone headlines wrap without clipping');
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'})); await capture(page,'today-six-light');
  await page.evaluate(()=>scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'}));
  assert.equal(await page.locator('.today-story-row').last().evaluate(row=>row.getBoundingClientRect().bottom<=document.querySelector('.main-nav').getBoundingClientRect().top),true,'six-story end state clears the bottom navigation');
  await capture(page,'today-six-end-light'); await page.emulateMedia({colorScheme:'dark'}); await capture(page,'today-six-dark');
  await capture(page,'today-six-end-dark'); await page.emulateMedia({colorScheme:'light'});
  edition.sections = originalSections; edition.audio = originalAudio; statusFixture.daily_run.daily_briefing = originalRunSummary;
  await page.reload({waitUntil:'domcontentloaded'}); await page.locator('.today-story-row').first().waitFor();
  await page.locator('.today-transcript').first().locator('summary').click();
  assert.match(await page.locator('.story-lead').first().innerText(), /^U\.S\. payment network/);
  assert.equal(await page.locator('.today-page h1').innerText(), 'Your daily briefing');
  assert.deepEqual(await page.locator('.today-chapter-title').allTextContents(), headlines, 'chapter rows keep complete headlines');
  assert.equal(await page.locator('.today-page .episode-entry-prompt').count(),1);
  assert.equal(await page.locator('.today-page .episode-entry-prompt').innerText(), 'Brief me on something else\n→');
  assert.equal(await page.locator('.today-page .episode-entry-prompt').getAttribute('href'), '#ask-episode', 'The Today-to-On-demand link opens the On demand tab route');
  assert.doesNotMatch(editionRendererSource, /\b(?:No stories today|nothing today)\b/i, 'The Today renderer contains no rejected quiet-day copy');
  assert.doesNotMatch(await page.locator('body').innerText(), /\b(?:No stories today|nothing today)\b/i, 'The Today experience never suggests that a topic has no stories today');
  assert.equal(await page.locator('.today-completion strong').innerText(), "You're caught up for today.");
  assert.equal(await page.locator('.today-completion span').innerText(), 'Next briefing tomorrow morning');
  assert.equal(await page.locator('.today-chapter-title').count(),3);
  assert.equal(await page.locator('.today-quick-hits').count(),1,'Server-approved quick hits render on Today');
  assert.equal(await page.locator('.today-quick-hits li').count(),4,'Quick hits show three to five approved rows');
  assert.deepEqual(await page.locator('.today-quick-hits li a span').allTextContents(),['Cedar News','Harbor Journal','Northstar Review','River County']);
  assert.deepEqual(await page.locator('.today-quick-hits li a strong').allTextContents(),[
   'Cedar expands instant payment access','Harbor publishes enrollment guidance','Northstar reports AI pilot results','River County updates its transit plan']);
  assert.equal(await page.locator('.today-quick-hits li').evaluateAll(rows=>rows.every(row=>{
   const link=row.querySelector('a'); return link?.getAttribute('href') && link.children.length===2 && link.scrollWidth<=link.clientWidth
    && link.querySelector('strong').scrollWidth<=link.querySelector('strong').clientWidth && link.getBoundingClientRect().height<=44;
  })),true,'Fixture rows show the complete outlet and headline on one line with no summary or clipping');
  assert.doesNotMatch(await page.locator('.today-quick-hits').innerText(),/Unfiltered item|Another unfiltered item/,'Raw quick_hits are withheld');
  assert.equal(await page.locator('.today-page').evaluate(el => /What happened|Why it matters|Things to consider|Helpful\?|Read:/.test(el.innerText)),false);
  assert.deepEqual(await page.locator('.today-page').evaluate(el => [...el.children].map(x=>x.className).slice(0,5)), ['today-date','today-title','today-run-summary','today-primary','today-contents']);
  const chapterStyle = await page.locator('.today-chapter-title').evaluateAll(buttons=>buttons.map(button=>{const s=getComputedStyle(button);return {color:s.color,titleColor:getComputedStyle(document.querySelector('.today-title')).color,height:button.getBoundingClientRect().height};}));
  assert.ok(chapterStyle.every(item=>item.color===item.titleColor && item.height>=44),`Chapter titles use normal body colour and accessible targets: ${JSON.stringify(chapterStyle)}`);
  assert.match(await page.locator('.today-run-meta').innerText(), /^3 stories · about 2 min$/);
  assert.equal(await page.locator('.today-greeting').count(), 0, 'Today has no greeting paragraph or story-link wall');
  assert.equal(await page.locator('.today-chapter-title').first().getAttribute('aria-label'), `Play from chapter 1: ${headlines[0]}`);
  await page.locator('.today-transcript').evaluateAll(items=>items.forEach(details=>{details.open=true;}));
  const moreIconStyle = await page.locator('.story-more-toggle svg').first().evaluate(icon=>[icon.getBoundingClientRect().width,icon.getBoundingClientRect().height,getComputedStyle(icon).strokeWidth]);
  assert.deepEqual(moreIconStyle,[24,24,'1.8px'],'More disclosure uses the same icon box and stroke');
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  for (const row of await page.locator('.today-story-row').all()) {
   assert.ok((await row.boundingBox()).height <= 640,'Collapsed story and its visible source list stay within the fixture content bound');
   const headline = row.locator('.inline-story-heading');
   assert.equal(await headline.evaluate(el=>getComputedStyle(el).textOverflow),'clip');
   assert.equal(await headline.evaluate(el=>el.scrollHeight <= el.clientHeight),true);
   assert.equal(await row.locator('.story-more-toggle').getAttribute('aria-expanded'),'false');
  }
  for (const theme of ['light','dark']) {
   await page.emulateMedia({colorScheme:theme});
   await checkBottomTabs(page, 'Today');
   assert.deepEqual(await page.locator('.main-nav').boundingBox(), initialNav, 'Theme changes preserve bar geometry');
   await captureBottomBar(page, `bottom-bar-${theme}`);
   await page.evaluate(()=>scrollTo({top:0,behavior:'instant'})); await capture(page,`today-${theme}`);
   await checkContrast(page);
   const source = page.locator('.inline-story-sources button').first();
   assert.match(await source.innerText(), /Fictional Settlement Desk/u, 'Today source list names the publisher');
   assert.equal(await source.evaluate(el=>el.getBoundingClientRect().height >= 44), true, 'Today source links remain comfortably tappable');
   await page.locator('.today-story-row').nth(1).evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'})); await capture(page,`today-${theme}-2mid`);
   await page.evaluate(()=>scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'}));
   const bottomGap = await page.evaluate(() => {
    const link = document.querySelector('.episode-entry-prompt').getBoundingClientRect();
    const nav = document.querySelector('.main-nav').getBoundingClientRect();
    return nav.top - link.bottom;
   });
   assert.ok(bottomGap >= 14 && bottomGap <= 18, `Today finishes 16px above the tab bar; got ${bottomGap}px`);
   assert.ok(await page.locator('.main-nav').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0 && Math.abs(r.bottom-innerHeight)<2;})); await capture(page,`today-${theme}-3end`);
  }
  for (const [audioSeconds, estimatedSeconds, minutes] of [[120, 300, 2], [300, 300, 5], [480, 300, 8]]) {
    statusFixture.daily_run.daily_audio.duration_seconds = audioSeconds;
    statusFixture.daily_run.daily_briefing.duration_seconds = estimatedSeconds;
    statusFixture.daily_run.daily_briefing.spoken_words = Math.round(estimatedSeconds / 60 * 150);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.today-run-summary').waitFor();
    const label = `about ${minutes} min`;
    const ariaLabel = `Play today's briefing, about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    await page.waitForFunction(expected => document.querySelector('.today-run-meta')?.textContent === `3 stories · ${expected}`,
      label);
    assert.equal(await page.locator('.today-run-meta').innerText(), `3 stories · ${label}`,
      `Today uses the ${minutes}-minute recorded audio length over the script estimate`);
    assert.equal(await page.locator('.today-listen-pill').innerText(), `Play · ${label}`);
    assert.equal(await page.locator('.today-listen-pill').getAttribute('aria-label'),
      ariaLabel);
  }
  statusFixture.daily_run.daily_briefing.duration_seconds = 127;
  statusFixture.daily_run.daily_briefing.spoken_words = 318;
  statusFixture.daily_run.daily_audio.duration_seconds = 120;
  statusFixture.daily_run.daily_audio.ready_at = '2026-11-17T10:02:00Z';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('.today-run-summary').waitFor();
  await page.emulateMedia({colorScheme:'light'});
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  await page.locator('.today-transcript').first().locator('summary').click();
  const more=page.locator('.story-more-toggle').first(); await more.focus(); await page.keyboard.press('Enter'); assert.equal(await more.getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('.story-more').first().isVisible(),true); assert.equal(await page.locator('.inline-story-sources').first().locator('li').count(),2);
  await page.locator('.today-transcript').nth(1).locator('summary').click();
  await page.locator('.story-more-toggle').nth(1).click(); assert.equal(await page.locator('.inline-story-sources').nth(1).locator('li').count(),1);
  await page.emulateMedia({colorScheme:'dark'}); await checkContrast(page); await capture(page,'today-dark-expanded'); await page.emulateMedia({colorScheme:'light'});
  await more.click();
  await page.reload({waitUntil:'domcontentloaded'});
  await page.locator('.today-transcript').first().locator('summary').click();
  await page.locator('.today-transcript').nth(1).locator('summary').click();
  await page.locator('.story-more-toggle').nth(1).waitFor();
  assert.equal(await page.locator('.story-more-toggle').first().getAttribute('aria-expanded'),'false','collapsed story sections stay collapsed after reload');
  assert.equal(await page.locator('.story-more-toggle').nth(1).getAttribute('aria-expanded'),'true','expanded story sections stay open after reload');
  const savedSectionPreferences = await page.evaluate(() => {
   const edition = document.querySelector('.edition');
   const preferences = JSON.parse(localStorage.getItem('briefing:story-more-open') || '{}');
   return [...document.querySelectorAll('.story-more-toggle')].slice(0,2).map(toggle => {
    const sectionId = toggle.closest('.edition-section').id;
    const key = `${edition.dataset.edition}:${edition.dataset.revision}:${sectionId}`;
    return { key, expanded: toggle.getAttribute('aria-expanded') === 'true', saved: preferences[key] };
   });
  });
  assert.deepEqual(savedSectionPreferences.map(({expanded,saved}) => saved === expanded), [true,true],
   'Today section disclosure choices are saved per edition and story in this device’s local storage');
  const blockedReadState=await page.evaluate(async ({edition,sources})=>{
   const original=Storage.prototype.getItem;
   Storage.prototype.getItem=function(key){if(key==='briefing:story-more-open')throw new Error('storage unavailable');return original.call(this,key);};
   const {renderEdition}=await import('/src/edition/edition.js');const host=document.createElement('div');
   renderEdition(host,edition,[],{sectionId:edition.sections[0].id,inline:true,sources});
   const expanded=host.querySelector('.story-more-toggle').getAttribute('aria-expanded');
   host.remove();Storage.prototype.getItem=original;return expanded;
  },{edition,sources});
  assert.equal(blockedReadState,'false','story disclosure uses its default when local storage cannot be read');
  await page.evaluate(()=>{const original=Storage.prototype.setItem;window.__restoreStoryMoreSet=()=>{Storage.prototype.setItem=original;};Storage.prototype.setItem=function(key,value){if(key==='briefing:story-more-open')throw new Error('storage unavailable');return original.call(this,key,value);};});
  const storedMore=page.locator('.story-more-toggle').first();await storedMore.click();
  assert.equal(await storedMore.getAttribute('aria-expanded'),'true','story disclosure still opens when local storage cannot be written');
  await page.evaluate(()=>window.__restoreStoryMoreSet());
  await page.locator('.today-listen-pill').click(); await page.locator('.mini-player').waitFor();
  const playIconSizes = await page.locator('.mini-player-toggle svg').evaluate(icon=>[icon.getBoundingClientRect().width,icon.getBoundingClientRect().height]);
  assert.deepEqual(playIconSizes,[24,24],'Mini player playback icon uses the shared 24px box');
  assert.equal(await page.locator('.mini-player').count(),1);
  await page.waitForFunction(()=>{const m=document.querySelector('.mini-player').getBoundingClientRect(),n=document.querySelector('.main-nav').getBoundingClientRect();return Math.abs(m.bottom-n.top)<2;});
  const mini=await page.locator('.mini-player').boundingBox(), nav=await page.locator('.main-nav').boundingBox(); assert.ok(Math.abs(mini.y+mini.height-nav.y)<2);
  assert.deepEqual(nav, initialNav, 'Player appearance does not move the tab bar');
  await page.evaluate(()=>scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'}));
  const playerGeometry = await page.evaluate(() => {
   const main = document.querySelector('main').getBoundingClientRect(), link = document.querySelector('.episode-entry-prompt').getBoundingClientRect(), player = document.querySelector('.mini-player').getBoundingClientRect();
   return { gap: player.top - link.bottom, padding: getComputedStyle(document.querySelector('main')).paddingBottom, classes: document.body.className, linkBottom: link.bottom, playerTop: player.top, scroll: [scrollY, document.documentElement.scrollHeight], mainBottom: main.bottom };
  });
  assert.ok(playerGeometry.gap >= 14 && playerGeometry.gap <= 18, `Today leaves 16px above the mini player; got ${JSON.stringify(playerGeometry)}`);
  for (const theme of ['light','dark']) {
   await page.emulateMedia({colorScheme:theme}); await checkBottomTabs(page, 'Today');
   await captureBottomBar(page, `bottom-bar-player-${theme}`);
  }
  await page.emulateMedia({colorScheme:'light'});
  await capture(page,'today-playing'); await page.locator('.mini-player-toggle').click(); assert.equal(await page.locator('.mini-player').count(),0);
  await page.locator('.episode-entry-prompt').click(); await page.locator('.episode-request-page h1').waitFor(); assert.equal(await page.locator('.episode-request-page h1').innerText(),'Brief me on anything');
  assert.equal(await page.locator('[data-nav=on-demand]').getAttribute('aria-current'),'page', 'Today’s quiet link opens the On demand tab');
  await page.getByRole('navigation',{name:'Main'}).getByRole('link',{name:'Today'}).click(); await page.locator('.today-page').waitFor();
  await page.getByRole('navigation',{name:'Main'}).getByRole('link',{name:'On demand'}).click(); await page.locator('.episode-request-page h1').waitFor(); assert.equal(await page.locator('.episode-request-page h1').innerText(),'Brief me on anything');
  assert.equal(await page.locator('.episode-request-chip').count(),4); assert.ok(await page.locator('.episode-request-example').count()<=3);
  await capture(page,'ask-light'); await page.emulateMedia({colorScheme:'dark'}); await capture(page,'ask-dark'); await page.emulateMedia({colorScheme:'light'});
  await page.getByRole('button',{name:'5 min',exact:true}).click(); assert.equal(await page.locator('.episode-request-chip[aria-pressed="true"]').innerText(),'5 min');
  await page.getByRole('navigation',{name:'Main'}).getByRole('link',{name:'Today'}).click(); await page.getByRole('navigation',{name:'Main'}).getByRole('link',{name:'Earlier'}).click();
  assert.equal(await page.locator('main>h1').innerText(),'Earlier'); assert.deepEqual(await page.locator('.library-filters button').allTextContents(),['Editions','Episodes','Saved']);
  await checkBottomTabs(page, 'Earlier');
  assert.deepEqual(await page.locator('.main-nav').boundingBox(), initialNav, 'Navigation preserves bar geometry');
  await page.getByRole('button',{name:'Saved',exact:true}).click();
  assert.equal(await page.locator('.library-empty h2').innerText(),'Nothing saved yet');
  assert.equal(await page.locator('.library-empty p').innerText(),'Save a story or briefing to find it here.');
  await page.getByRole('button',{name:'Episodes',exact:true}).click(); await page.locator('.earlier-ask-link').waitFor();
  await page.getByRole('button',{name:'Editions',exact:true}).click();
  assert.equal(await page.locator('.library-briefing-list li').count(),4,'Earlier renders several dated editions from the mock history fixture');
  await capture(page,'earlier-light'); await page.emulateMedia({colorScheme:'dark'}); await capture(page,'earlier-dark'); await page.emulateMedia({colorScheme:'light'});
  await page.locator('[data-nav=topics]').click(); await page.locator('.topics-page').waitFor();
  await checkBottomTabs(page, 'Topics');
  assert.equal(await page.locator('.topics-heading h1').innerText(),'Your topics');
  for (const toggle of await page.getByRole('switch').all()) assert.ok((await toggle.boundingBox()).height>=44);
  assert.ok(await page.locator('.topics-skipping').count()<=1); await capture(page,'topics-light'); await page.emulateMedia({colorScheme:'dark'}); await capture(page,'topics-dark'); await page.emulateMedia({colorScheme:'light'});
  await page.setViewportSize({width:1440,height:1000}); await page.locator('[data-nav=today]').click(); await page.locator('.today-page').waitFor(); await page.waitForTimeout(200); await page.evaluate(()=>scrollTo({top:0,behavior:'instant'})); await page.waitForTimeout(150); await capture(page,'desktop-light'); await page.emulateMedia({colorScheme:'dark'}); await capture(page,'desktop-dark');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.evaluate(() => localStorage.setItem('briefing:v1:daily-time', '06:30'));
  await page.locator('[data-nav="on-demand"]').click(); await page.locator('.episode-request-page').waitFor();
  await page.locator('[data-nav="today"]').click(); await page.locator('.today-page').waitFor();
  assert.equal(await page.locator('.today-completion span').innerText(), 'Next briefing tomorrow morning', 'Today never exposes the internal build time');
  await page.evaluate(() => localStorage.setItem('briefing:v1:daily-time', '05:00'));
  await page.locator('[data-nav="on-demand"]').click(); await page.locator('.episode-request-page').waitFor();
  await page.locator('[data-nav="today"]').click(); await page.locator('.today-page').waitFor();
  assert.equal(await page.locator('.today-completion span').innerText(), 'Next briefing tomorrow morning');
  // Known duration, available narration with unknown duration, and no playback.
  for (const [available,duration,label] of [[true,180,'Listen · about 3 min'],[true,undefined,'Listen · about 1 min'],[false,180,null]]) {
   await page.evaluate(async ({edition,sources,available,duration})=>{const {renderToday}=await import('/src/edition/edition.js');renderToday(document.querySelector('main'),{...edition,audio:{duration_seconds:duration}},[],{sources,listeningAvailable:available});},{edition,sources,available,duration});
   if(label) assert.equal(await page.locator('.today-listen-pill').innerText(),label.replace('Listen', 'Play'));
   else assert.equal(await page.locator('.today-listen-pill').count(),0);
  }
  await page.evaluate(async ({edition,sources})=>{const {renderToday}=await import('/src/edition/edition.js');renderToday(document.querySelector('main'),{...edition,audio:{duration_seconds:180}},[],{sources,listeningAvailable:true,deferListenDuration:true});},{edition,sources});
  assert.equal(await page.locator('.today-listen-pill').innerText(),'Play','The explicit deferred state does not show a provisional player duration');
  assert.equal(await page.locator('.today-listen-pill').getAttribute('aria-label'),"Play today's briefing");
  // Thin days open the one story without a separate banner.
  await page.evaluate(()=>localStorage.removeItem('briefing:story-more-open'));
  await page.evaluate(async ({edition,sources})=>{ const {renderToday}=await import('/src/edition/edition.js'); renderToday(document.querySelector('main'),{...edition,sections:edition.sections.slice(0,1),thin_day:true,sources},[],{sources}); },{edition,sources});
  assert.equal(await page.locator('.today-story-row').count(),1); assert.equal(await page.locator('.story-more-toggle').getAttribute('aria-expanded'),'true');
  // The component caps long approved lists at five and suppresses lists below three.
  await page.evaluate(async () => { const {renderQuickHits}=await import('/src/edition/edition.js'); const host=document.createElement('section');host.dataset.testQuickHits='';document.querySelector('main').append(host);renderQuickHits(host,Array.from({length:6},(_,i)=>({title:`Cedar publishes update ${i}`,outlet:'Cedar News',url:`https://cedar.example.test/${i}`}))); });
  assert.equal(await page.locator('[data-test-quick-hits] li').count(),5,'Quick hits are capped at five rows');
  await page.evaluate(async () => {const {renderQuickHits}=await import('/src/edition/edition.js');renderQuickHits(document.querySelector('[data-test-quick-hits]'),[{title:'Only one approved item',outlet:'Cedar News',url:'https://cedar.example.test/one'},{title:'Only two approved items',outlet:'Cedar News',url:'https://cedar.example.test/two'}]);});
  assert.equal(await page.locator('[data-test-quick-hits]').count(),0,'Fewer than three approved items do not create a partial block');
  assert.deepEqual(errors,[]); console.log('V3 Today, pushed Ask, Earlier segments, Topics, source disclosure and single player pass.');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
