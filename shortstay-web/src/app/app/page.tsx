"use client";

import { Header } from "../components/Header";
import { useAuth } from "../components/AuthProvider";
import { apiFetch } from "../lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

type Listing = {
  id: string;
  ownerId: string;
  title: string;
  area: string;
  pricePerNight: number;
  description: string;
};

export default function HomePage() {
  const { user } = useAuth();
  const [area, setArea] = useState("");
  const [items, setItems] = useState<Listing[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setErr(null);
    const q = area ? `?area=${encodeURIComponent(area)}` : "";
    const data = await apiFetch<{ items: Listing[] }>(`/listings${q}`);
    setItems(data.items);
  };

  useEffect(() => {
    load().catch((e) => setErr(String(e.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <Header />
      <div style={{ padding: 16, maxWidth: 900, margin: "0 auto" }}>
        <h2>חיפוש השכרות</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <input value={area} onChange={(e) => setArea(e.target.value)} placeholder="אזור (למשל תל אביב)" />
          <button onClick={() => load().catch((e) => setErr(e.message))}>חפש</button>
        </div>

        {err && <p style={{ color: "crimson" }}>{err}</p>}

        <div style={{ marginTop: 16, display: "grid", gap: 12 }}>
          {items.map((x) => (
            <div key={x.id} style={{ border: "1px solid #ddd", borderRadius: 10, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontWeight: 700 }}>{x.title}</div>
                  <div>{x.area}</div>
                </div>
                <div style={{ fontWeight: 700 }}>{x.pricePerNight} ₪/לילה</div>
              </div>

              <p style={{ marginTop: 8, color: "#444" }}>
                {x.description.length > 140 ? x.description.slice(0, 140) + "…" : x.description}
              </p>

              <div style={{ display: "flex", gap: 10 }}>
                <Link href={`/listing/${x.id}`}>פרטי מודעה</Link>
                <Link href={`/host/${x.ownerId}`}>פרופיל מארח</Link>
              </div>
            </div>
          ))}
        </div>

        {!user && <p style={{ marginTop: 16, color: "#666" }}>כדי לפרסם מודעה או להמליץ, צריך להתחבר עם Google.</p>}
      </div>
    </div>
  );
}
