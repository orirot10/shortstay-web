"use client";

import Link from "next/link";
import { useAuth } from "./AuthProvider";

export function Header() {
  const { user, loading, loginGoogle, logout } = useAuth();

  return (
    <div style={{ padding: 12, borderBottom: "1px solid #ddd", display: "flex", gap: 12, alignItems: "center" }}>
      <Link href="/" style={{ fontWeight: 700 }}>ShortStay</Link>
      <div style={{ display: "flex", gap: 10 }}>
        <Link href="/">חיפוש</Link>
        <Link href="/new">פרסם מודעה</Link>
      </div>
      <div style={{ marginLeft: "auto" }}>
        {loading ? (
          <span>טוען...</span>
        ) : user ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span>{user.displayName || user.email}</span>
            <button onClick={logout}>התנתק</button>
          </div>
        ) : (
          <button onClick={loginGoogle}>התחבר עם Google</button>
        )}
      </div>
    </div>
  );
}
