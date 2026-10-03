import { redirect } from "next/navigation";

/** Nothing lives at the root. The panel is /pr. */
export default function Home() {
  redirect("/pr");
}
