"use client";

import { Header } from "../../components/Header";
import { useAuth } from "../../components/AuthProvider";
import { apiFetch } from "../../lib/api";
import { useState } from "react";

export default function NewListingPage() {
  const { user, loginGoogle } = useAuth();
  const [title, setTitle] = useState("");
  const [area, setArea] = useState("");
  const [pricePerNight, setPricePerNight] = useState(250);
  const [description, setDescription] = useState("");
  const [availabilityText, setAvailabilityText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async () => {
    setMsg(null);
    if (!user) {
      await loginGoogle();
      return;
    }
    const out = await apiFetch<{ id: string }>(
      "/listings",
      {
        method: "POST",
        body: JSON.stringify({
          title,
          area,
          pricePerNight,
          description,
          availabilityText: availabilityText || undefined,
          images: [],
        }),
      },
      user
    );
    setMsg(`נוצרה מודעה! id=${out.id}`);
    setTitle(""); setArea(""); setDescription(""); setAvailabilityText(""); setPricePerNight(250);
  };

  return (
    <div>
      <Header />
      <div style={{ padding: 16, maxWidth: 700, margin: "0 auto" }}>
        <h2>פרסום מודעה</h2>

        {!user && <p>כדי לפרסם מודעה צריך להתחבר.</p>}

        <div style={{ display: "grid", gap: 8 }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="כותרת" />
          <input value={area} onChange={(e) => setArea(e.target.value)} placeholder="אזור" />
          <input
            type="number"
            value={pricePerNight}
            onChange={(e) => setPricePerNight(Number(e.target.value))}
            placeholder="מחיר ללילה"
          />
          <textarea value={availabilityText} onChange={(e) => setAvailabilityText(e.target.value)} placeholder="זמינות (טקסט חופשי)" />
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="תיאור" rows={6} />

          <button onClick={() => submit().catch((e) => setMsg(e.message))}>פרסם</button>
          {msg && <p>{msg}</p>}
        </div>
      </div>
    </div>
  );
}
