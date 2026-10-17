---
name: apexmed-ui
description: Apexmed brand design system for the APM Management web app (frontend/). Use before building or restyling ANY screen, component, dialog, table, badge, chart colour or page in frontend/src - new pages, "làm đẹp", giao diện, theme, dark/light mode, màu, font, logo - and to verify a UI change visually in both themes.
---

# Apexmed UI — APM Management

The web app (Next.js 15 + Tailwind v4, `frontend/`) wears the **Apexmed Brand Guideline (GTG 05/2026)**:
*y tế cao cấp · công nghệ hiện đại · sang trọng tối giản · chuyên nghiệp tin cậy*.
Product name on screen: **APM Management** (subtitle "Creative Ops"). UI text is Vietnamese.

Everything lives in one place: tokens + brand layer in `frontend/src/app/globals.css`, fonts in
`frontend/src/app/fonts.ts`, mark in `frontend/src/components/logo.tsx` (+ `app/icon.svg`,
`app/apple-icon.png`). Brand facts: [references/brand.md](references/brand.md).

## 1. Golden rules

1. **Colour only through tokens.** `text-[var(--text)]`, `bg-[var(--surface)]`, `border-[var(--border)]` …
   Never a hex or a Tailwind palette class (`bg-blue-600`) for anything that is not a fixed brand mark.
   Tokens flip for dark mode by themselves; a literal does not.
2. **Both themes, every time.** Light = clean medical white; dark = navy night. Check both before calling a
   change done (section 6).
3. **One primary action per area.** Primary = `bg-[var(--accent)] text-[var(--accent-text)]`; the brand layer
   paints it with the gradient + glow automatically. Everything else is secondary (bordered surface).
4. **Never pale blue for small text on white** (guideline "KHÔNG NÊN"). `--brand-sky`/`--brand-pastel` are for
   fills, dark mode text, and the navy rail only.
5. **Magenta is an accent, not a state.** It means the ORD stream / ads energy. Errors are `--bad` (red).
6. **Restraint.** Max 2–3 type families per layout, gradients only on primary actions, hero bands and the
   logo. No gradient body text, no drop shadows on text, no rainbow cards.

## 2. Tokens (`globals.css`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--surface-muted` | `#f2f6fc` | `#081127` | page background, table header, hover |
| `--surface` | `#ffffff` | `#0e1a3d` | cards, panels, inputs, dialogs |
| `--border` | `#dae3f1` | `#22325f` | every hairline |
| `--text` | `#0d1a3f` | `#e7eefc` | body text |
| `--text-muted` | `#55627f` | `#9babd0` | secondary text, labels |
| `--accent` | `#245cb5` | `#88afea` | primary action, links, focus |
| `--accent-text` | `#ffffff` | `#0a1430` | text on accent |
| `--accent-soft` | `#e6eefc` | `#172a5a` | selected / active background |
| `--accent-strong` | `#132863` | `#afe8ff` | text on accent-soft, emphasis |
| `--magenta` / `--magenta-soft` | `#c23c66` / `#fde8ef` | `#f59ab8` / `#45193a` | ORD accent text / chip |
| `--good` `--warn` `--bad` (+`-soft`) | green / amber / red | lighter twins | states only |
| `--ring` | blue 28% | sky 32% | focus halo (`box-shadow: 0 0 0 3px var(--ring)`) |
| `--shadow-card` / `--shadow-pop` | navy-tinted | black | cards / popovers, dialogs |

Fixed (same in both themes): `--brand-navy #132863`, `--brand-blue #245cb5`, `--brand-sky #88afea`,
`--brand-pastel #afe8ff`, `--brand-magenta #e86a8e`, `--brand-magenta-pastel #fa9ae1`,
`--brand-gradient` (navy band), `--brand-ads-gradient` (magenta), `--brand-duo-gradient` (blue→magenta),
`--accent-gradient` (theme-aware primary).

Streams: **PR = brand blue**, **ORD = magenta** — `.unit-tag-pr/.unit-tag-ads`, `.unit-switch [data-unit]`.
Status badges: `.st .st-<colour>` from `lib/status-colors.ts` (waiting = amber, done = green, …); every colour
`lib/status-colors.ts` returns must have a `.st-<colour> {` rule (a test checks).

**Adding a token**: define it in all three blocks — `:root`, the `@media (prefers-color-scheme: dark)
:root:not([data-theme="light"])` block, and `:root[data-theme="dark"]`. Same rule for any per-theme class.

## 3. Type

| Role | Family (CSS var / utility) | Stands in for | Where |
|---|---|---|---|
| Display | Exo 2 — `--font-display`, `font-display` | Conthrax | h1–h3 automatically, `.text-2xl+` figures, logo wordmark |
| Body/UI | Be Vietnam Pro — `--font-sans` (default) | UTM Mobifone KT (web) | everything else |
| Label | Jura — `--font-label` | Jura (guideline body) | small uppercase eyebrows, table headers (`.brand-eyebrow`, `.table-dense th`, `.uppercase` on h2/h3/p/span) |

Conthrax / UTM are commercial: never add them by URL. Fonts load via `next/font/google` (self-hosted, CSP
`font-src 'self'` stays). Under vitest `next/font/google` is aliased to `tests/next-font-stub.ts` — add any new
family there too. Script fonts (Motherland/SloopScript): not on screen; at most 1–3 words in a hero.

Scale: page title `text-xl sm:text-2xl font-semibold tracking-tight`; section title `text-base font-semibold`;
body `text-sm`; meta `text-xs text-[var(--text-muted)]`; KPI `text-3xl font-semibold tabular-nums`.
Inputs stay 16px under `sm` (iOS zoom; a test checks `font-size: 16px`).

## 4. Recipes (copy these)

```tsx
// Primary / secondary / danger: use components/pr.tsx PrimaryButton, SecondaryButton, DangerButton.
<button className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]">Lưu</button>
<button className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]">Huỷ</button>

// Card / panel (shadow comes from the brand layer)
<section className="panel p-4">…</section>
<div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">…</div>

// Page header: components/pr.tsx PageHeader. Eyebrow over a title:
<p className="brand-eyebrow">Luồng Order</p><h1 className="text-2xl font-semibold tracking-tight">…</h1>

// Field
<input className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]" />

// Selected / active row or chip
className="bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"

// Soft notice
<div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-4 text-sm">…</div>

// Dialog: components/modal.tsx (Modal, ModalBody, ModalFooter). Confirmation: components/confirm.tsx.
// Dense table: <table className="table-dense"> inside <div className="panel table-scroll-x">.
// Navy band (hero / promo): className="brand-rail …" — redefines tokens to white-on-navy inside it.
```

Shapes: radius `rounded-lg` controls, `rounded-xl` cards (panel = 14px), `rounded-full` pills/avatars.
Touch targets ≥ `min-h-11` (44px). Spacing: page `px-4 sm:px-6 xl:px-8`, card `p-4`/`p-6`, gaps 2–4.
Icons: inline 24-grid stroke SVG, `strokeWidth 1.8`, `currentColor` (see `Icon` in `components/shell.tsx`).

## 5. The brand layer (what applies automatically)

In `globals.css` after "Apexmed brand polish", inside `@layer base/components` so a utility on an element
still wins: heading font; `bg-[var(--accent)]` → `--accent-gradient` + glow + press; rounded-xl/2xl surfaces
→ `--shadow-card`; `.brand-topbar` frosted header with a sky→magenta hairline; `.brand-rail` navy sidebar
(tokens re-scoped to white, active link = pastel bar, "A" watermark); focus ring on fields; selection colour.
Unlayered rules (e.g. `.unit-tag-*`) beat layered ones — overrides for those go **outside** the layers.

## 6. Verify visually (required before "done")

The strict CSP (no `unsafe-eval`) breaks `next dev` in the browser, so check a production build:

```bash
docker compose build web && docker compose up -d web          # local only, http://127.0.0.1:8811
.claude/skills/apexmed-ui/scripts/shoot.sh /tmp/shots /dashboard /tasks /account   # light + dark PNGs
UI_VIEWPORT=390,844 .claude/skills/apexmed-ui/scripts/shoot.sh /tmp/shots-phone /tasks
```

Then Read the PNGs. Logs in as local test account `1000/12345` (`scripts/seed_test_logins.py`). With the
Playwright MCP (`.mcp.json`) you can also drive the page live (log in at `/login`, flip the theme switch).

Checklist: both themes legible · no literal colours added · text contrast ≥ 4.5:1 (muted on surface too)
· primary action obvious and single · phone width (390px) has no sideways scroll · Vietnamese diacritics
render in all three fonts · `cd frontend && npm run typecheck && npx vitest run` green (known failure:
`security.test.ts` #37). Several tests scan source (`logo.test.tsx`, `status-colors.test.ts`, `ux.test.tsx`,
`content-views.test.tsx` select-option colours) — keep them in step.
