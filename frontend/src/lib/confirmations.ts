import type { ReactNode } from "react";

import type { ConfirmSpec } from "@/components/confirm";
import {
  capabilityLabel,
  decisionLabelAt,
  monthLabel,
  stageLabel,
  transitionLabel,
} from "@/lib/labels";

/**
 * Every confirmation the panel asks, written once.
 *
 * ## Why the copy lives here and not at the call sites
 *
 * Step 1F.2.8. A confirmation is only worth interrupting somebody for if it
 * tells them something they did not already know from the button they pressed,
 * and that is a writing problem rather than a component problem. Keeping the
 * sentences in one file makes three things possible that scattering them does
 * not: they can be read together and kept in one voice, a test can assert that
 * none of them has degenerated into "Bạn có chắc không?", and the inventory
 * below can name every state-changing action in the panel and say what happens
 * to it.
 *
 * ## The rule every one of them follows
 *
 * Three questions, answered in two fields:
 *
 * 1. **what will happen** - the title, phrased as a question with the verb in
 *    it. "Duyệt nội dung này?", not "Xác nhận";
 * 2. **to what or whom** - the object, named. A person by name, a batch by
 *    count and step, a channel by platform;
 * 3. **what changes as a result** - the body, in the future tense, describing
 *    the state or the responsibility that moves. Not a warning, not a plea.
 *
 * The confirm label repeats the verb, so the last words under the cursor are
 * the action: `[Phân công Hà Chi]`, never `[Lưu]`.
 *
 * ## Which are destructive
 *
 * `destructive` is for what cannot be undone or what takes something away:
 * rejecting, cancelling, deleting, revoking a right, disconnecting an account,
 * unassigning somebody mid-job. Approving, claiming, assigning and completing
 * are ordinary primary actions - they move work forward, and dressing them in
 * red teaches people that red means nothing.
 */

/** A person's name, or a readable stand-in. Never a bare UUID in a sentence. */
const who = (name: string | undefined | null) => name?.trim() || "người này";

// --- Content: the review gates ----------------------------------------------

/**
 * Approving, requesting a revision, or rejecting one item at one gate.
 *
 * The gate decides the wording, because "Duyệt" at `INTERNAL_REVIEW` is "Duyệt
 * nội bộ" and the next step differs at each of the three - see `decisionLabelAt`
 * and `transitionLabel`, which already own that vocabulary.
 */
export function decisionConfirmation(stage: string, decision: string): ConfirmSpec {
  const verb = decisionLabelAt(stage, decision);
  if (decision === "APPROVED") {
    return {
      title: stage === "INTERNAL_REVIEW" ? "Duyệt nội bộ nội dung này?" : "Duyệt nội dung này?",
      description: "Nội dung sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.",
      confirmLabel: verb,
      variant: "primary",
    };
  }
  if (decision === "REVISION_REQUESTED") {
    return {
      title: "Yêu cầu chỉnh sửa nội dung này?",
      description:
        "Nội dung sẽ quay lại cho người thực hiện sửa, và bước duyệt hiện tại sẽ phải làm lại sau khi sửa xong.",
      confirmLabel: verb,
      variant: "destructive",
    };
  }
  return {
    title: "Từ chối nội dung này?",
    description:
      "Nội dung sẽ bị dừng lại và không đi tiếp trong quy trình. Việc này không tự hoàn tác được.",
    confirmLabel: verb,
    variant: "destructive",
  };
}

/**
 * The bulk approval, and the only confirmation that summarises a set.
 *
 * `titles` is shown as a sample - five at most - and the rest are counted.
 * Rendering eighty titles would turn the one dialog somebody must actually read
 * into a wall they scroll past, and the count is the fact that matters.
 *
 * `truncated` is not decoration: when a select-all hit the server's batch limit
 * the dialog has to say that this approves the first N of a longer queue, or the
 * button would be promising something it does not do.
 */
export function bulkApproveConfirmation({
  count,
  stage,
  titles,
  eligibleTotal,
}: {
  count: number;
  /** The workflow stage the batch is standing at, e.g. `INTERNAL_REVIEW`. */
  stage: string;
  titles: string[];
  /** The whole eligible queue, when it is larger than the batch. */
  eligibleTotal?: number;
}): ConfirmSpec {
  const step = stageLabel(stage);
  const shown = titles.slice(0, 5);
  const rest = Math.max(0, count - shown.length);
  const truncated = eligibleTotal !== undefined && eligibleTotal > count;
  return {
    title: `Duyệt ${count} nội dung?`,
    description:
      `${count} nội dung ở bước ${step} sẽ được ghi nhận là đã duyệt và chuyển sang bước tiếp theo.` +
      (truncated
        ? ` Lần này duyệt ${count} nội dung đầu tiên trong ${eligibleTotal} nội dung bạn duyệt được; phần còn lại chọn lại sau.`
        : ""),
    confirmLabel: `Duyệt ${count} nội dung`,
    variant: "primary",
    count,
    details:
      shown.length > 0
        ? `${shown.join(" · ")}${rest > 0 ? ` và ${rest} nội dung khác` : ""}`
        : undefined,
  };
}

/**
 * Step 1F.2.3f.6. *"Lưu trữ nội dung kỳ trước"*: one closed month's published
 * output, `PUBLISHED -> ARCHIVED`, as one batch or not at all. The count is the
 * server's candidate total, never a number the browser added up.
 */
export function bulkArchiveConfirmation({
  count,
  period,
  eligibleTotal,
}: {
  count: number;
  /** `YYYY-MM`, the closed month being put away. */
  period: string;
  /** The whole month, when it is larger than one batch. */
  eligibleTotal?: number;
}): ConfirmSpec {
  const month = monthLabel(period);
  const truncated = eligibleTotal !== undefined && eligibleTotal > count;
  return {
    title: `Lưu trữ ${count} nội dung đã đăng trong kỳ ${month.replace("Tháng ", "")}?`,
    description:
      `${count} nội dung đã đăng trong ${month} sẽ chuyển sang Lưu trữ. ` +
      "Lịch sử đăng và số liệu của chúng được giữ nguyên; việc này không tự hoàn tác được." +
      (truncated
        ? ` Lần này lưu trữ ${count} nội dung đầu tiên trong ${eligibleTotal}; phần còn lại bấm lại sau.`
        : ""),
    confirmLabel: `Lưu trữ ${count} nội dung`,
    variant: "primary",
    count,
  };
}

/** Any other workflow move offered by the server's action list. */
export function transitionConfirmation(target: string): ConfirmSpec {
  const verb = transitionLabel(target);
  if (target === "CANCELLED") {
    return {
      title: "Hủy nội dung này?",
      description: "Nội dung sẽ dừng lại ở trạng thái đã hủy và không đi tiếp trong quy trình.",
      confirmLabel: verb,
      variant: "destructive",
    };
  }
  if (target === "TEAM_LEAD_REVIEW") {
    // The direct submission. Confirmed like every other workflow move, and
    // the one thing the dialog adds to the button is the consequence the
    // button's own wording leaves out: the AI review will not run. Not
    // destructive - it moves work forward, and nothing human is skipped.
    return {
      title: "Gửi thẳng Trưởng nhóm duyệt?",
      description:
        "Nội dung này sẽ bỏ qua bước AI review và chuyển sang Chờ duyệt Trưởng nhóm.",
      confirmLabel: "Gửi duyệt",
      variant: "primary",
    };
  }
  return {
    title: `${verb}?`,
    description: `Nội dung sẽ chuyển sang bước ${stageLabel(target)}.`,
    confirmLabel: verb,
    variant: "primary",
  };
}

/** Taking back the last reversible decision. */
export const undoConfirmation = (what: string): ConfirmSpec => ({
  title: "Hoàn tác thao tác vừa rồi?",
  description: `${what} sẽ được thu hồi và nội dung quay lại bước trước đó.`,
  confirmLabel: "Hoàn tác",
  variant: "destructive",
});

/** Permanent deletion, before publication. */
export const deleteContentConfirmation = (code: string): ConfirmSpec => ({
  title: `Xóa vĩnh viễn ${code}?`,
  description:
    "Toàn bộ nội dung, phiên bản và lịch sử của mục này sẽ bị xóa khỏi hệ thống. Việc này không hoàn tác được.",
  confirmLabel: "Xóa vĩnh viễn",
  variant: "destructive",
});

// --- Production: who is responsible -----------------------------------------

export const claimProductionConfirmation = (): ConfirmSpec => ({
  title: "Nhận sản xuất nội dung này?",
  description:
    "Bạn sẽ trở thành người phụ trách sản xuất và nội dung sẽ chuyển sang bước Đang sản xuất.",
  confirmLabel: "Nhận sản xuất",
  variant: "primary",
});

export const startProductionConfirmation = (): ConfirmSpec => ({
  title: "Bắt đầu sản xuất nội dung này?",
  description: "Nội dung sẽ chuyển sang bước Đang sản xuất và bạn là người thực hiện.",
  confirmLabel: "Bắt đầu sản xuất",
  variant: "primary",
});

/**
 * Assigning, reassigning and unassigning - three sentences, one function.
 *
 * They are one function because they are one decision with three outcomes, and
 * writing them apart is how "Chuyển người sản xuất" ends up describing an
 * assignment. `current` is who holds it now, which is the only thing that makes
 * a reassignment different from an assignment.
 */
export function assignProducerConfirmation({
  name,
  current,
}: {
  /** The new producer, or `null` to clear the assignment. */
  name: string | null;
  /** Who is on it now, if anybody. */
  current?: string | null;
}): ConfirmSpec {
  if (name === null) {
    return {
      title: `Bỏ phân công ${who(current)}?`,
      description:
        "Nội dung sẽ quay lại trạng thái chưa có người phụ trách sản xuất, và công việc đang làm dở sẽ bị gián đoạn.",
      confirmLabel: "Bỏ phân công",
      variant: "destructive",
    };
  }
  if (current) {
    return {
      title: `Chuyển người sản xuất sang ${who(name)}?`,
      description: `Người phụ trách sản xuất hiện tại sẽ được thay thế bằng ${who(name)}.`,
      confirmLabel: `Chuyển sang ${who(name)}`,
      variant: "primary",
    };
  }
  return {
    title: `Phân công ${who(name)} sản xuất?`,
    description: `${who(name)} sẽ trở thành người phụ trách sản xuất của nội dung này.`,
    confirmLabel: `Phân công ${who(name)}`,
    variant: "primary",
  };
}

/**
 * Changing one of the three classification fields on an item.
 *
 * Small controls, and one of them is not small at all: **the content type feeds
 * scoped approval grants.** A grant covers "Kịch bản video ngắn on these two
 * channels", so reclassifying an item moves it into or out of somebody's
 * authority - and the distribution mode decides which policy pack the AI review
 * is run against. Neither is a preference; both are business state, and both
 * used to be sent on the `change` event of a dropdown.
 *
 * Priority is the least consequential of the three and confirms anyway, because
 * a rule that asked people to judge which dropdowns are load-bearing is a rule
 * that gets it wrong on the one that is.
 */
export function fieldChangeConfirmation({
  field,
  value,
  note,
}: {
  /** The field's name as it reads on screen: "Loại nội dung". */
  field: string;
  /** The chosen value, in words. */
  value: string;
  /** What follows from it, when something does. */
  note?: string;
}): ConfirmSpec {
  return {
    title: `Đổi ${field.toLowerCase()} thành “${value}”?`,
    description: note ?? `${field} của nội dung này sẽ được ghi nhận là “${value}”.`,
    confirmLabel: `Đổi thành ${value}`,
    variant: "primary",
  };
}

// --- Publication -------------------------------------------------------------

export const registerPublicationConfirmation = (channel: string): ConfirmSpec => ({
  title: `Ghi nhận đã đăng trên ${channel}?`,
  description:
    "Nội dung sẽ được ghi nhận là đã xuất bản trên kênh này, và bắt đầu được tính vào báo cáo.",
  confirmLabel: "Ghi nhận đã đăng",
  variant: "primary",
});

export const reversePublicationConfirmation = (channel: string): ConfirmSpec => ({
  title: `Thu hồi bản đăng trên ${channel}?`,
  description:
    "Bản đăng này sẽ thôi được tính là đang hoạt động và không còn vào báo cáo kể từ bây giờ.",
  confirmLabel: "Thu hồi bản đăng",
  variant: "destructive",
});

// --- Permissions -------------------------------------------------------------

export const grantCapabilityConfirmation = (
  capability: string,
  name: string | null | undefined,
): ConfirmSpec => ({
  title: `Cấp quyền ${capabilityLabel(capability)} cho ${who(name)}?`,
  description: `${who(name)} sẽ duyệt được đúng phần nội dung và kênh bạn vừa chọn, kể cả khi vai trò hiện tại chưa có quyền duyệt.`,
  confirmLabel: "Cấp quyền",
  variant: "primary",
});

export const revokeCapabilityConfirmation = (
  capability: string,
  name: string | null | undefined,
): ConfirmSpec => ({
  title: `Thu hồi quyền ${capabilityLabel(capability)} của ${who(name)}?`,
  description: "Quyền này sẽ ngừng có hiệu lực ngay lập tức.",
  confirmLabel: "Thu hồi quyền",
  variant: "destructive",
});

// --- Thành viên (membership, phase 1) ----------------------------------------

/** Changing the one base role. Names the person and both roles. */
export const changeRoleConfirmation = (
  name: string,
  fromLabel: string,
  toLabel: string,
): ConfirmSpec => ({
  title: `Đổi vai trò của ${name} từ ${fromLabel} sang ${toLabel}?`,
  description:
    "Vai trò mới có hiệu lực ngay ở lần thao tác tiếp theo của họ. Quyền duyệt cấp thêm (nếu có) giữ nguyên, không đổi theo vai trò.",
  confirmLabel: "Đổi vai trò",
});

/**
 * *Vô hiệu hóa thành viên.* Reversible, and the dialog says what stays: the
 * row, the history, the grants. It also says what the person still holds - the
 * counts come from the server and nothing here reassigns them.
 */
export const deactivateMemberConfirmation = (
  name: string,
  held: {
    content_owned: number;
    open_work: number;
    open_tasks: number;
    kpi_drafts: number;
    active_grants: number;
  } | null,
): ConfirmSpec => {
  const lines: string[] = [];
  if (held) {
    if (held.content_owned > 0) lines.push(`${held.content_owned} nội dung đang phụ trách`);
    if (held.open_work > 0) lines.push(`${held.open_work} công việc đang mở`);
    if (held.open_tasks > 0) lines.push(`${held.open_tasks} task chưa hoàn thành`);
    if (held.kpi_drafts > 0) lines.push(`${held.kpi_drafts} bản nháp KPI`);
    if (held.active_grants > 0) lines.push(`${held.active_grants} quyền duyệt cấp thêm`);
  }
  const holding =
    lines.length > 0
      ? ` ${name} đang giữ: ${lines.join(", ")}. Những mục này giữ nguyên, không được tự chuyển cho ai khác.`
      : "";
  return {
    title: `Vô hiệu hóa thành viên ${name}?`,
    description: `Họ sẽ không đăng nhập hay thao tác được nữa từ lần yêu cầu tiếp theo. Lịch sử, vai trò và quyền duyệt cấp thêm được giữ lại và hoạt động trở lại khi kích hoạt lại.${holding}`,
    confirmLabel: "Vô hiệu hóa",
    variant: "destructive",
  };
};

/** *Loại khỏi PR.* Terminal in phase 1; the row is never deleted. */
export const revokeMemberConfirmation = (name: string): ConfirmSpec => ({
  title: `Loại ${name} khỏi PR?`,
  description:
    "Tài khoản mất quyền truy cập và không thể kích hoạt lại trong giai đoạn này. Toàn bộ lịch sử nội dung, công việc và KPI của họ được giữ nguyên, và vẫn xem được ở đây.",
  confirmLabel: "Loại khỏi PR",
  variant: "destructive",
});

/** Undoing a suspension. Same role, same grants, from this moment. */
export const reactivateMemberConfirmation = (name: string): ConfirmSpec => ({
  title: `Kích hoạt lại ${name}?`,
  description:
    "Họ đăng nhập và thao tác được ngay, với đúng vai trò và quyền duyệt cấp thêm như trước khi bị vô hiệu hóa.",
  confirmLabel: "Kích hoạt lại",
});

// --- Channels and integrations ----------------------------------------------

export const disconnectChannelConfirmation = (provider: string): ConfirmSpec => ({
  title: `Ngắt kết nối ${provider} này?`,
  description:
    "MeoChat sẽ ngừng đồng bộ dữ liệu từ tài khoản này cho đến khi được kết nối lại. Số liệu đã ghi nhận vẫn được giữ.",
  confirmLabel: "Ngắt kết nối",
  variant: "destructive",
});

export const replaceConnectionConfirmation = (provider: string): ConfirmSpec => ({
  title: `Thay tài khoản ${provider} đang kết nối?`,
  description: "Kết nối hiện tại sẽ bị thay thế và việc đồng bộ sẽ chuyển sang tài khoản mới chọn.",
  confirmLabel: "Thay kết nối",
  variant: "destructive",
});

export const closeChannelAssignmentConfirmation = (name: string): ConfirmSpec => ({
  title: `Kết thúc phân công kênh của ${who(name)}?`,
  description: `${who(name)} sẽ thôi phụ trách kênh này kể từ hôm nay.`,
  confirmLabel: "Kết thúc phân công",
  variant: "destructive",
});

// --- Tasks -------------------------------------------------------------------

export const taskStatusConfirmation = (status: string, label: string): ConfirmSpec => ({
  title: `Chuyển task sang ${label}?`,
  description:
    status === "CANCELLED"
      ? "Task sẽ bị hủy và không còn nằm trong việc cần làm của ai."
      : `Trạng thái task sẽ được ghi nhận là ${label}.`,
  confirmLabel: label,
  variant: status === "CANCELLED" ? "destructive" : "primary",
});

export const assignTaskConfirmation = (name: string | null | undefined): ConfirmSpec => ({
  title: `Giao task cho ${who(name)}?`,
  description: `${who(name)} sẽ nhận task này và thấy nó trong việc cần làm của mình.`,
  confirmLabel: `Giao cho ${who(name)}`,
  variant: "primary",
});

// --- Deleting somebody's own contributions -----------------------------------

// --- The Work Ledger, M1 ----------------------------------------------------
//
// Every one of these changes who is answerable for something or whether work
// counts. Starting your own work does not - see `ACTION_INVENTORY`.

/** Taking on somebody's proposal. It enters their workload from this moment. */
export const acceptWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Chấp nhận đề xuất công việc này?",
  description: `“${title}” sẽ trở thành công việc chính thức và nằm trong khối lượng của người thực hiện.`,
  confirmLabel: "Chấp nhận",
});

export const rejectWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Từ chối đề xuất công việc này?",
  description: `“${title}” sẽ không được ghi nhận là công việc. Đề xuất vẫn được lưu lại kèm lý do.`,
  confirmLabel: "Từ chối",
  variant: "destructive",
});

/**
 * Starting assigned work.
 *
 * M1 shipped this without a dialog on the reasoning that it changes no
 * responsibility. That was the wrong test: the panel's rule is that **any
 * change of business state asks first**, and `ACCEPTED -> IN_PROGRESS` is a
 * workflow transition somebody else can see - a manager reading the board now
 * believes this job is under way.
 *
 * Not destructive, and not dressed as such: it moves work forward, which is an
 * ordinary primary action.
 */
export const startWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Bắt đầu công việc này?",
  description: `“${title}” sẽ chuyển sang trạng thái Đang làm.`,
  confirmLabel: "Bắt đầu",
});

/**
 * Reporting work finished.
 *
 * The description says what does **not** happen, because that is the thing
 * somebody pressing this button is most likely to assume wrongly: finishing is
 * a claim, and the work is not counted until somebody else confirms it.
 */
export const completeWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Báo hoàn thành công việc này?",
  description: `“${title}” sẽ chuyển sang trạng thái chờ xác nhận. Công việc chỉ được ghi nhận sau khi người khác xác nhận.`,
  confirmLabel: "Báo hoàn thành",
});

/**
 * The one that counts.
 *
 * Names the consequence in the words the ledger uses - "ghi nhận" - because
 * this is the click that puts a number on somebody's record.
 */
export const approveWorkConfirmation = (title: string, people: number): ConfirmSpec => ({
  title: "Xác nhận công việc đã hoàn thành?",
  description:
    people > 1
      ? `“${title}” sẽ được ghi nhận cho cả ${people} người thực hiện. Sau khi xác nhận thì không sửa lại được.`
      : `“${title}” sẽ được ghi nhận vào công việc của người thực hiện. Sau khi xác nhận thì không sửa lại được.`,
  confirmLabel: "Xác nhận hoàn thành",
});

/**
 * M4B. Starting a recurring routine.
 *
 * The one confirmation on the Định kỳ screen, and the only one it needs. What
 * this button does that no other button on the screen does is make the person
 * pressing it the **standing authorization** for every job the routine
 * generates from now on - so the description says both halves of that: work will
 * appear under their name, and it still has to be confirmed by somebody else
 * when it is finished. The second sentence exists because "bật lịch" is exactly
 * the kind of act somebody assumes is only a schedule.
 *
 * Not destructive: starting a routine moves work forward, and dressing it in red
 * would teach people that red means nothing.
 */
export const activateRecurringTemplateConfirmation = (
  name: string,
  schedule: string,
): ConfirmSpec => ({
  title: "Bật việc định kỳ này?",
  description:
    `Hệ thống sẽ tự tạo công việc “${name}” theo lịch ${schedule}, và bạn là người giao. ` +
    "Công việc được tạo vẫn cần một người khác xác nhận khi hoàn thành.",
  confirmLabel: "Bật chạy",
});

/**
 * M4B. Retiring a routine.
 *
 * Destructive because it cannot be undone - an ended template is copied, not
 * restarted - and the description says what is *not* taken away, because that is
 * the thing somebody is most likely to assume wrongly: ending the instruction
 * does not cancel the obligations it already created.
 */
export const endRecurringTemplateConfirmation = (name: string): ConfirmSpec => ({
  title: "Kết thúc việc định kỳ này?",
  description:
    `“${name}” sẽ ngừng sinh việc và không bật lại được. ` +
    "Công việc đã sinh ra vẫn giữ nguyên và vẫn phải làm.",
  confirmLabel: "Kết thúc",
  variant: "destructive",
});

/**
 * M4B. Deleting a draft the scheduler never reached.
 *
 * Offered only for a draft with no occurrence history at all - the server
 * refuses anything else, including a template whose only firing failed, because
 * that row is an answer somebody may need.
 */
export const deleteRecurringTemplateConfirmation = (name: string): ConfirmSpec => ({
  title: "Xoá việc định kỳ này?",
  description: `“${name}” chưa sinh việc nào nên sẽ được xoá hẳn.`,
  confirmLabel: "Xoá",
  variant: "destructive",
});

/**
 * M4A. Confirming a whole queue at once.
 *
 * The count is in the title because it is the thing to check before pressing:
 * "Xác nhận 40 công việc?" is a number somebody can compare against what they
 * ticked, and "Xác nhận các công việc đã chọn?" is not. The description states
 * the two properties that make this safe to press - all of it or none of it,
 * and it is final.
 */
export const bulkValidateWorkConfirmation = (count: number): ConfirmSpec => ({
  title: `Xác nhận ${count} công việc?`,
  description:
    "Tất cả sẽ được ghi nhận cùng lúc, hoặc không có công việc nào được ghi nhận. " +
    "Sau khi xác nhận thì không sửa lại được.",
  confirmLabel: `Xác nhận ${count} công việc`,
});

export const reopenWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Trả lại công việc này để làm tiếp?",
  description: `“${title}” sẽ quay về trạng thái đang làm và chưa được ghi nhận.`,
  confirmLabel: "Trả lại",
  variant: "destructive",
});

export const cancelWorkConfirmation = (title: string): ConfirmSpec => ({
  title: "Hủy công việc này?",
  description: `“${title}” sẽ được đánh dấu đã hủy và không tính vào công việc của ai. Dữ liệu vẫn được giữ lại.`,
  confirmLabel: "Hủy công việc",
  variant: "destructive",
});

export const addWorkContributorConfirmation = (name: string | null | undefined): ConfirmSpec => ({
  title: `Thêm ${who(name)} vào công việc này?`,
  description: `${who(name)} sẽ được ghi nhận một phần việc riêng khi công việc được xác nhận.`,
  confirmLabel: `Thêm ${who(name)}`,
});

export const removeWorkContributorConfirmation = (
  name: string | null | undefined,
): ConfirmSpec => ({
  title: `Gỡ ${who(name)} khỏi công việc này?`,
  description: `${who(name)} sẽ không còn được ghi nhận phần việc này.`,
  confirmLabel: `Gỡ ${who(name)}`,
  variant: "destructive",
});

export const workDeadlineConfirmation = (title: string): ConfirmSpec => ({
  title: "Đổi hạn công việc này?",
  description: `Hạn mới của “${title}” sẽ được ghi vào lịch sử kèm hạn cũ.`,
  confirmLabel: "Đổi hạn",
});

// --- Hạn mức KPI (quota engine, M2) -----------------------------------------
//
// Every one of these changes **how much of somebody's work counts towards their
// KPI**, which is why they ask first even though none of them deletes anything.
// The wording says "đủ điều kiện tính KPI" and never "tính điểm": M2 awards no
// points, and a dialog promising them would describe a feature that does not
// exist.

/**
 * Putting a plan into force. **The click that makes a target decide anything.**
 *
 * The description names the consequence in the words the module uses, and says
 * the part somebody is most likely to assume wrongly: an approved plan cannot
 * be edited afterwards, only revised.
 */
export const approveWorkPlanConfirmation = (
  name: string | null | undefined,
  period: string,
): ConfirmSpec => ({
  title: "Duyệt kế hoạch KPI này?",
  description: `Hạn mức của ${who(name)} trong kỳ ${period} sẽ có hiệu lực ngay, và công việc đã ghi nhận trong kỳ sẽ được tính lại theo hạn mức mới. Kế hoạch đã duyệt không sửa trực tiếp được — muốn đổi thì tạo bản điều chỉnh.`,
  confirmLabel: "Duyệt kế hoạch",
});

/**
 * Approving a replacement version.
 *
 * Says what happens to the old one, because "duyệt" on a revision quietly
 * retires a plan somebody else approved and that has to be visible before the
 * click, not only in the history afterwards.
 */
export const approveWorkPlanRevisionConfirmation = (
  name: string | null | undefined,
  version: number,
): ConfirmSpec => ({
  title: `Duyệt bản điều chỉnh v${version}?`,
  description: `Bản đang áp dụng của ${who(name)} sẽ chuyển thành "đã thay thế" và bản v${version} có hiệu lực thay vào đó. Việc đã ghi nhận trong kỳ sẽ được tính lại — có phần đang vượt hạn mức có thể trở thành đủ điều kiện, và ngược lại.`,
  confirmLabel: `Duyệt v${version}`,
});

/** Starting a revision. Not destructive: the version in force keeps deciding. */
export const reviseWorkPlanConfirmation = (
  name: string | null | undefined,
  version: number,
): ConfirmSpec => ({
  title: "Tạo bản điều chỉnh kế hoạch KPI?",
  description: `Một bản nháp v${version + 1} sẽ được tạo từ bản đang áp dụng của ${who(name)}. Bản đang áp dụng vẫn giữ nguyên hiệu lực cho tới khi bản điều chỉnh được duyệt.`,
  confirmLabel: "Tạo bản điều chỉnh",
});

/**
 * KPI self-service. **Gửi duyệt.** Says what it does *not* do, which is what
 * somebody submitting for the first time most needs to hear: nothing takes
 * effect, the plan in force stays, and the draft is locked for them until a
 * manager decides.
 */
export const submitWorkPlanConfirmation = (version: number, period: string): ConfirmSpec => ({
  title: `Gửi bản v${version} cho trưởng phòng duyệt?`,
  description: `Kế hoạch KPI kỳ ${period} của bạn sẽ được chuyển cho trưởng phòng xem xét. Trong lúc chờ duyệt bạn không sửa được; kế hoạch đang áp dụng (nếu có) vẫn giữ nguyên hiệu lực cho tới khi bản này được duyệt.`,
  confirmLabel: "Gửi duyệt",
});

/**
 * **Trả lại để chỉnh sửa.** Not destructive: the same version reopens for its
 * author and the plan in force is untouched. The note is the useful part.
 */
export const returnWorkPlanConfirmation = (
  name: string | null | undefined,
  version: number,
): ConfirmSpec => ({
  title: `Trả lại bản v${version} để chỉnh sửa?`,
  description: `${who(name)} sẽ sửa lại bản v${version} và gửi duyệt lại. Không tạo bản mới; kế hoạch đang áp dụng (nếu có) vẫn giữ nguyên hiệu lực.`,
  confirmLabel: "Trả lại",
});

export const removeWorkQuotaConfirmation = (workType: string): ConfirmSpec => ({
  title: "Bỏ hạn mức của loại việc này?",
  description: `“${workType}” sẽ không còn hạn mức trong bản nháp này. Nếu bản nháp được duyệt mà thiếu loại việc này, công việc thuộc loại đó sẽ ở trạng thái "chưa có hạn mức KPI".`,
  confirmLabel: "Bỏ hạn mức",
  variant: "destructive",
});

export const discardWorkPlanConfirmation = (version: number): ConfirmSpec => ({
  title: `Bỏ bản nháp v${version}?`,
  description: `Bản nháp sẽ được đánh dấu đã bỏ và không thể duyệt nữa. Dữ liệu vẫn được giữ lại trong lịch sử.`,
  confirmLabel: "Bỏ bản nháp",
  variant: "destructive",
});

/**
 * Recomputing an open period.
 *
 * Confirmed because it **can change eligibility state**: a contribution that
 * read as "chưa có hạn mức" can become "vượt hạn mức", and somebody looking at
 * their own figures deserves to know a click did that rather than a bug.
 */
export const reconcileEligibilityConfirmation = (period: string): ConfirmSpec => ({
  title: `Tính lại điều kiện KPI của kỳ ${period}?`,
  description:
    "Toàn bộ công việc đã ghi nhận trong kỳ sẽ được đối chiếu lại với hạn mức đã duyệt. Kết quả có thể thay đổi trạng thái của một số đầu việc. Kỳ đã chốt hoặc đã khóa sẽ bị từ chối.",
  confirmLabel: "Tính lại",
});

export const deleteItemConfirmation = (what: string, label: string): ConfirmSpec => ({
  title: `Xóa ${what} này?`,
  description: `${label} sẽ bị xóa khỏi nội dung. Việc này không hoàn tác được.`,
  confirmLabel: "Xóa",
  variant: "destructive",
});

/**
 * How a state-changing action in this panel satisfies the confirmation policy.
 *
 * * `dialog` - it opens a {@link ConfirmDialog} before anything is sent;
 * * `parameter-modal` - it already opens a form whose purpose is to collect
 *   what the action needs, and **that form's submit button is the
 *   confirmation**. Stacking a second dialog on top of a dialog somebody just
 *   filled in is a second click that carries no new information, so instead the
 *   submit button names the action - "Phân công Hà Chi", not "Lưu";
 * * `none` - deliberately unconfirmed, with the reason recorded.
 */
export type ConfirmationPolicy = "dialog" | "parameter-modal" | "none";

export interface ActionInventoryEntry {
  /** Where it is, in words a reader can find on screen. */
  readonly action: string;
  readonly policy: ConfirmationPolicy;
  /** Required for `none` and `parameter-modal`; why that is the right answer. */
  readonly reason?: string;
}

/**
 * Every meaningful state-changing action in the panel, and its policy.
 *
 * This is the maintainable half of the regression strategy. A test that swept
 * the source for `<button>` and demanded each one be confirmed would fail on
 * every tab strip and pass on any mutation somebody hid behind a `<div
 * onClick>` - brittle in one direction and blind in the other. This list is the
 * opposite: it is short, it is read by a test that checks each entry's copy is
 * specific and each `none` carries a reason, and adding a mutation without
 * adding a line here is the kind of omission a reviewer sees in the diff.
 *
 * It is documentation with a test attached, not a substitute for the shared
 * component: the component is what makes joining the policy easier than not.
 */
/**
 * Retiring a kind of work. **M2.5.**
 *
 * Asks because it changes what the whole department may file next month, and
 * the person doing it is usually thinking about one stale row rather than about
 * every picker in the product. The second sentence is the one that matters: the
 * fear this dialog answers is "will I lose the history", and the answer is no.
 */
export const deactivateWorkTypeConfirmation = (name: string): ConfirmSpec => ({
  title: "Tắt loại công việc này?",
  description: `“${name}” sẽ không còn xuất hiện khi tạo công việc hoặc KPI mới. Dữ liệu lịch sử vẫn được giữ nguyên.`,
  confirmLabel: "Tắt",
  variant: "destructive",
});

/**
 * Deleting a kind of work outright. **Work maintenance, PR_WORK_CONFIGURE.**
 *
 * Only reachable for a type the server has just reported as unreferenced; a
 * type anything still points at is refused with the counts, not asked about.
 */
export const deleteWorkTypeConfirmation = (name: string): ConfirmSpec => ({
  title: "Xóa loại công việc này?",
  description: `“${name}” không còn được dùng ở đâu và sẽ bị xóa hẳn. Không hoàn tác được.`,
  confirmLabel: "Xóa loại công việc",
  variant: "destructive",
});

/**
 * A validator refusing to count one result - *Từ chối / Không ghi nhận* - or
 * taking a counted one back. The reason is typed inside the dialog (`details`
 * is the caller's textarea), and the description says the one thing that is
 * easy to assume wrongly: a sync will not quietly bring the result back.
 */
export const rejectResultConfirmation = ({
  quantity,
  counted,
  details,
}: {
  /** "+3 khách hàng", as the row reads. */
  quantity: string;
  /** A counted result is *taken back*; a pending one is *refused*. */
  counted: boolean;
  details: ReactNode;
}): ConfirmSpec => ({
  title: counted ? "Loại bỏ kết quả?" : "Từ chối kết quả?",
  description: `Kết quả: ${quantity}. ${
    counted
      ? "Kết quả sẽ bị rút khỏi thực tế và không được ghi nhận."
      : "Kết quả sẽ không được ghi nhận."
  } Đồng bộ từ Nội dung sẽ không tự ghi nhận lại; chỉ 'Xem xét lại' mới mở lại được.`,
  confirmLabel: counted ? "Loại bỏ" : "Từ chối",
  cancelLabel: "Hủy",
  variant: "destructive",
  details,
});

/**
 * A validator releasing a rejected result back to pending - *Xem xét lại*.
 * Nothing is counted by this; the earlier rejection stays in the history.
 */
export const reconsiderResultConfirmation = (quantity: string): ConfirmSpec => ({
  title: "Xem xét lại kết quả?",
  description: `Kết quả: ${quantity}. Kết quả sẽ trở về trạng thái chờ xác nhận để xác nhận hoặc từ chối lại. Lần từ chối trước vẫn được giữ trong lịch sử.`,
  confirmLabel: "Xem xét lại",
  cancelLabel: "Hủy",
});

/**
 * An administrator taking one result out of the actual. **Work maintenance.**
 *
 * Says the one thing that is easy to get wrong: a content-derived result
 * whose source is still accepted may come back at the next projection.
 */
export const adminRemoveResultConfirmation = (isContent: boolean): ConfirmSpec => ({
  title: "Xóa kết quả công việc?",
  description: isContent
    ? "Kết quả sẽ bị loại khỏi thực tế và ghi lý do. Nếu nội dung gốc vẫn được duyệt, lần đồng bộ sau có thể ghi nhận lại kết quả này."
    : "Kết quả sẽ bị loại khỏi thực tế và ghi lý do. Dòng kết quả vẫn được giữ trong lịch sử.",
  confirmLabel: "Xóa kết quả công việc",
  variant: "destructive",
});

/**
 * An administrator deleting one **legacy** content work item outright.
 *
 * The pre-period-container shape - one work item per content milestone - is
 * the only thing this dialog is ever opened for, and it says the five things
 * that are easy to assume wrongly: the content stays, nothing is recorded in
 * its place, no sync runs, no re-mapping happens, no other work is touched.
 * The way back is named and left to the person: *Đồng bộ dữ liệu công việc*.
 */
export const deleteLegacyWorkItemConfirmation = ({
  code,
  title,
  responsible,
  contentCode,
}: {
  code: string;
  title: string;
  responsible: string | null | undefined;
  contentCode: string | null | undefined;
}): ConfirmSpec => ({
  title: "Xóa công việc?",
  description:
    "Đây là dữ liệu công việc được tạo từ cơ chế Nội dung cũ. Xóa là xóa hẳn dòng công việc này, không hoàn tác được.",
  confirmLabel: "Xác nhận xóa",
  cancelLabel: "Hủy",
  variant: "destructive",
  // Multi-line on purpose - the dialog renders details `whitespace-pre-line`.
  // Each promise is its own line so none of them can be skimmed past.
  details: [
    code,
    title,
    `Phụ trách: ${who(responsible)}`,
    contentCode ? `Nguồn: ${contentCode}` : null,
    "",
    "Việc xóa sẽ:",
    "• xóa công việc cũ này",
    "• giữ nguyên Nội dung nguồn",
    "• không tự tạo kết quả thay thế",
    "• không tự chạy đồng bộ Nội dung",
    "• không tự ánh xạ lại",
    "• không xóa các công việc khác",
    "",
    "Nếu Nội dung nguồn vẫn đủ điều kiện, bạn có thể chủ động chạy “Đồng bộ dữ liệu công việc” sau.",
  ]
    .filter((line): line is string => line !== null)
    .join("\n"),
});

/**
 * Deleting one **terminal** work item - cancelled or rejected - outright.
 * Work maintenance.
 *
 * Not the legacy dialog with a word changed: that one is about a row's
 * provenance and promises the content stays; this one is about a row's
 * lifecycle, and its promises are about scope - this row, its own child data
 * when safe, nothing else, and no way back from the screen. The status is
 * printed, in its own label, so a person confirming sees which terminal
 * state they are deleting - a rejected proposal is never called "đã hủy".
 */
export const deleteTerminalWorkItemConfirmation = ({
  code,
  title,
  responsible,
  statusLabel,
}: {
  code: string;
  title: string;
  responsible: string | null | undefined;
  statusLabel: string;
}): ConfirmSpec => ({
  title: "Xóa công việc?",
  description: `Công việc này đã kết thúc ở trạng thái “${statusLabel}” và không còn hoạt động. Xóa là xóa hẳn dòng công việc này khỏi hệ thống, không hoàn tác được từ giao diện.`,
  confirmLabel: "Xác nhận xóa",
  cancelLabel: "Hủy",
  variant: "destructive",
  details: [
    code,
    title,
    `Phụ trách: ${who(responsible)}`,
    `Trạng thái: ${statusLabel}`,
    "",
    "Việc xóa sẽ:",
    "• xóa công việc này khỏi hệ thống",
    "• xóa dữ liệu con chỉ thuộc riêng công việc này nếu an toàn",
    "• không ảnh hưởng các công việc khác",
    "• không thể khôi phục lại từ giao diện",
  ].join("\n"),
});

/**
 * Offering a retired kind of work again. **M2.5.**
 *
 * Asks for the same reason its opposite does - the panel's rule is that any
 * change of business state asks first, and this one puts a row back into every
 * employee's picker. Ordinary primary action, not dressed as destructive.
 */
export const activateWorkTypeConfirmation = (name: string): ConfirmSpec => ({
  title: "Bật lại loại công việc này?",
  description: `“${name}” sẽ xuất hiện trở lại khi tạo công việc và khi cấu hình hạn mức KPI mới.`,
  confirmLabel: "Bật lại",
});

/**
 * Putting a workload rate in force. **M6.**
 *
 * Asks because an approved rate is immutable and immediately decides what work
 * is worth - the two facts a person needs before they press it, and neither is
 * visible from the form.
 */
export const approveScoringRuleConfirmation = (workType: string, minutes: string): ConfirmSpec => ({
  title: "Duyệt quy tắc workload này?",
  description: `Từ ngày hiệu lực, “${workType}” sẽ được tính ${minutes} phút chuẩn cho mỗi đơn vị. Quy tắc đã duyệt không sửa được nữa — muốn đổi thì tạo bản điều chỉnh mới.`,
  confirmLabel: "Duyệt quy tắc",
});

/** Putting a performance policy in force. Same reasoning, one level up. */
export const approvePerformancePolicyConfirmation = (version: number): ConfirmSpec => ({
  title: `Duyệt chính sách hiệu suất v${version}?`,
  description:
    "Chính sách đã duyệt không sửa được nữa và sẽ quyết định trọng số, thang điểm và ngưỡng chất lượng cho các kỳ áp dụng. Muốn đổi thì tạo phiên bản mới.",
  confirmLabel: "Duyệt chính sách",
});

/**
 * Agreeing one person's month. **The evaluation, not the money.**
 *
 * The description says what becomes fixed and what does not, and deliberately
 * does not mention pay: M6 scores and reports performance, and the head decides
 * any allocation separately, outside MeoChat. A dialog that implied otherwise
 * would turn an evaluation into a promise.
 */
export const finalizePerformanceConfirmation = (name: string, month: string): ConfirmSpec => ({
  title: `Chốt hiệu suất tháng ${month}?`,
  description: `Kết quả đánh giá hiệu suất của “${name}” sẽ được ghi lại thành lịch sử và không tự động tính lại nữa. Đây là kết quả đánh giá — việc phân bổ thưởng do quản lý quyết định riêng, ngoài MeoChat.`,
  confirmLabel: "Chốt hiệu suất",
});

export const ACTION_INVENTORY: readonly ActionInventoryEntry[] = [
  // --- Thành viên & Phân quyền (membership, phase 1)
  {
    action: "Thêm thành viên (Telegram ID + vai trò)",
    policy: "parameter-modal",
    reason:
      "Form thêm thành viên chính là bước xác nhận: nó thu Telegram ID, tên và vai trò, và nút gửi nêu rõ vai trò sẽ gán. Thêm nhầm được sửa bằng đổi vai trò hoặc vô hiệu hóa, không mất dữ liệu.",
  },
  { action: "Đổi vai trò thành viên", policy: "dialog" },
  { action: "Vô hiệu hóa thành viên", policy: "dialog" },
  { action: "Kích hoạt lại thành viên", policy: "dialog" },
  { action: "Loại thành viên khỏi PR", policy: "dialog" },
  {
    action: "Xem quyền hiện tại của thành viên",
    policy: "none",
    reason: "Chỉ đọc: hiện quyền và nguồn gốc quyền do máy chủ tính, không thay đổi gì.",
  },
  // --- Hiệu suất (M6)
  { action: "Duyệt quy tắc workload", policy: "dialog" },
  { action: "Duyệt chính sách hiệu suất", policy: "dialog" },
  { action: "Chốt hiệu suất tháng của một nhân sự", policy: "dialog" },
  {
    action: "Tạo quy tắc workload (nháp)",
    policy: "parameter-modal",
    reason:
      "Form chọn loại công việc, số phút chuẩn và ngày hiệu lực; nút tạo trong form là bước xác nhận. Bản nháp chưa quyết định gì — bước duyệt mới là lúc hỏi.",
  },
  {
    action: "Tạo chính sách hiệu suất (nháp)",
    policy: "parameter-modal",
    reason:
      "Form nhập trọng số và trần; nút tạo trong form là bước xác nhận. Bản nháp chưa áp dụng cho kỳ nào.",
  },
  {
    action: "Lưu đánh giá hiệu suất tháng",
    policy: "parameter-modal",
    reason:
      "Form ba mức đánh giá kèm nhận xét; nút lưu trong form là bước xác nhận. Sửa lại được khi kỳ còn mở, và mọi thay đổi đều ghi nhật ký.",
  },
  {
    action: "Đặt mục tiêu workload thủ công",
    policy: "parameter-modal",
    reason:
      "Form nhập số phút và lý do bắt buộc; nút lưu trong form là bước xác nhận. Lý do chính là bản ghi giải trình.",
  },
  // --- Ánh xạ nội dung → công việc (M3 / M3.1)
  {
    action: "Đặt ánh xạ loại nội dung → loại công việc",
    policy: "parameter-modal",
    reason:
      "Form chọn cột mốc, loại nội dung và loại công việc; nút lưu trong form là bước xác nhận. Chỉ ảnh hưởng công việc được chiếu về sau — công việc đã tính trong kỳ đã đóng không bị viết lại.",
  },
  // --- Loại công việc (taxonomy, M2.5)
  { action: "Tắt loại công việc", policy: "dialog" },
  { action: "Bật lại loại công việc", policy: "dialog" },
  {
    action: "Tạo loại công việc",
    policy: "parameter-modal",
    reason:
      "Form nhập mã, tên, nhóm, cách tính và đơn vị; nút tạo trong form là bước xác nhận. Tạo thêm một lựa chọn không bỏ đi cái gì.",
  },
  {
    action: "Sửa loại công việc",
    policy: "parameter-modal",
    reason:
      "Form sửa siêu dữ liệu; nút lưu trong form là bước xác nhận. Các trường cấu trúc bị khóa sau khi đã phát sinh dữ liệu nên không sửa được ở đây.",
  },
  {
    action: "Khởi tạo bộ loại công việc mặc định",
    policy: "parameter-modal",
    reason:
      "Chạy lại không tạo trùng và không bật lại loại đã tắt, nên thao tác không phá hủy gì; nút trong form là bước xác nhận.",
  },
  // --- Hạn mức KPI (quota engine, M2)
  { action: "Duyệt kế hoạch KPI", policy: "dialog" },
  { action: "Duyệt bản điều chỉnh kế hoạch KPI", policy: "dialog" },
  { action: "Tạo bản điều chỉnh kế hoạch KPI", policy: "dialog" },
  { action: "Bỏ hạn mức khỏi bản nháp", policy: "dialog" },
  { action: "Bỏ bản nháp kế hoạch KPI", policy: "dialog" },
  { action: "Gửi kế hoạch KPI của tôi để duyệt", policy: "dialog" },
  { action: "Trả lại kế hoạch KPI để chỉnh sửa", policy: "dialog" },
  {
    action: "Tạo KPI của tôi / Đề xuất điều chỉnh",
    policy: "none",
    reason:
      "Tạo một bản nháp trống hoặc sao chép từ bản đang áp dụng; chưa quyết định gì và chưa ai thấy ngoài chính người tạo. Bước có hậu quả là Gửi duyệt, và bước đó có hộp thoại.",
  },
  { action: "Tính lại điều kiện KPI của kỳ", policy: "dialog" },
  {
    action: "Tạo bản nháp kế hoạch KPI",
    policy: "parameter-modal",
    reason: "Form chọn nhân sự và kỳ báo cáo; nút tạo trong form là bước xác nhận.",
  },
  {
    action: "Thêm hạn mức cho một loại việc",
    policy: "parameter-modal",
    reason:
      "Form chọn loại việc, mục tiêu và trần hạn mức; nút thêm trong form là bước xác nhận. Bản nháp chưa quyết định gì cả — bước duyệt mới là lúc hỏi.",
  },
  {
    action: "Sửa mục tiêu / trần hạn mức trong bản nháp",
    policy: "parameter-modal",
    reason:
      "Form nhập hai con số trên một bản nháp chưa có hiệu lực; nút lưu trong form là bước xác nhận.",
  },
  {
    action: "Mở kỳ báo cáo mới",
    policy: "parameter-modal",
    reason:
      "Form chọn tháng; thao tác idempotent, mở lại tháng đã có trả về đúng kỳ đó và không đổi trạng thái.",
  },
  // --- Công việc (Work Ledger, M1)
  { action: "Chấp nhận đề xuất công việc", policy: "dialog" },
  { action: "Từ chối đề xuất công việc", policy: "dialog" },
  { action: "Báo hoàn thành công việc", policy: "dialog" },
  { action: "Xác nhận công việc đã hoàn thành", policy: "dialog" },
  { action: "Trả lại công việc để làm tiếp", policy: "dialog" },
  { action: "Hủy công việc", policy: "dialog" },
  {
    action: "Xóa công việc cũ từ Nội dung (bảo trì, ADMIN/OWNER)",
    policy: "dialog",
  },
  {
    action: "Đồng bộ lại từ Nội dung (một nội dung, ADMIN/OWNER)",
    policy: "none",
    reason:
      "Chạy lại đúng bộ chiếu Nội dung → Công việc cho một nội dung: hội tụ, lặp lại bao nhiêu lần cũng ra một kết quả, không xóa gì và không tự ghi nhận gì mà nguồn không nói. Kết quả được báo bằng câu tiếng Việt ngay dưới nút; một hộp thoại chỉ hỏi lại điều nút đã nói.",
  },
  { action: "Thêm / gỡ người thực hiện", policy: "dialog" },
  { action: "Đổi hạn công việc", policy: "dialog" },
  { action: "Bắt đầu làm công việc của mình", policy: "dialog" },
  {
    action: "Đề xuất công việc",
    policy: "parameter-modal",
    reason: "Form nhập loại việc, tiêu đề và hạn; nút gửi trong form là bước xác nhận.",
  },
  {
    action: "Giao công việc",
    policy: "parameter-modal",
    reason: "Form nhập loại việc, người thực hiện và hạn; nút gửi trong form là bước xác nhận.",
  },
  {
    action: "Thêm minh chứng",
    policy: "parameter-modal",
    reason: "Form nhập nhãn và liên kết; nút gửi trong form là bước xác nhận.",
  },
  // --- Content and workflow
  { action: "Duyệt / Duyệt nội bộ (một nội dung)", policy: "dialog" },
  { action: "Yêu cầu chỉnh sửa", policy: "dialog" },
  { action: "Từ chối", policy: "dialog" },
  { action: "Duyệt hàng loạt", policy: "dialog" },
  { action: "Lưu trữ nội dung kỳ trước", policy: "dialog" },
  { action: "Chuyển bước quy trình", policy: "dialog" },
  { action: "Hủy nội dung", policy: "dialog" },
  { action: "Hoàn tác thao tác gần nhất", policy: "dialog" },
  { action: "Xóa vĩnh viễn nội dung", policy: "dialog" },
  {
    action: "Sửa nội dung (mở trình soạn thảo)",
    policy: "parameter-modal",
    reason:
      "Mở trình soạn thảo chưa ghi gì; nút Lưu bản mới trong trình soạn thảo là bước xác nhận.",
  },
  { action: "Đổi mức ưu tiên", policy: "dialog" },
  { action: "Đổi loại nội dung", policy: "dialog" },
  {
    action: "Đổi hình thức đăng (Organic / Quảng cáo trả phí)",
    policy: "dialog",
  },
  // --- Production
  { action: "Nhận sản xuất", policy: "dialog" },
  { action: "Bắt đầu sản xuất", policy: "dialog" },
  { action: "Phân công / chuyển người sản xuất", policy: "dialog" },
  { action: "Bỏ phân công người sản xuất", policy: "dialog" },
  {
    action: "Gửi duyệt nội bộ (nộp file sản xuất)",
    policy: "parameter-modal",
    reason:
      "Form nhập link file sản xuất; nút gửi trong form là bước xác nhận và nói rõ hành động.",
  },
  // --- Publication
  {
    action: "Ghi nhận đã đăng",
    policy: "parameter-modal",
    reason: "Form nhập kênh, link và thời điểm đăng; nút gửi trong form là bước xác nhận.",
  },
  { action: "Thu hồi bản đăng", policy: "dialog" },
  // --- Permissions
  {
    action: "Cấp quyền duyệt",
    policy: "parameter-modal",
    reason:
      "Form chọn người, quyền, phạm vi và thời hạn; nút Cấp quyền trong form là bước xác nhận và nêu rõ người được cấp.",
  },
  { action: "Thu hồi quyền duyệt", policy: "dialog" },
  // --- Channels and integrations
  { action: "Ngắt kết nối kênh", policy: "dialog" },
  { action: "Thay/kết nối lại tài khoản đã kết nối", policy: "dialog" },
  { action: "Kết thúc phân công kênh", policy: "dialog" },
  {
    action: "Kết nối kênh (mở màn hình cấp quyền của nhà cung cấp)",
    policy: "none",
    reason:
      "Chỉ mở màn hình đồng ý của TikTok/Google/Meta. Chính màn hình đó là bước xác nhận; hỏi thêm một lần nữa là thừa.",
  },
  {
    action: "Chọn tài khoản sau khi cấp quyền",
    policy: "parameter-modal",
    reason: "Danh sách tài khoản do nhà cung cấp trả về; chọn xong bấm nút là xác nhận.",
  },
  {
    action: "Đồng bộ số liệu kênh",
    policy: "none",
    reason:
      "Chỉ đọc lại số liệu từ nền tảng, không đổi trạng thái nghiệp vụ, không đổi quyền và không đổi người phụ trách.",
  },
  {
    action: "Tạo / cập nhật kênh, nền tảng, thương hiệu",
    policy: "parameter-modal",
    reason: "Form nhập dữ liệu; nút gửi trong form là bước xác nhận.",
  },
  // --- Công việc định kỳ (M4B)
  { action: "Bật việc định kỳ", policy: "dialog" },
  { action: "Kết thúc việc định kỳ", policy: "dialog" },
  { action: "Xoá việc định kỳ nháp", policy: "dialog" },
  {
    action: "Tạm dừng / chạy lại việc định kỳ",
    policy: "none",
    reason:
      "Cả hai đều đảo lại được bằng một lần bấm, không đổi công việc đã sinh ra, và tạm dừng là việc người quản lý làm gấp. Khoảng thời gian tạm dừng không bao giờ được sinh bù, nên bấm nhầm không tạo ra việc ngoài ý muốn.",
  },
  {
    action: "Tạo hoặc sửa việc định kỳ",
    policy: "parameter-modal",
    reason:
      "Form nhập lịch chạy và người thực hiện; nút lưu trong form là bước xác nhận. Mẫu mới nằm ở trạng thái nháp và chưa sinh việc — bước “Bật chạy” mới là lúc hỏi.",
  },
  // --- Tasks
  { action: "Đổi trạng thái task", policy: "dialog" },
  { action: "Giao task", policy: "dialog" },
  {
    action: "Đăng xuất",
    policy: "none",
    reason:
      "Chỉ kết thúc phiên của chính người đang đăng nhập; không đổi dữ liệu nghiệp vụ và đăng nhập lại được ngay.",
  },
  {
    action: "Tạo task / tạo nội dung",
    policy: "parameter-modal",
    reason: "Form nhập dữ liệu; nút tạo trong form là bước xác nhận.",
  },
  // --- Contributions
  {
    action: "Xóa tài nguyên / sản phẩm phái sinh / nơi đăng / bình luận",
    policy: "dialog",
  },
  {
    action: "Thêm hoặc sửa tài nguyên, sản phẩm phái sinh, nơi đăng, bình luận",
    policy: "parameter-modal",
    reason: "Form nhập dữ liệu; nút lưu trong form là bước xác nhận.",
  },
  // --- Read-only, deliberately unconfirmed
  {
    action: "Lọc, tìm kiếm, sắp xếp, đổi mốc ngày, đổi tab, phân trang",
    policy: "none",
    reason: "Chỉ đổi những gì đang hiển thị, không ghi gì lên hệ thống.",
  },
  {
    action: "Mở chi tiết nội dung, mở form, mở dropdown",
    policy: "none",
    reason:
      "Chưa gửi yêu cầu nào; xác nhận ở đây sẽ làm việc điều hướng bình thường trở nên nặng nề.",
  },
  {
    action: "Đánh dấu thông báo đã đọc",
    policy: "none",
    reason:
      "Chỉ đổi trạng thái đã đọc của chính người đang đăng nhập, không đổi dữ liệu nghiệp vụ và bấm nhầm không gây hậu quả.",
  },
];
