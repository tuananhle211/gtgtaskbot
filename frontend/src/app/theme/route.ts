import { NextResponse, type NextRequest } from "next/server";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";

/**
 * Remembers the header's light/dark switch for this browser, for a year.
 * Only "light" or "dark" is accepted; the cookie holds nothing else.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const body: unknown = await request.json().catch(() => null);
  const theme = parseTheme(
    body && typeof body === "object" && "theme" in body
      ? String((body as { theme: unknown }).theme)
      : null,
  );
  if (theme === null) {
    return NextResponse.json({ error: "theme_invalid" }, { status: 422 });
  }
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set(THEME_COOKIE, theme, {
    httpOnly: true,
    sameSite: "lax",
    secure: request.nextUrl.protocol === "https:" ||
      request.headers.get("x-forwarded-proto") === "https",
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });
  return response;
}
