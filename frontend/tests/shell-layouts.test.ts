/**
 * Every section of the app keeps the menu (2026-10-27): each top-level route
 * folder has a `layout.tsx` that wraps its pages in `<Shell>`. A new section
 * without one renders its screens with no sidebar and no top bar - which is
 * exactly what happened to /ads the day it was added.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const APP = path.resolve(__dirname, "../src/app");

/** Screens that are deliberately bare: signing in, the legal pages' own frame, the theme probe. */
const WITHOUT_SHELL = new Set(["auth", "login", "(legal)", "theme"]);

describe("every section keeps the menu", () => {
  it("wraps each top-level route folder in <Shell>", () => {
    const sections = readdirSync(APP).filter(
      (name) => statSync(path.join(APP, name)).isDirectory() && !name.startsWith("_"),
    );
    expect(sections.length).toBeGreaterThan(8);
    for (const section of sections) {
      if (WITHOUT_SHELL.has(section)) continue;
      const layout = path.join(APP, section, "layout.tsx");
      expect(existsSync(layout), `${section}/layout.tsx is missing`).toBe(true);
      expect(readFileSync(layout, "utf8"), `${section}/layout.tsx`).toContain("<Shell>");
    }
  });
});
