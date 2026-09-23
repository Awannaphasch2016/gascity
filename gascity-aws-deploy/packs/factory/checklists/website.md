# Website discovery checklist

What a website project must have settled before anyone builds it. The
discoverer walks this list against the brief. An item is *settled by the
brief* when the brief answers it, *settled by fact* when the discoverer can
find the answer without a person, and *open* when only the owner can decide.
Only open items become questions, and every question carries a recommendation.

Items are ordered so that earlier answers narrow later ones. Ask in this
order.

## 1. Purpose

- What the site is for, in one sentence. What a visitor should do or know
  after visiting that they could not before.
- What success looks like to the owner, measurably if they can say it (a
  signup, a purchase, a message sent, a page read).
- What the site is explicitly *not*: scope the owner wants left out.

## 2. Audience

- Who visits, and what they already know. Whether they arrive from a link, a
  search, or by typing the address.
- Devices: phone-first, desktop-first, or both equally.
- Languages. One is the default; more is a decision.
- Accessibility: the level the owner wants met (a sensible default is WCAG 2.1
  AA, which is a fact, not a question, unless the owner wants less).

## 3. Content and pages

- The list of pages or screens, by name, and what each is for.
- Where the content comes from: written by the owner, already existing,
  generated, or pulled from a source. Whether it changes after launch and who
  changes it.
- Media: images, video, downloads. Who supplies them.

## 4. Behaviour and data

- What a visitor can *do* beyond reading: forms, search, accounts, purchases,
  comments, uploads.
- For each form or action: what is collected, where it goes, who sees it, and
  what the visitor sees afterwards.
- Whether anything must persist between visits. If yes, what, and for how
  long. This is what decides whether there is a database.
- Sign-in: none, a single owner login, or visitor accounts. Each is a
  different project.

## 5. Look

- Existing brand: logo, colours, type, a site to match. If none, whether the
  owner wants a recommendation or has a reference they like.
- Tone of the copy: who is speaking and how.

## 6. Running it

- Where it will live: a static host, a small server, the owner's existing
  infrastructure. What the owner already pays for.
- Domain: existing, to be bought, or not yet decided.
- Analytics and privacy: whether visits are measured, and what that implies
  for consent.
- Who maintains it after delivery, and what they are comfortable editing.

## 7. Constraints

- Deadline, if any, and what happens if it slips.
- Anything that must be used or must not be used: a language, a service, a
  provider.
- Legal: pages the owner's jurisdiction or business requires (privacy policy,
  imprint, terms).

## Defaults that are facts, not questions

Use these unless the brief or an answer says otherwise, and record them in the
requirements as defaults applied:

- TypeScript throughout; Node LTS; `npm` scripts `build`, `start`, `test`.
- Single-page or few-page site served from one process; SQLite when anything
  must persist and the brief names no other store.
- Responsive layout that works on a phone and a desktop.
- WCAG 2.1 AA.
- No third-party scripts unless a requirement needs one.
- No analytics unless asked for.
