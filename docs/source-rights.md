# Source rights and ingestion rules

This is source-use documentation, not legal advice or a rights clearance. Before public launch, check each source’s robots.txt (including per-bot rules) and site terms. Record those checks here; they are not a test or release gate.

The `enabled` field records whether a feed is available for guarded retrieval. The runtime checks robots before every fetch, including per-bot rules. Entries with a known feed restriction or an AI crawler-disallow list stay disabled. Do not enable an entry whose feed is blocked, robots-disallowed, paywalled, or outside allowed terms. Do not bypass restrictions.

The registry preserves source restrictions and reuse notes per feed. Article access settings control ingestion only and do not grant permission to retain or republish article text. Extracted article text remains transient. Where reuse terms have not been checked, that uncertainty is explicit. Review the linked primary terms before relying on any permission.

## Per-source reuse notes

| ID | Publisher/feed | State | Reported reuse terms |
|---|---|---|---|
| `bea` | US Bureau of Economic Analysis | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `boe-news` | Bank of England news | enabled | UNVERIFIED: BoE terms not fetched (UK Crown/BoE copyright) |
| `cfpb-newsroom` | CFPB newsroom | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `cms-newsroom` | CMS newsroom | disabled; broken links| INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `fda-press` | FDA press releases | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `fdic-press` | FDIC press releases | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `fed-bcreg` | Federal Reserve Board banking and consumer regulation | enabled | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-enforcement` | Federal Reserve Board enforcement actions | enabled | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-monetary` | Federal Reserve Board monetary policy | enabled | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-press-all` | Federal Reserve Board press releases | enabled | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-speeches` | Federal Reserve Board speeches and testimony | enabled | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `ftc-consumer-blog` | FTC Consumer Blog | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `ftc-consumer-protection` | FTC consumer protection press releases | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `ftc-data-spotlight` | FTC Data Spotlight | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `ftc-press` | FTC press releases | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `nasa-breaking` | NASA breaking news | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `nist-news` | NIST news | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `occ-news` | OCC news releases | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `sec-press` | SEC press releases | enabled | OBSERVED: public information, may be copied, cite SEC (sec.gov/about/privacy-information) |
| `bbc-pl` | BBC Sport Premier League | disabled; AI-bot restrictions reported | OBSERVED: feed copyright '(C) British Broadcasting Corporation, see bbc.co.uk terms'; BBC feeds page states no reuse licence (fetched) |
| `cbs-nhl` | CBS Sports NHL | disabled; AI-bot restrictions reported | OBSERVED feed copyright: CBS Interactive Inc.; site terms not read |
| `espn-mlb` | ESPN MLB | disabled; AI-bot restrictions reported | OBSERVED (espn.com RSS FAQ): may display only content provided in the feed, must link to the espn.com URL in the feed and indicate ESPN as provider; no advertising alongside |
| `espn-nba` | ESPN NBA | disabled; AI-bot restrictions reported | OBSERVED (espn.com RSS FAQ): may display only content provided in the feed, must link to the espn.com URL in the feed and indicate ESPN as provider; no advertising alongside |
| `espn-nfl` | ESPN NFL | disabled; AI-bot restrictions reported | OBSERVED (espn.com RSS FAQ): may display only content provided in the feed, must link to the espn.com URL in the feed and indicate ESPN as provider; no advertising alongside |
| `espn-nhl` | ESPN NHL | disabled; AI-bot restrictions reported | OBSERVED (espn.com RSS FAQ): may display only content provided in the feed, must link to the espn.com URL in the feed and indicate ESPN as provider; no advertising alongside |
| `espn-soccer` | ESPN soccer | disabled; AI-bot restrictions reported | OBSERVED (espn.com RSS FAQ): may display only content provided in the feed, must link to the espn.com URL in the feed and indicate ESPN as provider; no advertising alongside |
| `guardian-mlb` | Guardian MLB | disabled; AI-bot restrictions reported | OBSERVED: feed copyright 'Guardian News and Media Limited ... All rights reserved'; Open Platform terms not fetched |
| `guardian-nba` | Guardian NBA | disabled; AI-bot restrictions reported | OBSERVED: feed copyright 'Guardian News and Media Limited ... All rights reserved'; Open Platform terms not fetched |
| `guardian-nfl` | Guardian NFL | disabled; AI-bot restrictions reported | OBSERVED: feed copyright 'Guardian News and Media Limited ... All rights reserved'; Open Platform terms not fetched |
| `guardian-nhl` | Guardian NHL | disabled; AI-bot restrictions reported | OBSERVED: feed copyright 'Guardian News and Media Limited ... All rights reserved'; Open Platform terms not fetched |
| `aba-banking` | ABA Banking Journal | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `acasignups` | ACASignups.net | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `aljazeera` | Al Jazeera | disabled; AI-bot restrictions reported | OBSERVED feed copyright: © 2026 Al Jazeera Media Network; site terms not read |
| `apple-newsroom` | Apple Newsroom | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `ars` | Ars Technica | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `bair` | Berkeley AI Research blog | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `banking-dive` | Banking Dive | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `bleeping` | BleepingComputer | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `bonappetit` | Bon Appetit | disabled; AI-bot restrictions reported | OBSERVED feed copyright: © Condé Nast 2026; site terms not read |
| `calculated-risk` | Calculated Risk | enabled | OBSERVED feed copyright: CalculatedRisk; site terms not read |
| `canadianrunning` | Canadian Running | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `cbs-news` | CBS News | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `cfodive` | CFO Dive | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `cloudflare-blog` | Cloudflare blog | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `cnbc-finance` | CNBC finance | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `cnbc-top` | CNBC top news | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `cointelegraph` | Cointelegraph | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `darkreading` | Dark Reading | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `digitaltransactions` | Digital Transactions | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `eff` | EFF updates | enabled | OBSERVED: EFF site footer 'Copyright (CC BY)' |
| `epic` | EPIC | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `epicgardening` | Epic Gardening | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `eurogamer` | Eurogamer | disabled; AI-bot restrictions reported | OBSERVED feed copyright: Copyright (c) Eurogamer.net a brand of IGN Entertainment, Inc.; site terms not read |
| `fierce-healthcare` | Fierce Healthcare | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `finovate` | Finovate | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `fintechtakes` | Fintech Takes (Alex Johnson) | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `fintech-times` | The Fintech Times | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `gamespot` | GameSpot | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `gardenista` | Gardenista | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `google-security` | Google Security Blog | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `guardian-business` | Guardian business | disabled; AI-bot restrictions reported | OBSERVED: feed copyright 'Guardian News and Media Limited ... All rights reserved'; Open Platform terms not fetched |
| `hackernews-thn` | The Hacker News | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `health-affairs` | Health Affairs | disabled; AI-bot restrictions reported | OBSERVED feed copyright: © 2026 Health Affairs Publishing, LLC; site terms not read |
| `healthcare-dive` | Healthcare Dive | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `healthinsurance-org` | Healthinsurance.org | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `healthsystemtracker` | Peterson-KFF Health System Tracker | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `hoopsrumors` | Hoops Rumors | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `ign` | IGN | disabled; AI-bot restrictions reported | OBSERVED feed copyright: Copyright (c) IGN Entertainment Inc., a Ziff Davis company; site terms not read |
| `insurancejournal` | Insurance Journal | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `irunfar` | iRunFar | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `kff` | KFF | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `kff-health-news` | KFF Health News | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `krebs` | Krebs on Security | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `malwarebytes` | Malwarebytes Labs | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `minimalistbaker` | Minimalist Baker | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `mlbtraderumors` | MLB Trade Rumors | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `nerdwallet` | NerdWallet | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `newscientist` | New Scientist | disabled; metered paywall| UNVERIFIED: no copyright text in feed; site terms not read |
| `nintendolife` | Nintendo Life | enabled | OBSERVED feed copyright: (c) 2026 Hookshot Media; site terms not read |
| `nomadicmatt` | Nomadic Matt | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `payments-dive` | Payments Dive | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `pft` | ProFootballTalk | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `physorg` | Phys.org | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `quanta` | Quanta Magazine | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `realgm` | RealGM NBA wiretap | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `schneier` | Schneier on Security | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `sciencedaily` | ScienceDaily | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `sky-sports` | Sky Sports football | disabled; AI-bot restrictions reported | OBSERVED feed copyright: Copyright 2026, BSKYB. All Rights Reserved.; site terms not read |
| `smittenkitchen` | Smitten Kitchen | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `sportsnet` | Sportsnet | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `state-travel` | US State Dept travel advisories | enabled | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on that host |
| `steam-news` | Steam news | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `techcrunch` | TechCrunch | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `techcrunch-fintech` | TechCrunch fintech | disabled; AI-bot restrictions reported | UNVERIFIED: no copyright text in feed; site terms not read |
| `thepointsguy-deals` | Upgraded Points | disabled; mislabeled feed| UNVERIFIED: no copyright text in feed; site terms not read |
| `troyhunt` | Troy Hunt | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `umn-extension` | University of Minnesota Extension | enabled | UNVERIFIED: no copyright text in feed; site terms not read |
| `wired` | WIRED | disabled; AI-bot restrictions reported | OBSERVED feed copyright: © Condé Nast 2026; site terms not read |
| `yahoo-finance-headline` | Yahoo Finance headlines | disabled; AI-bot restrictions reported | OBSERVED feed copyright: Copyright (c) 2026 Yahoo Inc. All rights reserved.; site terms not read |
| `hn-top` | Hacker News Firebase top stories | disabled; robots not verified allowed | UNVERIFIED: no copyright text in feed; site terms not read |
| `hn-algolia` | Hacker News Algolia front page | disabled; robots not verified allowed | UNVERIFIED: no copyright text in feed; site terms not read |
| `wikipedia-feed` | Wikipedia featured feed | disabled; fixed date| UNVERIFIED: no copyright text in feed; site terms not read |
| `federalregister` | Federal Register documents API | disabled; robots not verified allowed | OBSERVED: Board site is public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fiscaldata` | Treasury FiscalData API | disabled; robots not verified allowed | INFERRED: US Treasury open data |
| `worldbank` | World Bank API | disabled; robots not verified allowed | UNVERIFIED: World Bank open data terms not fetched |
| `frankfurter` | Frankfurter FX rates (ECB reference) | disabled; replace with ECB| UNVERIFIED: no copyright text in feed; site terms not read |
| `openfda` | openFDA drug enforcement | disabled; robots not verified allowed | UNVERIFIED terms (openFDA) |

## Additional enabled source reuse notes

These registry entries were enabled with headline, short-snippet and link-only
handling. The reuse note remains explicitly unverified; it is not evidence that
full article text may be fetched or stored. The registry linter requires every
enabled entry to appear here, while edition check 11 evaluates only sources
cited by that edition.

| ID | Publisher/feed | State | Reported reuse terms |
|---|---|---|---|
| `paymentsjournal` | Payments Journal | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `technologyreview` | MIT Technology Review | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `kingarthur` | King Arthur Baking | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `knowable` | Knowable Magazine | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `lithub` | Literary Hub | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `marginalian` | The Marginalian | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `openai-news` | OpenAI news | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `proton-blog` | Proton blog | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `simonwillison` | Simon Willison | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `tearsheet` | Tearsheet | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `thehockeynews` | The Hockey News | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `justonecookbook` | Just One Cookbook | enabled | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |

## Curated evergreen background references

| ID | Source and allowed use |
|---|---|
| `fed-payments` | Federal Reserve payment systems reference; public-domain Board material, attribute and link the Board. Used only for labelled background context. |
| `fdic-deposit-insurance` | FDIC deposit insurance guide; attribute and link FDIC. General U.S. explainer context only. |
| `nist-digital-identity` | NIST digital identity guidance; attribute and link NIST. Technical guidance, not a security claim about a named product. |
| `naic-insurance-basics` | NAIC consumer guide; terms not independently verified. Attribute and link; use only for labelled general context. |
| `fifa-football-rules` | IFAB Laws of the Game; terms not independently verified. Attribute and link; never use as current match reporting. |

These are curated references already used by `content/deep-dives.json`; they are not fetched during news ingestion. Generated context sentences are paraphrased from the reviewed evergreen guide summaries, receive their own numbered citation, and are visibly labelled `Background:`.

## Global constraints from source review

- ESPN RSS: display only feed-provided content, link to the ESPN URL supplied by the feed, credit ESPN, and place no advertising alongside. The writer must not rewrite or augment an ESPN-only item.
- Federal Reserve Board, SEC, and ECB: attribute the publisher; for ECB content also identify modifications as its terms require.
- Wikipedia: only use for evergreen explainers after source review. Store the page title, revision ID and URL; show “Source: Wikipedia, CC BY-SA 4.0” with a link, say when text was adapted, and share adapted text under CC BY-SA 4.0. The explainer is marked share-alike.
- Wikidata: Structured data is under CC0; retain an entity link for provenance.
- Sports sources: headline and link only; do not generate a summary.
- GDELT: headlines and URLs only, cite and link GDELT, and permit no more than one request per five seconds.
- Skip NWS because its User-Agent requires personal contact details. User-Agent is the non-personal `BriefingBot/1.0`.
- Ignore ESPN unofficial site APIs and the NBA CDN.
- Feed metadata retained locally is limited to title, URL, published date, snippet (at most 280 characters), fetched date, and topic tags. Article text may be fetched only transiently when allowed; never persist or redisplay extracted article text.
- Stored feed records expire after 30 days. Keep source attribution and links visible in generated stories. Never bypass paywalls, robots exclusions, access controls, or rate limits.

## Approved explainer APIs

These explainer endpoints are separate from enabled news feeds and stay disabled in the feed-fetch job until a topic explainer requests them. Their registry rows identify `source_class: explainer`, set a one-request-per-second minimum, and use `BriefingBot/1.0` without personal details.

| ID | Class | Reuse and attribution |
|---|---|---|
| `wikipedia-explainer` | Explainer · share-alike | CC BY-SA 4.0. Store title, revision ID and URL; label “Source: Wikipedia, CC BY-SA 4.0”; include adaptation date; adapted text must retain share-alike status. |
| `wikidata-explainer` | Explainer · CC0 | Structured data is CC0. Keep the entity URL for provenance. |

Source evidence: feed review, 16 November 2026.

## New tier-A sources from the 17 November source review

These entries were imported only when the report marked them as valid RSS/Atom feeds and `robots_ok: true`. Existing AI-crawler restrictions remain disabled. `full-text-safe` means the source review reported explicit public-domain, CC0 or reuse permission; other news sources contribute feed headline/snippet only. Sports remains headline and link only. Rights-reserved article bodies are never stored or redisplayed.


| ID | Publisher/feed | Enabled | Content depth | Reported reuse terms |
|---|---|---:|---|---|
| `arxiv-csai` | arXiv cs.AI | yes | snippet | OBSERVED: metadata CC0; PDFs not to be stored/served; 1 request per 3 s |
| `arxiv-cscr` | arXiv cs.CR security | yes | snippet | OBSERVED: metadata CC0; PDFs not to be stored/served; 1 request per 3 s |
| `arxiv-econgn` | arXiv econ.GN | yes | snippet | OBSERVED: metadata CC0; PDFs not to be stored/served; 1 request per 3 s |
| `arxiv-qfin-gn` | arXiv q-fin.GN general finance | yes | snippet | OBSERVED: metadata CC0; PDFs not to be stored/served; 1 request per 3 s |
| `astonvilla-news` | Aston Villa news | yes | headline | OBSERVED feed copyright: Copyright© 2026; no reuse licence read |
| `atlanta-gdpnow` | Atlanta Fed GDPNow | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `atlanta-headlines` | Atlanta Fed headlines | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `atlanta-takeonpayments` | Atlanta Fed Take on Payments | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `atlanta-wps` | Atlanta Fed working papers | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `bankofcanada-home` | Bank of Canada | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bankunderground` | Bank Underground (BoE staff blog) | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bis-fsi` | BIS FSI publications | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bis-mgmt-speeches` | BIS management speeches | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bis-press` | BIS press releases | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bis-wp` | BIS working papers | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bls-latest` | BLS latest numbers | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `boe-bank-insights` | Bank of England Bank Overground / insights | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `boe-news` | Bank of England news | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `boe-publications` | Bank of England publications | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `boe-speeches` | Bank of England speeches | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `boe-statistics` | Bank of England statistics | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `cfpb-newsroom` | CFPB newsroom | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `cisa-alerts` | CISA alerts | yes | snippet | INFERRED: US federal work; KEV licence file exists (not read) |
| `dallas-fed-main` | Dallas Fed all updates | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `ecb-blog` | ECB blog | yes | full-text-safe | OBSERVED: free reuse if ECB cited, accurate, modifications stated (ecb.europa.eu disclaimer) |
| `ecb-press` | ECB press | yes | full-text-safe | OBSERVED: free reuse if ECB cited, accurate, modifications stated (ecb.europa.eu disclaimer) |
| `ecb-pub` | ECB publications | yes | full-text-safe | OBSERVED: free reuse if ECB cited, accurate, modifications stated (ecb.europa.eu disclaimer) |
| `ecb-rss-index` | ECB RSS (euro area) | yes | full-text-safe | OBSERVED: free reuse if ECB cited, accurate, modifications stated (ecb.europa.eu disclaimer) |
| `everton-news` | Everton news | yes | headline | OBSERVED feed copyright: Copyright© 2026; no reuse licence read |
| `fca-news` | FCA news | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `fda-press` | FDA press releases | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `fdic-press` | FDIC press releases | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `fed-bcreg` | Federal Reserve Board banking and consumer regulation | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-feds-notes` | Federal Reserve FEDS Notes | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-g19` | Fed consumer credit G.19 | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-other` | Federal Reserve Board other announcements | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-press-all` | Federal Reserve Board press releases | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-speeches` | Federal Reserve Board speeches and testimony | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fed-working-papers` | Federal Reserve Board working papers | yes | full-text-safe | OBSERVED: Board content public domain, cite the Board (federalreserve.gov/disclaimer.htm) |
| `fedreg-rss-cms` | Federal Register RSS: CMS | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `fincen-news` | FinCEN news (autodiscovered) | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `ftc-consumer-blog` | FTC Consumer Blog | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `ftc-consumer-protection` | FTC consumer protection press releases | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `ftc-press` | FTC press releases | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `georgetown-chir` | Georgetown CHIR (Health Policy Matters) | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `healthsystemtracker` | Peterson-KFF Health System Tracker | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `kff` | KFF | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `kff-health-news` | KFF Health News | yes | snippet | UNVERIFIED: believed CC BY-ND republishing licence; republish page 404 today |
| `libertystreet` | NY Fed Liberty Street Economics | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `mastercard-pr-q4` | Mastercard IR press (Q4 pattern) | yes | snippet | OBSERVED feed copyright: Copyright Q4 Inc. All rights reserved.; no reuse licence read |
| `nacha-rss` | Nacha RSS | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `nasa-news` | NASA news | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `nber-new` | NBER new working papers | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `noaa` | NOAA news | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `occ-news` | OCC news releases | yes | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `paypal-news` | PayPal newsroom | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `paypal-pr-q4` | PayPal IR press (Q4 pattern) | yes | snippet | OBSERVED feed copyright: Copyright Q4 Inc. All rights reserved.; no reuse licence read |
| `pcissc-blog` | PCI Security Standards Council blog | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `projectzero` | Google Project Zero | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `quanta` | Quanta Magazine | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `sans-isc` | SANS Internet Storm Center | yes | snippet | OBSERVED feed copyright: (C) SANS Institute 2026; no reuse licence read |
| `sec-edgar-fiserv-8k` | SEC EDGAR Fiserv filings (atom) | yes | full-text-safe | OBSERVED: SEC content public, cite SEC; 10 requests/s cap; no logos (sec.gov/about/privacy-information) |
| `sec-edgar-mastercard-8k` | SEC EDGAR Mastercard filings (atom) | yes | full-text-safe | OBSERVED: SEC content public, cite SEC; 10 requests/s cap; no logos (sec.gov/about/privacy-information) |
| `stlouis-blog` | St. Louis Fed blog entries | yes | snippet | SECONDARY: Reserve Bank legal notices apply (Atlanta Fed says feeds free to use subject to legal notices); not public domain by default; read each notice |
| `stripe-blog` | Stripe blog | yes | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |

## Weekly watched pages

These pages did not have a validated eligible feed in the report/source check. They are checked no more than weekly. Only a title, a snippet capped at 280 characters, a content hash and the fetch timestamp are retained; the article/page body is transient, never stored, and dates are not inferred from fetch time.

| ID | Page | State | Reuse and handling |
|---|---|---|---|
## Manually validated probation feeds

These feeds are disabled and excluded from retrieval until reuse terms and stable source behavior are established. They are not eligible for headline cards or generated edition writing during the disabled period.

| ID | Publisher | Registry status | Reason | Rights note |
|---|---|---:|---|---|
| `bpc-health` | Bipartisan Policy Center feed | disabled | reuse review pending | UNKNOWN: feed terms were not checked; headline-only probation. |

Gemini may propose publisher names and official homepages for a profile topic with `python3 -m dev.ingest.discover --topic "health insurance" --topic-id health --dry-run`. Its output is only a candidate list: the application fetches the homepage/feed over checked HTTPS, checks robots and redirects, validates dates and items, samples accessible pages only when allowed, and scores the feed. Gemini-proposed candidates have unknown rights by default, are headline-only and cannot score above probation until rights are independently verified. No model-proposed feed URL is trusted.

The weekly federal rates watch stores bounded table-cell values alongside the page title, short snippet, URL and content digest. It does not save the HTML page body. Optional reader-selected watches and probation feeds are defined only in the configured external file.

## Housing feeds from source review’s 17 November report

Eligible tier A/B feeds are enabled only where the report verified HTTPS RSS/Atom/JSON and robots access. All are snippet-only except local TV feeds, which are headline-only and filtered by housing-related title/snippet keywords before storage. Article fetch is off. Rights uncertainty remains explicit; the scorecard must treat unknown rights as unknown.

| ID | Publisher/feed | Report depth | Reuse terms reported by source review |
|---|---|---|---|
| `freddiemac-research` | Freddie Mac research | snippet | OBSERVED (PMMS page): may be used with proper attribution; alteration prohibited; other pages' terms not read |
| `hud-press` | HUD press releases | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `nahb-eyeonhousing` | NAHB Eye on Housing | snippet | OBSERVED: no reuse or copyright statement on Eye on Housing page; assume headline, snippet and link |
| `nahbnow` | NAHBNow | snippet | OBSERVED: no reuse or copyright statement on Eye on Housing page; assume headline, snippet and link |
| `terner-feed` | Terner Center feed | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `architectmag` | Architect Magazine | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `archpaper` | The Architect's Newspaper | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `cityobservatory` | City Observatory | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `connectcre` | Connect CRE | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `enr-rss` | ENR feed | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `forestry-lumber` | Forisk / lumber | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `housingfinance` | Affordable Housing Finance | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `jlconline` | Journal of Light Construction | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `pew-stateline` | Stateline | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `realtor-research` | Realtor.com Research | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `redfin-news` | Redfin News | snippet | OBSERVED: Redfin data page states no terms; terms pages not read |
| `shelterforce` | Shelterforce | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `census-nrc-xml` | Census economic indicators feed | snippet | INFERRED: US federal work; no reuse statement on NRC page (observed) |
| `doe-buildings` | DOE Building Technologies | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `fedreg-hud` | Federal Register: HUD | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `nlihc-rss` | NLIHC feed | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `aei-housing` | AEI Housing Center | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `attom` | ATTOM data | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `constructiondive` | Construction Dive | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `constructionexec` | Construction Executive | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `curbed` | Curbed | snippet | OBSERVED feed copyright: 2026; no reuse licence read |
| `governing` | Governing | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `greenbuildingadvisor-feed` | Green Building Advisor feed | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `housingwire` | HousingWire | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `madisons` | Madison's Lumber Reporter | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `mortgagenewsdaily` | Mortgage News Daily | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `nextcity-home` | Next City home | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `probuilder` | Professional Builder | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `proremodeler` | Pro Remodeler | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `strongtowns` | Strong Towns | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `zonda` | Zonda | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `habitat-news` | Habitat for Humanity newsroom | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `agc-news` | AGC news | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `atlanta-crem` | Atlanta Fed commercial real estate index | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `atlanta-hoam` | Atlanta Fed Home Ownership Affordability Monitor | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |
| `bls-ppi` | BLS producer price index feed | snippet | INFERRED: US federal work, generally public domain (17 USC 105); no statement fetched on this host |
| `cnbc-realestate` | CNBC real estate | snippet | UNVERIFIED: no reuse statement seen; use headline, short snippet and link only |

## Weekly housing data watches

Zillow CSVs are checked at most weekly, only after a fresh robots.txt check allows the exact host. Their CSV bodies are parsed in memory; only the latest selected regional value, observation date, attribution note, title, URL, fetch time, and digest are retained. As of the issue #24 audit, FRED CSV watches are blocked: they perform no network request and keep only a check timestamp and `transient_only` status, removing previously retained fields for the configured watch when it next runs. The new FRED domain adapter is live link discovery only; observations and model evidence are prohibited. See [the statistics rights audit](retrieval.md#statistics-and-fred-rights-audit). Zillow terms remain unverified, so values are clearly attributed and are not rehosted as downloadable datasets. The historical FRED/Census/BLS and Freddie Mac notes below are attribution context, not retention or model-use permission; all five FRED watches are blocked. If robots blocks a host, the watch records the block and stores no data.

| ID | Dataset | Cadence | Rights/attribution |
|---|---|---|---|

## Recently verified feed reuse notes

| ID | Publisher/feed | State | Reported reuse terms |
|---|---|---|---|
| `statnews` | STAT News | enabled | UNVERIFIED: terms and reuse permission were not established; headline/snippet-only permission not confirmed. |
| `utility-dive` | Utility Dive | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `canary-media` | Canary Media | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `arxiv-qfin-cp` | arXiv quantitative finance: computational finance | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `arxiv-qfin-rm` | arXiv quantitative finance: risk management | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `arxiv-qfin-tr` | arXiv quantitative finance: trading | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `google-ai-news` | Google AI news | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `finra-news` | FINRA news | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `ecb-working-papers` | ECB working papers | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `nvidia-blog` | NVIDIA blog | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `nvidia-technical-blog` | NVIDIA technical blog | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `arm-newsroom` | Arm newsroom | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `micron-press` | Micron press releases | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `ieee-spectrum-semiconductors` | IEEE Spectrum semiconductors | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `ieee-spectrum-computing` | IEEE Spectrum computing | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `semiconductor-engineering` | Semiconductor Engineering | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `ee-times` | EE Times | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `semiwiki` | SemiWiki | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `servethehome` | ServeTheHome | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `cset-chips` | CSET chips policy | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `fedreg-bis` | Federal Register: BIS export controls | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `toms-hardware` | Tom's Hardware | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `fsb-feed` | Financial Stability Board | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `medpac-feed` | MedPAC publications | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `fedreg-epa` | Federal Register: EPA rules | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `carbon-brief` | Carbon Brief | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `inside-climate-news` | Inside Climate News | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `grist-climate` | Grist climate | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `clean-energy-wire` | Clean Energy Wire | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `our-world-data` | Our World in Data | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `yale-climate` | Yale Climate Connections | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `nasa-science` | NASA science | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `nasa-space-station` | NASA Space Station blog | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `jpl-news` | JPL news | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `esa-top-news` | ESA top news | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `nasa-oig` | NASA Office of Inspector General | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `space-news` | SpaceNews | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `payload-space` | Payload | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `space-policy-online` | SpacePolicyOnline | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `spaceflight-now` | Spaceflight Now | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
| `via-satellite` | Via Satellite | enabled | UNVERIFIED: use a short headline and snippet with a link; publisher terms need review. |
