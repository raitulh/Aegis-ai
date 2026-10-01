import { redirect } from "next/navigation";

/** API keys moved into “API & SDK”; keep old links working. */
export default function ApiKeysRedirect() {
  redirect("/dashboard/developers?tab=keys");
}
