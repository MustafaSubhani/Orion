"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Session } from "@/lib/types";
import { logout } from "@/lib/api";

type Props = {
  session: Session;
  page: "chat" | "lab";
  note: string;
};

export function Chrome({ session, page, note }: Props) {
  const router = useRouter();

  async function onSignOut() {
    await logout();
    router.replace("/");
  }

  return (
    <header className="top">
      <div>
        <div className="brand">Orion</div>
        <div className="muted">{note}</div>
      </div>
      <nav className="nav">
        <Link href="/app" className={page === "chat" ? "active" : undefined}>
          Chat
        </Link>
        {session.role === "super_user" ? (
          <Link href="/app/lab" className={page === "lab" ? "active" : undefined}>
            Lab
          </Link>
        ) : null}
        <span className="muted">{session.username}</span>
        <button type="button" className="btn-ghost" onClick={onSignOut}>
          Sign out
        </button>
      </nav>
    </header>
  );
}
