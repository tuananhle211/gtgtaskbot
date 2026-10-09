/**
 * The header's light/dark switch: it flips `<html data-theme>` at once and
 * asks the server (`POST /theme`) to remember the choice; the route accepts
 * only "light" or "dark" and sets a year-long cookie.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextRequest } from "next/server";
import { ThemeToggle } from "@/components/theme-toggle";
import { POST } from "@/app/theme/route";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

describe("the theme switch", () => {
  it("flips the page and saves the choice", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    document.documentElement.dataset.theme = "light";
    render(<ThemeToggle />);
    const toggle = screen.getByRole("switch", { name: "Chế độ tối" });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(fetchMock).toHaveBeenCalledWith(
      "/theme",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ theme: "dark" }) }),
    );

    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("POST /theme", () => {
  const post = (body: unknown) =>
    POST(
      new NextRequest("http://localhost/theme", {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "content-type": "application/json" },
      }),
    );

  it("remembers light or dark for a year", async () => {
    const response = await post({ theme: "dark" });
    expect(response.status).toBe(204);
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("meobot_theme=dark");
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie).toContain("Max-Age=31536000");
  });

  it("refuses anything else", async () => {
    expect((await post({ theme: "<script>" })).status).toBe(422);
    expect((await post(null)).status).toBe(422);
  });
});
