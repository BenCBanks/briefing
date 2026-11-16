# Design pass 001 · minimal reader and dependable audio

**Status:** Astra/Lumen advice, 16 November 2026; Ben's current direction is authoritative. The first browser preview was rejected for its UI, voice and Play/Pause behavior. Build an iOS app and keep the same content accessible on the web. Do not merge the new screens until Ben previews them.

## Visual system

The product is a minimal news reader, with one complete daily edition. Use native SwiftUI on iPhone and equivalent responsive web layouts. Avoid a brand masthead, decorative hero, large serif title, coverage card and repeated prototype notices. Keep fictional content labeled once near the date.

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#111113` |
| Main text | `#18181B` | `#F4F4F5` |
| Secondary text | `#62626A` | `#A1A1AA` |
| Separator | `#E5E5EA` | `#303036` |
| Action | `#175CD3` | `#8AB4FF` |

Use system typography. Page title 28 pt bold, article headline 26 pt semibold, section heading 21 pt semibold, body 18 pt with about 28 pt line height, metadata 14 pt, controls 17 pt semibold. Let text grow with Dynamic Type and browser zoom. Phone horizontal margin 20 pt; paragraph gap 16 pt; section gap 28 pt. Interactive targets are at least 44 pt. Keep player and tab bar clear of article content and safe areas.

## Today and weekly reader

Today has a navigation title, Settings, date and a compact `Sample · Fictional content` label. Follow with `Today's briefing`, a short descriptive headline, measured audio duration, estimated reading time and the primary `Play briefing` control. That control becomes `Pause`, `Resume · m:ss`, or `Replay` from one player state. Show a quieter `Continue reading` when a reading anchor exists. The article begins in the first 390 × 844 screen at default text size.

Give one sentence of overview. A row `5 topics · View coverage` opens a sheet listing each selected topic's Update, Context, Quiet today or Coverage limited state, reason and `Explore [topic]`. Material limits and corrections also appear directly in the article. Present continuous sections with source references and topical deeper links. Finish with `You're up to date for this edition`, `Mark finished` and `Save briefing`; one secondary row links to the weekly recap.

Weekly reuses the reader: `Your week in perspective`, date range, measured duration, `Play recap`, cross-topic sections, coverage sheet and a clear finish. A partial week says so; an empty week gives the actual next availability if known.

Topics is a plain list. Each row has the topic, its explanatory question, measured audio duration and review date. Detail has a back label matching its origin, the question, audio/review metadata, `Play explanation`, `Save`, scope sentence, article, sources and return to the daily edition. Opening a deep dive preserves the daily reading and listening places and does not switch audio.

## Player contract

Use a recorded, script-matched audio file. Never switch to browser speech silently. The iOS player uses `AVPlayer`; the web uses `HTMLAudioElement`. Both expose one source of truth for current item, status, duration, position and speed. Page and player controls must agree.

Once an item is selected, a compact mini-player appears above native tabs or mobile web navigation. It shows title, elapsed/duration, one 44 pt Play/Pause control and thin progress. Tapping the title opens a native sheet or accessible web dialog; tapping Play/Pause only controls playback. The expanded player has Minimize, item and chapter, seek slider, elapsed/remaining, Back 15 seconds, large Play/Pause, Forward 15 seconds, speed choices (0.75×, 1×, 1.25×, 1.5×, 2×), Chapters and Read transcript. No fake artwork or waveform.

Pause freezes the actual timestamp. Seeking a paused item leaves it paused. Save position every five seconds and on pause, item switch and app lifecycle transition. Save reading anchors separately by content revision. Only one item plays. Browsing never changes audio; explicit item change stops/saves the first. Completion stops, with no autoplay. Buffering says `Loading audio…` and can be cancelled. Failure says `Audio couldn't load · Retry`. Superseded audio stops when a material correction is detected. Native playback continues when the phone locks or another app opens, supports lock-screen/headset controls, pauses for calls/Siri, and pauses on headphone disconnect without sending narration to the speaker.

## Voice and build gate

Ben wants a **professional reader**: composed, clear, neutral, about 150–165 words per minute, natural pauses and restrained emphasis. No music, banter or multi-speaker format. Prepare a 45–60-second sample with names, acronyms, money, percentages and a section transition; Ben judges it by listening. Full edition audio must match the approved script, with duration measured from the asset. The provider and any spending need a separate decision. Avoid running a local speech model on this low-RAM host.

TestFlight preparation needs a separate iOS app identity, native build files, app resources, signing plan and build workflow. Ben previews every new screen before merge. A physical iPhone must verify repeated Pause/Resume, seek, background and lock-screen controls, calls/Siri, headphones, relaunch, VoiceOver, Dynamic Type and dark mode. Browser screenshots do not establish iPhone behavior.
