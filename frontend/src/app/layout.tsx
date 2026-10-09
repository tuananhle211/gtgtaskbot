import type { Metadata, Viewport } from "next";
import "./globals.css";
import { cookies } from "next/headers";
import { Providers } from "@/components/providers";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";

export const metadata: Metadata = {
  /*
   * `default` is the tab title for every page that sets none of its own - which
   * is every page under /pr, because they are client components and a client
   * component cannot export metadata. `template` is what the pages that *do*
   * set one are composed into, so a page names only itself: `/terms` says
   * "Terms of Service" and the browser tab reads "Terms of Service | TasksBot".
   *
   * The product name therefore appears once, here, rather than being repeated in
   * every page's own title where the two could drift apart.
   */
  title: {
    default: "TasksBot",
    template: "%s | TasksBot",
  },
  description: "Bảng điều khiển nội dung PR.",
  // Nothing here is public, and a search engine indexing an approval queue would
  // be a leak rather than a feature. The two legal pages override this - they are
  // published on purpose; see src/app/(legal)/terms/page.tsx.
  robots: { index: false, follow: false },
  /*
   * No `icons` key, deliberately. `src/app/icon.svg` (and `apple-icon.png`) are picked up by the App
   * Router's file convention and emitted as the <link rel="icon"> itself. Naming
   * it here as well is the usual way a project ends up with two icon links that
   * disagree.
   */
};

/**
 * `viewportFit: "cover"` is what makes `env(safe-area-inset-bottom)` a real
 * number on an iPhone. Without it the value is 0 and the sticky action bar sits
 * under the home indicator, where a tap on "Duyệt" either does nothing or
 * dismisses the browser.
 *
 * `maximumScale` is deliberately absent: capping zoom would stop somebody
 * pinching to read a script, which is the one thing this panel exists to show.
 * iOS auto-zoom on focus is prevented the correct way instead - form controls
 * hold 16px on small screens, in `globals.css`.
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // The header switch's saved choice; none = follow the system.
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html lang="vi" data-theme={theme ?? undefined}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
