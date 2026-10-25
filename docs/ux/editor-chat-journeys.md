# Editor chat: user journeys and layout

Status: agreed with the owner (2026-10-25). Desktop app only: no phone layout work.
Scope: the Chat tab of the build workspace (`ChatPanel.tsx`, `MessageList.tsx`, `TurnStatus.tsx`, `Composer.tsx`).

## Problems this answers

1. Free instant edits and paid AI runs share one timeline and look alike.
2. Decisions (clarify chips, "Ask the AI", review card, approvals) are spread over four places.
3. Undo works only on the last message.
4. The mode hint line under the composer is overloaded and changes text constantly.
5. Answered clarify and needs-AI cards lose their chips and their chosen answer.
6. The review card is pinned above the transcript with only a "+N / -N / moved" count.
7. Attachments silently force the AI route; errors are not tied to a message.

## Layout (direction A: one typed timeline plus a "Needs you" tray)

- One timeline. Every message has a type badge: **You**, **Free · instant**, **AI · cost**.
- One **Needs you** tray pinned just above the composer. It holds whatever waits for the person: clarify chips, the needs-AI estimate, the proposal review, tool approvals. Empty when nothing waits. The transcript keeps a plain record of what was chosen.
- The composer hint line is split into a **Quick / AI** segmented switch and one separate cost line.
- A collapsible **Session changes** list above the composer with per-change Undo and "Go back to version N".

## Journeys

**J1 Start a build from a description.** Empty canvas, one prompt with three example prompts. The free path builds from a generator at once ("Built a House, free"); otherwise a needs-AI card with estimate waits in the tray. The result offers tweak suggestions.

**J2 Tweak the current build.** One message may carry several edits. The result is one card listing each edit as before → after, with its own Undo. The changed parts are highlighted on the canvas.

**J3 Answer a clarifying question.** The question and chips sit in the tray. "Show them" toggles a canvas highlight with a visible on/off state. "Just pick for me" says what it picked. After answering, the transcript keeps "You chose: Red".

**J4 Review a big change before applying it.** The proposal appears in the tray with a diff grouped by role/section, plus Preview, Keep, Discard. A short record stays in the transcript at the message that produced it.

**J5 Fix a part by role.** Selecting a part adds a context chip to the composer ("Door · 1 selected"); the next message is scoped to it. Role-aware chips offer "All doors" or "This door only".

**J6 Import or upload a reference.** Attaching shows "This goes to the AI" with the estimate before sending, a file chip in the composer, and an "I used: reference.png" line in the answer.

**J7 Undo and roll back.** Session changes list: per-change Undo/Redo and "Go back to version N", replacing last-message-only Undo.

**J8 Choose and release parts.** The person decides which parts the build may use: all of My Parts, loose parts only, specific sets or sources, or no limit. The picker sits in the composer or the Needs you tray and feeds the owned-parts and prefer modes and the reservations. A built or reserved set can be disassembled into the pool for this build, from the chat ("use the parts from my fire station") or a chip, which updates the set's state and reservations. After a build the result says where the parts came from (loose parts, which sets, extra to buy).

## Out of scope

Phone layout, a canvas banner for previews, and the older standalone agent chat page.
