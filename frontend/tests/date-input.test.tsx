/** The app's own date boxes: always dd/mm/yyyy [HH:mm], 24h, on any machine. */
import { describe, expect, it } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DateInput, DateTimeInput, maskDate, parseDate } from "@/components/date-input";
import { Modal } from "@/components/modal";

function Harness({ kind, start = "" }: { kind: "date" | "datetime"; start?: string }) {
  const [value, setValue] = useState(start);
  const Box = kind === "date" ? DateInput : DateTimeInput;
  return (
    <>
      <label>
        Ngày
        <Box value={value} onChange={(event) => setValue(event.target.value)} />
      </label>
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("the date boxes", () => {
  it("show a native value the app's way", () => {
    render(<Harness kind="datetime" start="2026-10-21T17:30" />);
    expect(screen.getByLabelText("Ngày")).toHaveValue("21/10/2026 17:30");
    expect(screen.getByLabelText("Ngày")).toHaveAttribute("placeholder", "dd/mm/yyyy HH:mm");
  });

  it("lay out typed digits and send the value once it is whole", async () => {
    render(<Harness kind="datetime" />);
    const box = screen.getByLabelText("Ngày");
    await userEvent.type(box, "2110202617");
    expect(box).toHaveValue("21/10/2026 17");
    expect(screen.getByTestId("value")).toHaveTextContent("");
    await userEvent.type(box, "45");
    expect(box).toHaveValue("21/10/2026 17:45");
    expect(screen.getByTestId("value")).toHaveTextContent("2026-10-21T17:45");
  });

  it("never sends a day that does not exist, and puts the last good one back", async () => {
    render(<Harness kind="date" start="2026-10-05" />);
    const box = screen.getByLabelText("Ngày");
    await userEvent.clear(box);
    expect(screen.getByTestId("value")).toHaveTextContent("");
    await userEvent.type(box, "31022026");
    expect(screen.getByTestId("value")).toHaveTextContent("");
    await userEvent.type(box, "{backspace}{backspace}{backspace}{backspace}{backspace}{backspace}{backspace}{backspace}{backspace}{backspace}05102026");
    expect(screen.getByTestId("value")).toHaveTextContent("2026-10-05");
    expect(box).toHaveValue("05/10/2026");
  });

  it("parse and mask by the same rules", () => {
    expect(maskDate("05102026", "date")).toBe("05/10/2026");
    expect(parseDate("29/02/2028 23:59", "datetime")).toBe("2028-02-29T23:59");
    expect(parseDate("29/02/2026", "date")).toBeNull();
    expect(parseDate("21/10/2026 24:00", "datetime")).toBeNull();
  });

  it("open the app's own picker: a day, then the time, 24h", async () => {
    render(<Harness kind="datetime" start="2026-10-21T17:30" />);
    await userEvent.click(screen.getByRole("button", { name: "Chọn ngày giờ" }));
    const picker = screen.getByRole("dialog", { name: "Chọn ngày giờ" });
    expect(picker).toHaveTextContent("Tháng 10, 2026");
    expect(screen.getByRole("button", { name: "Thứ Tư, 21/10/2026" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Thứ Sáu, 23/10/2026" }));
    expect(screen.getByTestId("value")).toHaveTextContent("2026-10-23T17:30");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Giờ" }), "09");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Phút" }), "15");
    expect(screen.getByTestId("value")).toHaveTextContent("2026-10-23T09:15");
    await userEvent.click(screen.getByRole("button", { name: "23:59" }));
    expect(screen.getByLabelText("Ngày")).toHaveValue("23/10/2026 23:59");
    await userEvent.click(screen.getByRole("button", { name: "Tháng sau" }));
    expect(picker).toHaveTextContent("Tháng 11, 2026");
    await userEvent.click(screen.getByRole("button", { name: "Xong" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("close a date-only picker on the day chosen, and on Escape", async () => {
    render(<Harness kind="date" />);
    await userEvent.click(screen.getByRole("button", { name: "Chọn ngày" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Chọn ngày" }));
    await userEvent.click(screen.getByRole("button", { name: "Hôm nay" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("value").textContent).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("inside a dialog, Escape closes the picker and leaves the dialog open", async () => {
    let closed = false;
    render(
      <Modal open onClose={() => (closed = true)} title="Giao việc">
        <Harness kind="datetime" start="2026-10-21T17:30" />
      </Modal>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Chọn ngày giờ" }));
    expect(screen.getByRole("dialog", { name: "Chọn ngày giờ" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Chọn ngày giờ" })).not.toBeInTheDocument();
    expect(closed).toBe(false);
    // A day picked in the picker does not close the dialog either.
    await userEvent.click(screen.getByRole("button", { name: "Chọn ngày giờ" }));
    await userEvent.click(screen.getByRole("button", { name: "Thứ Năm, 22/10/2026" }));
    expect(closed).toBe(false);
    expect(screen.getByTestId("value")).toHaveTextContent("2026-10-22T17:30");
  });
});
