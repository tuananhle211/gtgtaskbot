import { Be_Vietnam_Pro, Exo_2, Jura } from "next/font/google";

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
 * `next/font` downloads them at build time and serves them from /_next, so the
 * CSP (`font-src 'self'`) needs nothing new and no page asks Google for anything.
 */
export const bodyFont = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

export const headingFont = Exo_2({
  subsets: ["latin", "vietnamese"],
  weight: ["500", "600", "700"],
  variable: "--font-heading",
  display: "swap",
});

export const accentFont = Jura({
  subsets: ["latin", "vietnamese"],
  weight: ["500", "600", "700"],
  variable: "--font-accent",
  display: "swap",
});

/** Put on `<html>`: defines the three variables for globals.css. */
export const fontVariables = `${bodyFont.variable} ${headingFont.variable} ${accentFont.variable}`;
