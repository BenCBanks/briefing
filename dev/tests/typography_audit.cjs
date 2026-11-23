require('../test_guard.cjs').enforceTestEnvironment();

// Shared editorial type scale on representative routes. All data is bundled fiction.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.BRIEFING_URL || 'http://127.0.0.1:8949';
const episodes = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/episodes.json'), 'utf8')).episodes;
const routes = [
  { name:'Today', hash:'#today', ready:'.today-title', sizes:[['.today-title',28],['.today-top-story-copy strong',15],['.story-lead p',17],['.today-date',13]] },
  { name:'Earlier', hash:'#library', ready:'main>h1', sizes:[['main>h1',28],['.library-briefing-link',17],['.library-date-meta',13]] },
  { name:'Topics', hash:'#topics', ready:'.topics-page h1', sizes:[['.topics-page h1',28],['.topic-label',18],['.topics-daily-summary',13]] },
  { name:'Ask', hash:'#ask-episode', ready:'.episode-request-page h1', sizes:[['.episode-request-page h1',28]] },
  { name:'Episode', hash:`?episodes=1#episode/${episodes[0].id}`, ready:'.episode-page h1', sizes:[['.episode-page h1',28],['.episode-asked p',17],['.episode-meta',13]] },
  { name:'Episode deck', hash:'?episodes=1#episodes', ready:'.episode-card h2', sizes:[['.episode-card h2',28]] },
  { name:'Story', hash:'#edition/daily/daily-sample-001/daily-payments-001', ready:'.edition h1', sizes:[['.edition h1',28],['.edition .edition-section>p',17]] },
  { name:'Weekly recap', hash:'#edition/weekly/weekly-sample-001', ready:'.edition h1', sizes:[['.edition h1',28]] },
  { name:'Explanation', hash:'#explanation/dive-payments-basics', ready:'.deep-dive-page h1', sizes:[['.deep-dive-page h1',28],['.deep-dive-meta',13]] },
  { name:'Topic detail', hash:'#topic-detail/payments', ready:'.topic-detail-header h1', sizes:[['.topic-detail-header h1',28]] },
  { name:'Source', hash:'#source/fed-payments', ready:'.source-title', sizes:[['.source-title',28],['.source-published',13]] },
  { name:'Corrections', hash:'#corrections', ready:'.corrections-page>h1', sizes:[['.corrections-page>h1',28]] },
  { name:'Settings', hash:'#preferences', ready:'main>h1', sizes:[['main>h1',28]] },
  { name:'Interests', hash:'#interests', ready:'.interests-page h1', sizes:[['.interests-page h1',28],['.interests-intro',17]] },
];

async function main() {
  const parsed = new URL(baseURL);
  assert.ok(['127.0.0.1','localhost'].includes(parsed.hostname), 'Typography audit uses a loopback preview');
  const browser = await chromium.launch({ args:['--no-sandbox'] });
  try {
    for (const width of [390,1280]) for (const colorScheme of ['light','dark']) for (const route of routes) {
      const page = await browser.newPage({ viewport:{width,height:844}, colorScheme, timezoneId:'America/New_York' });
      page.setDefaultTimeout(7000);
      await page.route('**/*', request => new URL(request.request().url()).origin === baseURL
        ? request.continue() : request.abort());
      for (const endpoint of ['/api/profile','/api/generate','/api/edition','/api/corrections','/api/status','/api/voice']) {
        await page.route(`**${endpoint}**`, request => request.fulfill({status:404,contentType:'application/json',body:'{}'}));
      }
      await page.route('**/api/episodes**', request => request.fulfill({json:{jobs:[]}}));
      await page.route('**/dev/tests/fixtures/episodes.json', request => request.fulfill({json:{episodes}}));
      await page.goto(`${baseURL}/${route.hash}`, {waitUntil:'domcontentloaded'});
      await page.locator(route.ready).first().waitFor();
      for (const [selector,expected] of route.sizes) {
        const locator = page.locator(selector).first();
        assert.equal(await locator.count(),1,`${route.name}: ${selector} exists`);
        const actual = await locator.evaluate(element => parseFloat(getComputedStyle(element).fontSize));
        assert.equal(actual,expected,`${route.name} ${selector} is ${expected}px at ${width}px in ${colorScheme} (got ${actual}px)`);
      }
      if (process.env.BRIEFING_SHOT_DIR) {
        fs.mkdirSync(process.env.BRIEFING_SHOT_DIR,{recursive:true});
        const slug=route.name.toLowerCase().replace(/[^a-z0-9]+/g,'-');
        await page.screenshot({animations:'disabled',path:path.join(process.env.BRIEFING_SHOT_DIR,`${slug}-${width}-${colorScheme}.png`)});
      }
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('Typography hierarchy: page titles, story headlines, ledes and metadata share the defined scale across fourteen routes, two themes and phone/desktop widths.');
}

main().catch(error=>{console.error(error);process.exitCode=1;});
