import { redirect } from "next/navigation";

/** "Mã task design" became "Danh mục điểm" (0074): every step's codes. */
export default function DesignCodesPage() {
  redirect("/tasks/task-codes?phase=THIET_KE");
}
