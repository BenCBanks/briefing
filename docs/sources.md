# Source registry coverage

This is an offline registry audit. The registry contains 274 feeds; this batch adds 40 feed candidates selected from Faro’s `great-sources-2026-11-17.md` ranked feed set and an explicitly disabled endpoint list. No feed or article was fetched while preparing this change.

## Coverage audit

| Area | Registry sources | Enabled | Notes |
| --- | ---: | ---: | --- |
| Payments and fintech | 46 | 42 | Trade reporting, official regulators, central banks and finance analysis. |
| Health policy | 20 | 10 | CMS, policy research and health-insurance reporting; two probation feeds are disabled pending reuse review. |
| AI | 15 | 13 | AI research, reporting and finance-adjacent analysis. |
| Housing | 70 | 70 | Existing local and national sources remain enabled. |
| Energy | 6 | 1 | DOE Buildings is enabled; new endpoints await Morrow validation. |
| Local news | 10 | 8 | WFAE, WCNC, NC Newsline, SC Daily Gazette and local government feeds. |
| Science | 11 | 6 | NASA, NOAA, ScienceDaily, Quanta and additional science coverage. |
| Sports business | 3 | 0 | Three new candidates; all disabled pending endpoint and rights validation. |

Topics can overlap: feeds tagged with multiple areas are counted once in each matching row. The `local-news` tag was added to existing North Carolina and Upstate South Carolina reporting; `energy` now also covers the existing DOE building feed.

## Rights, readability and cadence

Research-set entries retain Faro’s rights notes, measured feed-summary lengths and estimated items per week. `readability` is a summary-length band (`good` ≥250 characters, `usable` 80–249, `limited` under 80); `unverified` means the endpoint itself was not checked in the cited research. `yield_estimate.items_per_week` is Faro’s dated estimate, not a current measurement. `cadence` is a proposed polling interval, not a promise about how often a publisher posts. Every addition sets `allow_article_fetch: false`; enabled research sources are limited to headline and short feed-snippet use where the report explicitly recommends it. Entries with personal-use-only terms, blocked/unknown robots, or unclear endpoint/reuse status remain disabled.

The ten hand-curated energy, local and sports-business endpoints below were not fetched. They remain disabled with `robots_ok: unknown` and require Morrow’s live endpoint, robots, feed-shape and rights checks before use.

The offline rights audit requires every enabled source to have a reuse-status
note and a matching row in [`source-rights.md`](source-rights.md). Twelve enabled
sources missing a documentation row were added there with their existing
registry notes. `ncdhhs-news-feed` and `bpc-health` remain disabled until their
reuse terms are reviewed. The registry linter checks all enabled sources;
edition scorecard check 11 checks only sources actually cited by that edition,
so an unused feed cannot invalidate a story.

## Added feeds

| ID | Feed | Topics | Readability | Est. items/week | License note | Poll cadence | Enabled | URL |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `acasignups` | ACASignups.net | health | good | 5.1 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://acasignups.net/rss.xml` |
| `paymentsjournal` | Payments Journal | payments | good | 14.2 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.paymentsjournal.com/feed/` |
| `technologyreview` | MIT Technology Review | ai, digital | good | 13.8 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.technologyreview.com/feed/` |
| `therecord` | The Record (Recorded Future News) | security, privacy | usable | 26.3 | OBSERVED feed copyright: Copyright © 2026 / The Record from Recorded Future News; no reuse licence read | hourly-check | no | `https://therecord.media/feed` |
| `aisnakeoil` | AI Snake Oil | ai | limited | 0.2 | OBSERVED feed copyright: Sayash Kapoor and Arvind Narayanan; no reuse licence read | hourly-check | no | `https://www.aisnakeoil.com/feed` |
| `bair` | Berkeley AI Research blog | ai | good | 0.2 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://bair.berkeley.edu/blog/feed.xml` |
| `canadianrunning` | Canadian Running | running | usable | 19.8 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://runningmagazine.ca/feed/` |
| `cbs-soccer` | CBS Sports soccer | sports, premier-league | usable | 21.4 | OBSERVED feed copyright: CBS Interactive Inc.; no reuse licence read | hourly-check | no | `https://www.cbssports.com/rss/headlines/soccer/` |
| `cfodive` | CFO Dive | finance | usable | 23.0 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.cfodive.com/feeds/news/` |
| `dw` | Deutsche Welle | world | usable | 32.1 | OBSERVED feed copyright: 2026 DW; no reuse licence read | hourly-check | no | `https://rss.dw.com/xml/rss-en-all` |
| `fintechbizweekly` | Fintech Business Weekly (Jason Mikula) | fintech, banking, payments | usable | 2.1 | OBSERVED feed copyright: 312 Global Strategies, BV; no reuse licence read | hourly-check | no | `https://fintechbusinessweekly.substack.com/feed` |
| `fintechtakes` | Fintech Takes (Alex Johnson) | fintech, payments, banking | limited | 6.0 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.fintechtakes.com/feed` |
| `epic` | EPIC | privacy | good | 13.8 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://epic.org/feed/` |
| `importai` | Import AI | ai | limited | 1.0 | OBSERVED feed copyright: Jack Clark; no reuse licence read | hourly-check | no | `https://importai.substack.com/feed` |
| `irunfar` | iRunFar | running | usable | 8.7 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.irunfar.com/feed` |
| `kingarthur` | King Arthur Baking | cooking | good | 2.1 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.kingarthurbaking.com/blog/feed` |
| `knowable` | Knowable Magazine | science | usable | 2.2 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://knowablemagazine.org/rss` |
| `lithub` | Literary Hub | books | usable | 146.0 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://lithub.com/feed/` |
| `marginalian` | The Marginalian | books | good | 17.0 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.themarginalian.org/feed/` |
| `npr-health` | NPR health | health | usable | 23.3 | OBSERVED: feed copyright 'NPR - For Personal Use Only' | hourly-check | no | `https://feeds.npr.org/1128/rss.xml` |
| `npr-news` | NPR news | world, us | usable | 138.8 | OBSERVED: feed copyright 'NPR - For Personal Use Only' | hourly-check | no | `https://feeds.npr.org/1001/rss.xml` |
| `openai-news` | OpenAI news | ai | usable | 2.5 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://openai.com/news/rss.xml` |
| `pbs-newshour` | PBS NewsHour | world, us | good | 564.7 | OBSERVED feed copyright: NewsHour Productions LLC. All Rights Reserved. 2026; no reuse licence read | hourly-check | no | `https://www.pbs.org/newshour/feeds/rss/headlines` |
| `proton-blog` | Proton blog | privacy | usable | 8.9 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://proton.me/blog/feed` |
| `simonwillison` | Simon Willison | ai, digital | good | 17.0 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://simonwillison.net/atom/everything/` |
| `tearsheet` | Tearsheet | fintech, banking | good | 6.4 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://tearsheet.co/feed/` |
| `thehockeynews` | The Hockey News | sports, nhl | usable | 56.1 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://thehockeynews.com/feed` |
| `guardian-pl` | Guardian Premier League | sports, premier-league | good | 28.0 | OBSERVED: feed says all rights reserved; Open Platform terms not fetched | hourly-check | no | `https://www.theguardian.com/football/premierleague/rss` |
| `eia-today-in-energy` | EIA Today in Energy | energy | unverified | — | Official EIA feed endpoint candidate; validate URL and current item shape before enabling. | six-hour-check | no | `https://www.eia.gov/rss/todayinenergy.xml` |
| `utility-dive` | Utility Dive | energy, finance | unverified | — | Industry Dive feed candidate; headline and short RSS snippet only pending rights review. | six-hour-check | no | `https://www.utilitydive.com/feeds/news/` |
| `canary-media` | Canary Media | energy | unverified | — | Energy-transition reporting feed candidate; disabled pending feed and reuse validation. | six-hour-check | no | `https://www.canarymedia.com/rss` |
| `rmi-energy` | RMI Energy | energy | unverified | — | Research and analysis feed candidate; disabled pending feed and reuse validation. | six-hour-check | no | `https://rmi.org/feed/` |
| `nrel-news` | NREL News | energy, science | unverified | — | Official lab feed endpoint candidate; path may be dated; Morrow must verify before enabling. | six-hour-check | no | `https://www.nrel.gov/news/press/2019/rss.xml` |
| `front-office-sports` | Front Office Sports | sports-business, sports | unverified | — | Sports business feed candidate; disabled pending feed and reuse validation. | six-hour-check | no | `https://frontofficesports.com/feed/` |
| `sportico` | Sportico | sports-business, sports | unverified | — | Sports business feed candidate; disabled pending feed and reuse validation. | six-hour-check | no | `https://www.sportico.com/feed/` |
| `sports-business-journal` | Sports Business Journal | sports-business, sports | unverified | — | Sports business feed candidate; disabled pending feed and reuse validation. | six-hour-check | no | `https://www.sportsbusinessjournal.com/rss/` |
| `wunc-local` | WUNC local news | local-news, charlotte | unverified | — | North Carolina public media feed endpoint candidate; disabled pending endpoint validation. | six-hour-check | no | `https://www.wunc.org/feeds/` |
| `wsoc-local` | WSOC Charlotte local news | local-news, charlotte | unverified | — | Charlotte local-news feed endpoint candidate; disabled pending endpoint validation. | six-hour-check | no | `https://www.wsoctv.com/arc/outboundfeeds/rss/category/news/local/` |
| `gardenista` | Gardenista | gardening | good | 6.8 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.gardenista.com/feed/` |
| `justonecookbook` | Just One Cookbook | cooking | usable | 0.9 | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only | hourly-check | yes | `https://www.justonecookbook.com/feed/` |

## Morrow live-validation queue

Verify these disabled feed endpoints and allowed snippet use before enabling:
- `eia-today-in-energy` — EIA Today in Energy: `https://www.eia.gov/rss/todayinenergy.xml`
- `utility-dive` — Utility Dive: `https://www.utilitydive.com/feeds/news/`
- `canary-media` — Canary Media: `https://www.canarymedia.com/rss`
- `rmi-energy` — RMI Energy: `https://rmi.org/feed/`
- `nrel-news` — NREL News: `https://www.nrel.gov/news/press/2019/rss.xml`
- `front-office-sports` — Front Office Sports: `https://frontofficesports.com/feed/`
- `sportico` — Sportico: `https://www.sportico.com/feed/`
- `sports-business-journal` — Sports Business Journal: `https://www.sportsbusinessjournal.com/rss/`
- `wunc-local` — WUNC local news: `https://www.wunc.org/feeds/`
- `wsoc-local` — WSOC Charlotte local news: `https://www.wsoctv.com/arc/outboundfeeds/rss/category/news/local/`

The other disabled research-set additions remain off where Faro noted a paywall, personal-use-only terms, uncertain robots status or no confirmed snippet permission. The registry records the reason per source.

## Verification

```sh
python3 dev/tools/registry_lint.py
python3 -m unittest -q dev.tests.source_registry_coverage_test dev.tests.sources_yield_test
```

All checks are offline and use the checked-in registry and item fixtures.
