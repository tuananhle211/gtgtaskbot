/**
 * Long drop-downs get a search box (2026-10-22): `Select` hands a list of
 * SEARCH_THRESHOLD options or more to `SearchableSelect`, which keeps the
 * native select (label, value, onChange, keyboard) and adds a panel with a
 * text box that filters without accents.
 */
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Select } from "@/components/pr";
import { foldText } from "@/components/searchable-select";

const PEOPLE = [
  "Nguyễn Văn An",
  "Trần Minh Khoa",
  "Lê Thu Hà",
  "Phạm Văn Kiên",
  "Đặng Khôi",
  "Hồ Quỳnh",
  "Vũ Hằng",
  "Dương An",
  "Huỳnh Thoa",
];

function Picker({ onChange, people = PEOPLE }: { onChange?: (value: string) => void; people?: string[] }) {
  const [value, setValue] = useState("");
  return (
    <label>
      Người làm
      <Select
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          onChange?.(event.target.value);
        }}
      >
        <option value="">Tất cả</option>
        {people.map((name, index) => (
          <option key={name} value={`u-${index}`}>
            {name}
          </option>
        ))}
      </Select>
    </label>
  );
}

describe("a long drop-down", () => {
  it("keeps the native select under its label and opens a searchable panel", async () => {
    const changed = vi.fn();
    render(<Picker onChange={changed} />);
    const select = screen.getByRole("combobox", { name: "Người làm" });
    expect(select).toHaveAttribute("data-searchable", "true");
    await userEvent.click(document.querySelector<HTMLElement>("[data-select-trigger]")!);
    const box = screen.getByRole("textbox", { name: "Tìm trong danh sách" });
    expect(box).toHaveFocus();
    // No accents, any case: "dang" finds "Đặng", "an" finds both Ans.
    await userEvent.type(box, "dang");
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Đặng Khôi",
    ]);
    await userEvent.keyboard("{Enter}");
    expect(changed).toHaveBeenCalledWith("u-4");
    expect(select).toHaveValue("u-4");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(select).toHaveFocus();
  });

  it("opens from the keyboard, seeds the search with the letter typed, closes on Escape", async () => {
    render(<Picker />);
    const select = screen.getByRole("combobox", { name: "Người làm" });
    select.focus();
    await userEvent.keyboard("h");
    expect(screen.getByRole("textbox", { name: "Tìm trong danh sách" })).toHaveValue("h");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("still answers to selectOptions on the native select", async () => {
    const changed = vi.fn();
    render(<Picker onChange={changed} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Người làm" }), "u-2");
    expect(changed).toHaveBeenCalledWith("u-2");
  });

  it("leaves a short list a plain select", () => {
    render(<Picker people={["A", "B"]} />);
    expect(screen.getByRole("combobox", { name: "Người làm" })).not.toHaveAttribute("data-searchable");
    expect(document.querySelector("[data-select-trigger]")).toBeNull();
  });

  it("folds accents and đ", () => {
    expect(foldText("Đặng Quỳnh")).toBe("dang quynh");
  });
});
