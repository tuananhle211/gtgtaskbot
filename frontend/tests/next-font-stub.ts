/**
 * `next/font/google` only works inside Next's compiler. Under vitest every
 * font is this stand-in: a fixed class name and CSS variable, enough for
 * `src/app/fonts.ts` and the root layout to load.
 */
type FontOptions = { variable?: string };

const font = (options: FontOptions = {}) => ({
  className: "font-stub",
  variable: options.variable ? `font-stub${options.variable}` : "font-stub",
  style: { fontFamily: "sans-serif" },
});

export const Be_Vietnam_Pro = font;
export const Exo_2 = font;
export const Jura = font;
