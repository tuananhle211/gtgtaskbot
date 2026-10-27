/**
 * The Design team's "Thư viện": the sidebar branch and the screen that keeps it.
 *
 * Sidebar: a second folding branch, after the app's own screens, drawn from
 * `design.library` on `/api/units/me`. Its entries leave the app (a new tab,
 * `rel="noopener noreferrer"`), each led by a round chip - the icon the Leader
 * typed, else the name's initials - and only an `http(s)` link is ever drawn.
 * "Quản lý thư viện" closes the branch for whoever has `design.manage_library`.
 *
 * `/tasks/library`: the list edited as a whole (add, remove, move, icon / name
 * / link) and saved with one PUT; a refusal is shown on the row and field the
 * server names.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DesignLibraryPage from "@/app/tasks/library/page";
import {
  isLibraryUrl,
  libraryIconText,
  libraryInitials,
} from "@/components/library-icon";
import { Shell } from "@/components/shell";
import type { DesignLibraryLink } from "@/lib/api";
import { confirm, renderWithQuery, SESSION, stubFetch, urlStore } from "./helpers";

const SEARCH = { value: new URLSearchParams() };
const URL_BAR = urlStore(SEARCH);
let pathname = "/tasks";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  usePathname: () => pathname,
  useSearchParams: () => URL_BAR.useSearchParams(),
  useRouter: () => ({
    replace: (url: string) => URL_BAR.navigate(url),
    push: (url: string) => URL_BAR.navigate(url),
    refresh: vi.fn(),
    back: vi.fn(),
  }),
}));

beforeEach(() => {
  SEARCH.value = new URLSearchParams();
  pathname = "/tasks";
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };
const calls = (stub: unknown) => (stub as { calls: Call[] }).calls;

const NHAP_MON: DesignLibraryLink = {
  id: "11110000-0000-4000-8000-000000000001",
  name: "Nhập môn",
  url: "https://drive.example.com/nhap-mon",
  icon: "N",
};
const BRAND: DesignLibraryLink = {
  id: "11110000-0000-4000-8000-000000000002",
  name: "Brand Guideline",
  url: "https://drive.example.com/brand",
  icon: null,
};
const TEMPLATE: DesignLibraryLink = {
  id: "11110000-0000-4000-8000-000000000003",
  name: "Template media",
  url: "http://intranet.example.com/template",
  icon: "🎨",
};

type DesignBlock = {
  view: boolean;
  manage_codes: boolean;
  birthday: boolean;
  library?: DesignLibraryLink[];
  manage_library?: boolean;
};

const ME = (design?: DesignBlock) => ({
  units: [
    {
      code: "ADS",
      label: "Luồng Order (ORD)",
      role: "THIET_KE",
      role_label: "Design",
      is_lead: false,
      member_code: "MY",
      personal_nas_url: null,
      settings: {},
    },
  ],
  default_unit: "ADS",
  can_view_all: false,
  can_admin: [],
  effort_enabled: false,
  ...(design ? { design } : {}),
});

const MEMBER = (library: DesignLibraryLink[]): DesignBlock => ({
  view: true,
  manage_codes: false,
  birthday: true,
  library,
  manage_library: false,
});
const LEADER = (library: DesignLibraryLink[]): DesignBlock => ({
  view: true,
  manage_codes: true,
  birthday: true,
  library,
  manage_library: true,
});

const NOTIFICATIONS = { match: "/api/notifications", body: { items: [], unread_count: 0 } };

// --- 1. The sidebar branch -----------------------------------------------------

describe('the "Thư viện" branch of the nav', () => {
  const primary = () => screen.getByRole("navigation", { name: "Điều hướng chính" });
  const branch = () => within(primary()).getByRole("group", { name: "Thư viện" });
  const shell = (design?: DesignBlock) => {
    stubFetch([
      { match: "/api/auth/session", body: { ...SESSION, role: "EMPLOYEE" } },
      { match: "/api/units/me", body: ME(design) },
      NOTIFICATIONS,
    ]);
    renderWithQuery(<Shell>{null}</Shell>);
  };

  it("lists the entries for a Design member, as links out of the app", async () => {
    shell(MEMBER([NHAP_MON, BRAND, TEMPLATE]));
    await screen.findByRole("link", { name: /Nhập môn/ });
    const links = within(branch()).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "https://drive.example.com/nhap-mon",
      "https://drive.example.com/brand",
      "http://intranet.example.com/template",
    ]);
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      // Not an app screen: never "the current page".
      expect(link).not.toHaveAttribute("aria-current");
    }
    // No right to manage: nothing at the end of the list.
    expect(screen.queryByRole("link", { name: "Quản lý thư viện" })).not.toBeInTheDocument();
    // After the app's own screens, which keep their place and order.
    const hrefs = within(primary())
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs.slice(0, 6)).toEqual([
      "/dashboard",
      "/performance/me", "/performance/timeline",
      "/tasks",
      "/tasks/birthday",
      "/tasks/task-codes",
    ]);
    // Then the library's links.
    expect(hrefs.slice(6)).toEqual(links.map((link) => link.getAttribute("href")));
  });

  it("leads each entry with its icon, else the name's initials", async () => {
    shell(MEMBER([NHAP_MON, BRAND, TEMPLATE]));
    await screen.findByRole("link", { name: /Nhập môn/ });
    const chips = within(branch())
      .getAllByRole("link")
      .map((link) => link.querySelector("[data-library-icon]"));
    expect(chips.map((chip) => chip?.textContent)).toEqual(["N", "BG", "🎨"]);
    for (const chip of chips) {
      // The app's own tint (`.avatar`, a hue from the name): no colour of its own.
      expect(chip).toHaveClass("avatar");
      expect(chip).toHaveAttribute("aria-hidden", "true");
      expect((chip as HTMLElement).style.getPropertyValue("--avatar-h")).toMatch(/^\d+$/);
    }
    // The same entry, the same colour: it follows the name.
    const hue = (chip: Element | null) => (chip as HTMLElement).style.getPropertyValue("--avatar-h");
    expect(hue(chips[0])).not.toBe("");
    expect(screen.getByRole("link", { name: "Nhập môn" })).toBeInTheDocument();
  });

  it("never draws a link that is not http(s)", async () => {
    shell(
      MEMBER([
        { ...NHAP_MON, url: "javascript:alert(1)" },
        { ...BRAND, id: "x-data", name: "Data", url: "data:text/html,<p>x</p>" },
        { ...BRAND, id: "x-path", name: "Đường dẫn", url: "/tasks/library" },
        TEMPLATE,
      ]),
    );
    await screen.findByRole("link", { name: /Template media/ });
    expect(
      within(branch())
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["🎨Template media"]);
    expect(screen.queryByText("Nhập môn")).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it("is not there for anybody outside the Design team", async () => {
    shell({ view: false, manage_codes: false, birthday: true, library: [], manage_library: false });
    await screen.findByRole("link", { name: "Order sinh nhật" });
    expect(within(primary()).queryByRole("group", { name: "Thư viện" })).not.toBeInTheDocument();
    expect(within(primary()).queryByRole("button", { name: "Thư viện" })).not.toBeInTheDocument();
  });

  it("is not there with an API that predates it, nor without the design block", async () => {
    shell({ view: true, manage_codes: false, birthday: true });
    await screen.findByRole("link", { name: "Order sinh nhật" });
    expect(within(primary()).queryByRole("group", { name: "Thư viện" })).not.toBeInTheDocument();
  });

  it("is not there when every entry is unusable and the viewer cannot manage", async () => {
    shell(MEMBER([{ ...NHAP_MON, url: "javascript:alert(1)" }]));
    await screen.findByRole("link", { name: "Order sinh nhật" });
    expect(within(primary()).queryByRole("group", { name: "Thư viện" })).not.toBeInTheDocument();
  });

  it('ends with "Quản lý thư viện" for whoever may manage it, even while empty', async () => {
    shell(LEADER([]));
    const manage = await screen.findByRole("link", { name: "Quản lý thư viện" });
    expect(manage).toHaveAttribute("href", "/tasks/library");
    expect(manage).not.toHaveAttribute("target");
    expect(within(branch()).getAllByRole("link")).toEqual([manage]);
  });

  it("puts it after the entries, and marks it - not the task table - on its own screen", async () => {
    pathname = "/tasks/library";
    shell(LEADER([NHAP_MON, BRAND]));
    const manage = await screen.findByRole("link", { name: "Quản lý thư viện" });
    expect(
      within(branch())
        .getAllByRole("link")
        .map((link) => link.getAttribute("href")),
    ).toEqual([NHAP_MON.url, BRAND.url, "/tasks/library"]);
    expect(manage).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Order tổng" })).not.toHaveAttribute("aria-current");
  });

  it('folds on its own, apart from "Bảng order"', async () => {
    shell(LEADER([NHAP_MON]));
    await screen.findByRole("link", { name: /Nhập môn/ });
    const heading = within(branch()).getByRole("button", { name: "Thư viện" });
    const boards = screen.getByRole("button", { name: "Bảng order" });
    expect(heading).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "false");
    expect(boards).toHaveAttribute("aria-expanded", "true");
    // Back open: the fold is shared by every Shell of the tab (and every test).
    await userEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps the names for screen readers on the icon-only sidebar", async () => {
    shell(MEMBER([NHAP_MON]));
    const link = await screen.findByRole("link", { name: /Nhập môn/ });
    await userEvent.click(screen.getByRole("button", { name: "Thu gọn menu" }));
    expect(link).toHaveAttribute("title", "Nhập môn");
    expect(link.querySelector("[data-library-icon]")).toHaveTextContent("N");
    await userEvent.click(screen.getByRole("button", { name: "Mở rộng menu" }));
  });

  it("reads initials, icons and links the way the menu draws them", () => {
    expect(libraryInitials("Brand Guideline")).toBe("BG");
    expect(libraryInitials("Nhập môn")).toBe("NM");
    expect(libraryInitials("đào tạo nội bộ")).toBe("ĐT");
    expect(libraryInitials("Template")).toBe("T");
    expect(libraryInitials("   ")).toBe("?");
    expect(libraryIconText("Nhập môn", "N")).toBe("N");
    expect(libraryIconText("Nhập môn", "  ")).toBe("NM");
    expect(libraryIconText("Nhập môn", null)).toBe("NM");
    expect(isLibraryUrl("https://drive.example.com/a")).toBe(true);
    expect(isLibraryUrl("HTTP://example.com")).toBe(true);
    expect(isLibraryUrl("javascript:alert(1)")).toBe(false);
    expect(isLibraryUrl("//example.com")).toBe(false);
    expect(isLibraryUrl("https://")).toBe(false);
    expect(isLibraryUrl("https://a b")).toBe(false);
    expect(isLibraryUrl(null)).toBe(false);
  });
});

// --- 2. The screen that keeps it -------------------------------------------------

type Routes = Parameters<typeof stubFetch>[0];

describe('"Quản lý thư viện"', () => {
  const managerPage = (
    links: DesignLibraryLink[] = [NHAP_MON, BRAND],
    extra: Routes = [],
    design: DesignBlock = LEADER(links),
  ) => {
    const stub = stubFetch([
      ...extra,
      { match: "/api/units/me", body: ME(design) },
      { match: "/api/units/ADS/library", body: { links, can_manage: true } },
    ]);
    pathname = "/tasks/library";
    renderWithQuery(<DesignLibraryPage />);
    return stub;
  };
  const rows = () => screen.getAllByTestId("library-row");
  const field = (name: string, n: number) => screen.getByRole("textbox", { name: `${name} · dòng ${n}` });
  const names = () => rows().map((_, index) => (field("Tên", index + 1) as HTMLInputElement).value);
  const save = () => screen.getByRole("button", { name: "Lưu" });
  const put = (stub: unknown) => calls(stub).find((call) => call.method === "PUT");
  const meCalls = (stub: unknown) =>
    calls(stub).filter((call) => call.url === "/api/units/me").length;

  it("opens on the stored list, with a live preview of each chip", async () => {
    managerPage();
    expect(await screen.findByRole("textbox", { name: "Tên · dòng 1" })).toHaveValue("Nhập môn");
    expect(field("Icon", 1)).toHaveValue("N");
    expect(field("Icon", 1)).toHaveAttribute("placeholder", "N hoặc 🎨");
    expect(field("Link", 1)).toHaveValue("https://drive.example.com/nhap-mon");
    expect(field("Icon", 2)).toHaveValue("");
    const chip = (n: number) => rows()[n - 1].querySelector("[data-library-icon]");
    expect(chip(1)).toHaveTextContent("N");
    // No icon: the name's initials, following the name as it is typed.
    expect(chip(2)).toHaveTextContent("BG");
    await userEvent.clear(field("Tên", 2));
    await userEvent.type(field("Tên", 2), "Kho ảnh");
    expect(chip(2)).toHaveTextContent("KẢ");
    await userEvent.type(field("Icon", 2), "📷");
    expect(chip(2)).toHaveTextContent("📷");
    // Nothing changed yet on the server's side: saving is one explicit step.
    expect(screen.getByText("2 / 30 mục")).toBeInTheDocument();
  });

  it("has nothing to save until something changes", async () => {
    const stub = managerPage();
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    expect(save()).toBeDisabled();
    await userEvent.type(field("Tên", 1), " 2");
    expect(save()).toBeEnabled();
    await userEvent.clear(field("Tên", 1));
    await userEvent.type(field("Tên", 1), "Nhập môn");
    expect(save()).toBeDisabled();
    expect(put(stub)).toBeUndefined();
  });

  it("adds, reorders and removes, then PUTs the whole list in order", async () => {
    const stored = [
      { id: "11110000-0000-4000-8000-000000000009", name: "Template media", url: "https://drive.example.com/template", icon: null },
      BRAND,
    ];
    const stub = managerPage(
      [NHAP_MON, BRAND],
      [
        {
          match: "/api/units/ADS/library",
          method: "PUT",
          body: { links: stored, can_manage: true },
        },
      ],
    );
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });

    await userEvent.click(screen.getByRole("button", { name: "+ Thêm mục" }));
    expect(rows()).toHaveLength(3);
    // A blank row says what it needs, and holds the save back.
    expect(within(rows()[2]).getByText(/Nhập tên và link cho dòng này/)).toBeInTheDocument();
    expect(save()).toBeDisabled();
    await userEvent.type(field("Tên", 3), "Template media");
    await userEvent.type(field("Link", 3), "  https://drive.example.com/template ");
    expect(save()).toBeEnabled();

    // The new row goes to the top; the first row is removed.
    await userEvent.click(screen.getByRole("button", { name: "Chuyển dòng 3 lên" }));
    await userEvent.click(screen.getByRole("button", { name: "Chuyển dòng 2 lên" }));
    expect(names()).toEqual(["Template media", "Nhập môn", "Brand Guideline"]);
    expect(screen.getByRole("button", { name: "Chuyển dòng 1 lên" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Chuyển dòng 3 xuống" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Chuyển dòng 2 xuống" }));
    expect(names()).toEqual(["Template media", "Brand Guideline", "Nhập môn"]);
    await userEvent.click(screen.getByRole("button", { name: "Xoá dòng 3" }));
    expect(names()).toEqual(["Template media", "Brand Guideline"]);

    const before = meCalls(stub);
    await userEvent.click(save());
    expect(await screen.findByRole("dialog")).toHaveTextContent("Lưu thư viện (2 mục)?");
    expect(put(stub)).toBeUndefined();
    await confirm();
    await waitFor(() => expect(put(stub)).toBeDefined());
    expect(put(stub)!.url).toBe("/api/units/ADS/library");
    expect(put(stub)!.body).toEqual({
      links: [
        // New: no id. Trimmed. No icon: null.
        { name: "Template media", url: "https://drive.example.com/template", icon: null },
        // Kept: its id goes back. The removed one is simply not there.
        { id: BRAND.id, name: "Brand Guideline", url: BRAND.url, icon: null },
      ],
    });
    // The sidebar's menu comes from /api/units/me: it is read again at once.
    await waitFor(() => expect(meCalls(stub)).toBeGreaterThan(before));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The screen now holds what the server stored: nothing left to save.
    await waitFor(() => expect(save()).toBeDisabled());
    expect(names()).toEqual(["Template media", "Brand Guideline"]);
  });

  it("sends the icon as typed, and null once it is cleared", async () => {
    const stub = managerPage(
      [NHAP_MON, BRAND],
      [
        {
          match: "/api/units/ADS/library",
          method: "PUT",
          body: { links: [NHAP_MON, BRAND], can_manage: true },
        },
      ],
    );
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    await userEvent.clear(field("Icon", 1));
    await userEvent.type(field("Icon", 2), " BG ");
    await userEvent.click(save());
    await confirm();
    await waitFor(() => expect(put(stub)).toBeDefined());
    expect(put(stub)!.body).toEqual({
      links: [
        { id: NHAP_MON.id, name: "Nhập môn", url: NHAP_MON.url, icon: null },
        { id: BRAND.id, name: "Brand Guideline", url: BRAND.url, icon: "BG" },
      ],
    });
  });

  it("says early what a row is missing, mirroring the server's rules", async () => {
    managerPage();
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    await userEvent.clear(field("Tên", 1));
    expect(within(rows()[0]).getByText("Nhập tên.")).toBeInTheDocument();
    await userEvent.clear(field("Link", 2));
    await userEvent.type(field("Link", 2), "javascript:alert(1)");
    expect(
      within(rows()[1]).getByText("Link phải bắt đầu bằng http:// hoặc https://."),
    ).toBeInTheDocument();
    expect(field("Link", 2)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Còn 2 dòng chưa đủ tên hoặc link hợp lệ.")).toBeInTheDocument();
    expect(save()).toBeDisabled();
    expect(field("Tên", 1)).toHaveAttribute("maxlength", "80");
    expect(field("Icon", 1)).toHaveAttribute("maxlength", "8");
  });

  it("stops adding at 30 entries", async () => {
    const many = Array.from({ length: 30 }, (_, index) => ({
      id: `11110000-0000-4000-8000-0000000001${String(index).padStart(2, "0")}`,
      name: `Mục ${index + 1}`,
      url: `https://drive.example.com/${index + 1}`,
      icon: null,
    }));
    managerPage(many);
    await screen.findByRole("textbox", { name: "Tên · dòng 30" });
    expect(screen.getByText("30 / 30 mục")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Thêm mục" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Xoá dòng 30" }));
    expect(screen.getByRole("button", { name: "+ Thêm mục" })).toBeEnabled();
  });

  it("shows the server's refusal on the row and field it names", async () => {
    managerPage(
      [NHAP_MON, BRAND],
      [
        {
          match: "/api/units/ADS/library",
          method: "PUT",
          status: 422,
          body: {
            error: {
              code: "validation_error",
              message: "Link của mục này không hợp lệ.",
              details: { reason: "library_url_invalid", field: "url", index: 1 },
            },
          },
        },
      ],
    );
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    // Fine by the checks here; the server disagrees, and its word is shown.
    await userEvent.clear(field("Link", 2));
    await userEvent.type(field("Link", 2), "https://brand.example.com");
    await userEvent.click(save());
    await confirm();
    // On row 2's link, and nowhere else.
    expect(await within(rows()[1]).findByRole("alert")).toHaveTextContent(
      "Link của mục này không hợp lệ.",
    );
    // Said once, where it belongs: no second copy under the list.
    expect(screen.getAllByText("Link của mục này không hợp lệ.")).toHaveLength(1);
    expect(field("Link", 2)).toHaveAttribute("aria-invalid", "true");
    expect(within(rows()[0]).queryByRole("alert")).not.toBeInTheDocument();
    expect(field("Tên", 2)).not.toHaveAttribute("aria-invalid");
    // The list is as it was typed, and editing it answers the refusal.
    expect(field("Link", 2)).toHaveValue("https://brand.example.com");
    await userEvent.type(field("Link", 2), "/v2");
    expect(within(rows()[1]).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a refusal on a name the same way", async () => {
    managerPage(
      [NHAP_MON, BRAND],
      [
        {
          match: "/api/units/ADS/library",
          method: "PUT",
          status: 422,
          body: {
            error: {
              code: "validation_error",
              message: "Tên mục không được để trống.",
              details: { reason: "library_name_missing", field: "name", index: 0 },
            },
          },
        },
      ],
    );
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    await userEvent.type(field("Tên", 1), " mới");
    await userEvent.click(save());
    await confirm();
    expect(await within(rows()[0]).findByRole("alert")).toHaveTextContent(
      "Tên mục không được để trống.",
    );
    expect(field("Tên", 1)).toHaveAttribute("aria-invalid", "true");
    expect(within(rows()[1]).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a refusal about the list as a whole under the list", async () => {
    managerPage(
      [NHAP_MON, BRAND],
      [
        {
          match: "/api/units/ADS/library",
          method: "PUT",
          status: 422,
          body: {
            error: {
              code: "validation_error",
              message: "Thư viện chỉ chứa tối đa 30 mục.",
              details: { reason: "library_too_many", field: "links" },
            },
          },
        },
      ],
    );
    await screen.findByRole("textbox", { name: "Tên · dòng 1" });
    await userEvent.type(field("Tên", 1), " mới");
    await userEvent.click(save());
    await confirm();
    expect(await screen.findByRole("status")).toHaveTextContent("Thư viện chỉ chứa tối đa 30 mục.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts from an empty library", async () => {
    managerPage([]);
    expect(await screen.findByText("Thư viện chưa có mục nào.")).toBeInTheDocument();
    expect(save()).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "+ Thêm mục" }));
    expect(rows()).toHaveLength(1);
  });

  it("is only a notice for somebody who may not manage it", async () => {
    const stub = managerPage([NHAP_MON], [], MEMBER([NHAP_MON]));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Thư viện do Trưởng phòng Design quản lý",
    );
    expect(calls(stub).some((call) => call.url.includes("/library"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Lưu" })).not.toBeInTheDocument();
  });
});
