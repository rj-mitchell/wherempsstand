# Look and feel

How Where MPs Stand looks, and why. 6 November 2026. The styles are in `web/src/styles.css`, whose opening comment
says the same in brief.

## The printed record

The web app takes its look from the printed Official Report:

- ivory paper, black serif type (Source Serif 4, bundled with the site), and small-capital headings set between
  rules;
- column numbers in the margin, and speakers attributed the way Hansard prints them: "Hilary Benn (Leeds Central)
  (Lab)";
- the bound volumes' cloth, green for the Commons and red for the Lords, with gilt lettering. Sources are edged in
  their House's colour, and the sign-in page (invite-only mode) is a volume cover.

The tone is that of a reference work:

- the site's mark is the bound record, open: handwriting and a pen on the left page, the same words found by search
  on the right, under a magnifying glass. It is drawn in `web/public/icon.svg`, `web/partials/seal.html` and
  `web/src/components/Mark.tsx`, which are kept the same by hand;
- the name in spaced capitals; on the home page a full title page, and on every other page the name and mark on one
  line beside the menu. On a phone (under 30rem) the home page's title page is closer set, with a smaller mark, and
  its imprint keeps only the date of the last update: independence and sourcing are said again in the introduction
  and the footer. In the markup the name and home link come before the menu, so Tab reaches them first; the grid
  still draws the menu above the name on the home page and on phones;
- square corners (`--radius` is 0; only pill-shaped chips and tags are round), ruled lists, and example questions
  numbered as on the Order Paper;
- one marker for every fold-out (`<details>`): a gilt triangle, as on a volume's spine, that turns down when it is
  open (the turn is animated only for people who have not asked for reduced motion). The header's menus and the
  Export menu are menus, and keep their ▾;
- plain, formal wording; colour only where it carries meaning (the House, a citation, a warning).

## Colour and type

| Token (`styles.css`) | Use | Value |
|---|---|---|
| `--paper`, `--ink` | Page and text | Ivory `#faf6ec` and near-black by day; a dark green binding `#0e1612` with warm white text by night |
| `--commons` (also `--accent`) | The Commons, links, focus rings, primary buttons | Commons green, `oklch(41% 0.08 162)` light, `oklch(76% 0.09 160)` dark |
| `--lords` | The Lords | Lords red, `oklch(43% 0.13 24)` light, `oklch(75% 0.11 22)` dark |
| `--gilt` | Rules and lettering on the cloth | Gilt, `oklch(62% 0.1 80)` light, `oklch(80% 0.11 85)` dark |
| `--warn`, `--danger` | Warnings (statements not tied to a source), errors | Amber and red, always with words |

- **The House decides the colour**: green for the Commons and red for the Lords, as on the bound volumes and the
  benches. These describe the Houses, not a side in any vote.
- **Votes have no colour.** Every vote is shown in words and numbers: "Voted Aye", "Ayes 312, Noes 198", a table of
  each party's ayes and noes. Nothing is green for aye or red for no, which would imply that one way is right. The
  "Vote" label on a division is the site's accent, the same for every division. Checked on 6 November 2026 against
  the family plan's neutral marks (section 5): there is nothing to change. If votes are ever drawn as marks, use the
  family's neutral ones, as Where Councils Stand and Where Countries Stand do: filled for aye, outlined for no,
  hatched for abstained, always beside the word.
- **Charts** draw each series with a different marker shape as well as a colour, so they can be read without
  colour.
- **Source Serif 4** (SIL Open Font Licence) for the record, briefings and headings; the system's sans for controls
  and labels: navigation, buttons, chips, metadata and the small capital labels. Bundled with the site
  (`@fontsource-variable/source-serif-4`); nothing is loaded from Google.

Dark mode follows the system setting. The CSS follows [good-css](https://github.com/vojtaholik/good-css):

- logical properties;
- OKLCH colour tokens that hold both the light and dark values;
- one fluid type scale;
- hover styles only where a mouse is present;
- visible focus rings and press feedback;
- animation only for people who have not asked for reduced motion;
- a print stylesheet that opens collapsed sources, and a forced-colours mode.

## The family binding

Where MPs Stand is a Where They Stand project, and carries the family's binding, the same on every site (the
charity's family kit, copied into `web/public/family/` by its `family/kit/sync.mjs`):

- **the endorsement** under the site's name in the masthead: the site's spine in Commons green and "A Where They
  Stand project" in gilt capitals, linking to wheretheystand.uk;
- **the family shelf** at the foot of every page: the charity, its status ("applying to register as a charity in
  England and Wales"), wheretheystand.uk, the Donate page, and every project's spine with a link to each;
- **the favicon, theme colour and link card**: since kit 1.1.0 the favicon and link card show the site's own
  open-book mark on its Commons green tile, as on the page (family decision 4, amended at the owner's request on 6
  November 2026); at 16 and 32 px, and in the SVG favicon, a simpler drawing of it without the handwriting. The
  theme colour is the cloth, Commons green `#1D4535`. The kit's `head.html` replaces the site's own icon links and
  theme colour, so there is one set; the web manifest points at the same icons, and `/favicon.ico` and
  `/apple-touch-icon.png` are copies of the kit's. Every page without an image of its own shares the kit's link card
  (`/family/card.png`); a briefing also has its own card for embedding (`/b/<id>/card.png`, drawn by
  `src/hansard/api/cards.py`);
- **Source Serif 4 for headings** (family decision 1), through `class="wts-headings"` on `<body>`. The site's
  headings were already in it. The kit's styles are pointed at the site's own copy of the face (`--wts-serif` on
  `body`), so the kit's copy is never downloaded. The small capital labels set as headings for screen readers
  ("Try", "Where they stand", the source groups) keep the sans.

The kit's files are included as they are, never edited here. The static pages include them at build time
(`<!--#include family/shelf-->` in the partials, `web/vite.config.ts`); the home page's React masthead and footer
read the same files (`web/src/family.ts`), so the two cannot drift. A test (`web/src/__tests__/family.test.tsx`)
checks the copy against the hash in its `VERSION` file, and that every page carries the binding. To update it, from
a checkout of the charity's repository:

```bash
node <charity checkout>/family/kit/sync.mjs --site mps --out web/public/family
cp web/public/family/favicon.ico web/public/family/apple-touch-icon.png web/public/
```

## Links where the records meet

Under a briefing, a quiet line offers the same question on a sister site, by name and with its spine from the
family shelf: "Ask Where Councils Stand" for local services, "Ask Where Countries Stand" for foreign affairs. A short
rule for each (`web/src/sisters.ts`) decides when it shows, so it appears only where the other site's record deals
with the subject. Both sites take the question in `?q=` on their home page.

## Accessibility

The target is WCAG 2.2 AA. CI runs axe (`scripts/a11y.mjs`) on the main pages, light and dark, at desktop and
phone widths, against the built site on sample data. The site's statement is at
[/accessibility](https://wherempsstand.com/accessibility).
