# Reading experience · owner-directed revision

Status: Draft for Ben's preview, 16 November 2026. This supersedes the screen structure in design-pass-006 after Ben said the whole UI and navigation felt clunky and confusing. Astra supplied this fresh read-only direction after reviewing the actual prototype. Ben's preview decides the final design.

## Product shape

The three obvious actions are **read today**, **understand a topic**, and **revisit an edition**. One daily edition remains a continuous cross-topic document. Optional explanations add depth without becoming separate feeds. Browser and iOS use the same destination map:

```text
Today → Edition → Explanation → Source
Topics → Explanation → Source
Library → Edition → Explanation → Source
```

Root tabs appear only on Today, Topics and Library. Readers and Source use a single contextual Back action in a sticky header. Visible Back, browser Back/Forward and iOS swipe-back traverse the immediate parent. Each entry retains root tab, content revision, scroll position/section and initiating control; returning restores position and keyboard focus. Direct edition links fall back to Today or Library as appropriate, and direct explanation links to Topics. Settings and the expanded player may be sheets; articles and sources are pages.

## Today front page

- Header: Today, Settings. Date and `Sample · Fictional content` above a 32px editorial title, brief introduction and accurate reading time/topic count.
- One primary action: `Read today's briefing`. Once begun, `Continue reading` with the current section is more useful. No long article paragraphs on Today.
- `In this edition` lists the actual story titles under topic labels and section numbers. A row enters the same edition at that section. Quiet/limited topics live in a compact `Other followed topics` disclosure and do not look like story links.
- Weekly recap is a secondary destination below the contents. No coverage modal, save/finish controls, or audio-status line on Today.
- Do not claim a 12-minute edition while the sample reads in eight minutes. Keep the displayed duration accurate until the content itself is expanded.

## Edition reader

- Sticky header: `‹ Today` or `‹ Library` and `Contents`. Contents opens a small navigational sheet of section titles; choosing one closes it and scrolls inside this same document.
- Title block: date, `Daily briefing`, title, reading time, and clear sample/fictitious disclosure. Keep the connecting introduction brief.
- Continuous stories with `01 / 05` and topic label, 24px heading and 18px/29px body. No swipe-only stories or cards. After a story, one row names its actual explanation question, then a `Sources (n)` disclosure with publisher/title links. A source always opens the shared Source page.
- Preserve material corrections near title and affected section. At the end, show `End of today's briefing`, `Back to Today`, secondary `Save edition`, and a link to the weekly recap. Reaching the end may record progress; do not make `Mark finished` a required task.

## Topics and explanation

- Topics is a flat explanation bookshelf: `Topics`, short description, secondary `Manage topics`, and one row per topic with label, question title and accurate reading time. No duplicate buttons, coverage status or ISO dates in the list. Topic management must not imply that a fixed sample edition regenerates.
- Explanation uses the reader's width, typography and sticky contextual Back: `‹ Briefing`, `‹ Topics`, or `‹ Library` according to its parent. Header is topic plus `Explanation`, question title, reading time and concise summary. An evergreen/background distinction and fictional caveat remain in one quiet note. Reviewed date belongs at the end. Sources use the same disclosure and detail page as edition sources.

## Source and Library

- Source is a full-height page in the same stack, with `‹ Briefing` or `‹ Explanation` at top, publisher, title, `Open original ↗` when a verified external URL exists, `Why this source`, `Limits`, and publication date/domain only when known. External links open a new browser tab or an in-app browser that returns to Source. Hide empty fields and technical labels such as Kind/Availability.
- Library has local `Editions` and `Saved` filters. Editions are date-led, newest first, with weekly recaps explicitly labelled. Saved may include editions and explanations labelled by type. Empty Saved says `Save an edition or explanation to find it here.` Add `Saved on this device` as a quiet note. Keep a root's scroll/filter state when switching tabs.

## Audio and visual system

- Until an approved recording exists, show no Play control, disabled control or `Audio pending approval` message in the reader. Browsing never changes an active recording. When approved audio exists, Today may add `Listen · [duration]`; a mini-player is the only persistent reader overlay. Player sections seek only with verified timing data.
- Near-white `#FAFAF8`, white reading surface, text `#202124`, secondary `#62676F`, action blue `#215DB0`; charcoal dark mode with off-white text and lighter blue links. No gradients or decorative cards.
- Georgia editorial titles on web and system serif on iOS. 32px Today title, 30px reader title, 24px section headings, 18px/29px reading body, 15–16px controls, 13px minimum metadata. Use 20px phone gutters and 4/8/12/16/24/32/48 spacing. Minimum 44px targets. Desktop root column about 760px, reader text about 640px.

## Demo and acceptance

The next Ben preview must demonstrate Today → Read → Explanation → Source → return to the same paragraph → end → weekly recap on both web and iOS. Check origin-specific Back paths from Topics and Library, browser Back/Forward/refresh, direct links, source focus, section scroll, root-tab preservation, light/dark, phone/desktop, large text, loading/error states, and no hidden final controls. Use only fictional content. Browser captures do not establish native or physical-iPhone behavior. Keep the PR Draft until Ben previews and approves the new screens.
