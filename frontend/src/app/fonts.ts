import localFont from "next/font/local";

/**
 * The Apexmed brand type, as web fonts the panel may serve itself.
 *
 * The guideline's faces are Conthrax (headings), Jura (body) and UTM Mobifone
 * KT (websites). Conthrax and UTM are commercial and not licensed for web
 * hosting here, so each role takes the closest free face with Vietnamese marks:
 *
 * - `--font-heading`: Exo 2 - wide, technical, the Conthrax feel for titles.
 * - `--font-body`: Be Vietnam Pro - the plain, very legible sans the guideline
 *   asks for on websites (its UTM Mobifone KT role), for every dense table.
 * - `--font-accent`: Jura - the guideline's own face, kept for small uppercase
 *   labels and table headings where its character shows.
 *
 * The files live in `src/fonts` (2026-11-11): the build used to download them
 * from Google Fonts (`next/font/google`) and a deploy failed when Google sent a
 * bad answer. Each is the google/fonts OFL original (licences beside them),
 * cut to Latin + Vietnamese with fontTools and saved as woff2 - the same
 * characters Google served. `next/font/local` serves them from /_next, so the
 * CSP (`font-src 'self'`) needs nothing new and no page asks Google for anything.
 */
export const bodyFont = localFont({
  src: [
    { path: "../fonts/be-vietnam-pro-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/be-vietnam-pro-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/be-vietnam-pro-600.woff2", weight: "600", style: "normal" },
    { path: "../fonts/be-vietnam-pro-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-body",
  display: "swap",
});

/** A variable font: every weight from one file (the panel uses 500-700). */
export const headingFont = localFont({
  src: "../fonts/exo-2-variable.woff2",
  weight: "100 900",
  variable: "--font-heading",
  display: "swap",
});

export const accentFont = localFont({
  src: "../fonts/jura-variable.woff2",
  weight: "300 700",
  variable: "--font-accent",
  display: "swap",
});

/** Put on `<html>`: defines the three variables for globals.css. */
export const fontVariables = `${bodyFont.variable} ${headingFont.variable} ${accentFont.variable}`;
