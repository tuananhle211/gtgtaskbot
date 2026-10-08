/**
 * The TasksBot mark and wordmark.
 *
 * The mark is an inline SVG (no request for the CSP to allow) and decorative by
 * default. Its gradient id must be unique per instance: the sidebar copy is
 * hidden on small screens, and a gradient defined inside a hidden SVG does not
 * paint for the visible one that points at it.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { Logo, LogoMark } from "@/components/logo";

const SRC = path.resolve(__dirname, "../src");

describe("LogoMark", () => {
  it("is decorative unless given a title", () => {
    const { container } = render(<LogoMark size={20} />);
    const svg = container.querySelector("svg") as SVGSVGElement;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("width", "20");
    expect(svg).toHaveAttribute("height", "20");
    expect(svg).not.toHaveAttribute("role");
  });

  it("names itself when given a title", () => {
    render(<LogoMark title="TasksBot" />);
    expect(screen.getByRole("img", { name: "TasksBot" })).not.toHaveAttribute("aria-hidden");
  });

  it("gives every instance its own gradient", () => {
    const { container } = render(
      <>
        <LogoMark />
        <LogoMark />
      </>,
    );
    const ids = [...container.querySelectorAll("linearGradient")].map((node) => node.id);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    for (const [index, path] of [...container.querySelectorAll("svg path")].entries()) {
      expect(path.getAttribute("stroke")).toBe(`url(#${ids[index]})`);
    }
  });
});

describe("Logo", () => {
  it("shows the wordmark in the theme text colour, with an optional subtitle", () => {
    const { container } = render(<Logo subtitle="Creative Ops" />);
    const word = screen.getByText("TasksBot");
    expect(word.className).toContain("text-[var(--text)]");
    expect(screen.getByText("Creative Ops")).toBeInTheDocument();
    expect(container.querySelector("svg[data-logo-mark]")).toHaveAttribute("aria-hidden", "true");
  });

  it("leaves out the subtitle when none is given", () => {
    render(<Logo />);
    expect(screen.queryByText("Creative Ops")).toBeNull();
  });
});

describe("the brand assets", () => {
  it("ships the mark as the app icon and no longer uses the old bitmap", () => {
    expect(readFileSync(path.join(SRC, "app/icon.svg"), "utf8")).toContain("#1f6feb");
    for (const file of ["components/shell.tsx", "app/login/page.tsx", "components/legal.tsx"]) {
      const source = readFileSync(path.join(SRC, file), "utf8");
      expect(source, file).not.toContain("meochat-icon");
      expect(source, file).toContain("@/components/logo");
    }
  });
});
