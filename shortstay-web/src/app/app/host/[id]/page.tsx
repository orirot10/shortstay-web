"use client";

import { Header } from "../../../components/Header";
import { useAuth } from "../../../components/AuthProvider";
import { apiFetch } from "../../../lib/api";
import { useEffect, useState } from "react";
import Image from "next/image";

type Host = {
  id: string;
  name: string | null;
  avatarUrl: string | null;
  hostStats: { hostScore: number; avgRating: number; recsCount: number };
};

type Rec = {
  id: string;
  authorId: string;
  ratings: { overall: number; trust: number; accuracy: number; experience: number };
  text: string | null;
  createdAt: string;
};

export default function HostPage({ params }: { params: { id: string } }) {
  const { user, loginGoogle } = useAuth();
  const [host, setHost] = useState<Host | null>(null);
  const [recs, setRecs] = useState<Rec[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const [overall, setOverall] = useState(5);
  const [trust, setTrust] = useState(5);
  const [accuracy, setAccuracy] = useState(5);
  const [experience, setExperience] = useState(5);
  const [text, setText] = useState("");

  const load = async () => {
    const h = await apiFetch<{ host: Host }>(`/hosts/${params.id}`);
    const r = await apiFetch<{ items: Rec[] }>(`/hosts/${params.id}/recommendations`);
    setHost(h.host);
    setRecs(r.items);
  };

  useEffect(() => {
    load().catch((e) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.id]);

  const submitRec = async () => {
    setErr(null);
    if (!user) {
      await loginGoogle();
      return;
    }
    await apiFetch(
      `/hosts/${params.id}/recommendations`,
      {
        method: "POST",
        body: JSON.stringify({
          ratings: { overall, trust, accuracy, experience },
          text: text || undefined,
        }),
      },
      user
    );
    setText("");
    await load();
  };

  return (
    <div>
      <Header />
      <div style={{ padding: 16, maxWidth: 900, margin: "0 auto" }}>
        {err && <p style={{ color: "crimson" }}>{err}</p>}
        {!host ? (
          <p>טוען...</p>
        ) : (
          <>
            <h2>פרופיל מארח</h2>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              {host.avatarUrl && <Image src={host.avatarUrl} alt="" width={48} height={48} style={{ borderRadius: 999 }} unoptimized />}
              <div>
                <div style={{ fontWeight: 700 }}>{host.name || host.id}</div>
                <div>
                  HostScore: <b>{host.hostStats.hostScore.toFixed(2)}</b> · ממוצע:{" "}
                  <b>{host.hostStats.avgRating.toFixed(2)}</b> · המלצות: <b>{host.hostStats.recsCount}</b>
                </div>
              </div>
            </div>

            <h3 style={{ marginTop: 18 }}>המלצות</h3>
            <div style={{ display: "grid", gap: 10 }}>
              {recs.map((r) => (
                <div key={r.id} style={{ border: "1px solid #ddd", borderRadius: 10, padding: 12 }}>
                  <div>
                    ⭐ {r.ratings.overall} · Trust {r.ratings.trust} · Accuracy {r.ratings.accuracy} · Exp {r.ratings.experience}
                  </div>
                  {r.text && <div style={{ marginTop: 6 }}>{r.text}</div>}
                  <div style={{ marginTop: 6, color: "#666", fontSize: 12 }}>
                    by {r.authorId} · {new Date(r.createdAt).toLocaleString("he-IL")}
                  </div>
                </div>
              ))}
              {recs.length === 0 && <p>אין עדיין המלצות.</p>}
            </div>

            <h3 style={{ marginTop: 18 }}>כתוב המלצה</h3>
            <div style={{ display: "grid", gap: 8, maxWidth: 520 }}>
              <label>Overall <input type="number" min={1} max={5} value={overall} onChange={(e) => setOverall(Number(e.target.value))} /></label>
              <label>Trust <input type="number" min={1} max={5} value={trust} onChange={(e) => setTrust(Number(e.target.value))} /></label>
              <label>Accuracy <input type="number" min={1} max={5} value={accuracy} onChange={(e) => setAccuracy(Number(e.target.value))} /></label>
              <label>Experience <input type="number" min={1} max={5} value={experience} onChange={(e) => setExperience(Number(e.target.value))} /></label>
              <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="טקסט (אופציונלי)" />
              <button onClick={() => submitRec().catch((e) => setErr(e.message))}>שלח המלצה</button>
              {!user && <p style={{ color: "#666" }}>כדי להמליץ צריך להתחבר.</p>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
