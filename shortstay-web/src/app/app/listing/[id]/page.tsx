"use client";

import { Header } from "../../../components/Header";
import { apiFetch } from "../../../lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

type Listing = {
  id: string;
  ownerId: string;
  title: string;
  area: string;
  pricePerNight: number;
  description: string;
  availabilityText?: string;
};

export default function ListingPage({ params }: { params: { id: string } }) {
  const [item, setItem] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const d: { item: Listing } = await apiFetch<{ item: Listing }>(`/listings/${params.id}`);
        setItem(d.item);
      } catch (err) {
        setErr(String((err as Error)?.message || err));
      }
    };
    load();
  }, [params.id]);

  return (
    <div>
      <Header />
      <div style={{ padding: 16, maxWidth: 900, margin: "0 auto" }}>
        {err && <p style={{ color: "crimson" }}>{err}</p>}
        {!item ? (
          <p>טוען...</p>
        ) : (
          <>
            <h2>{item.title}</h2>
            <p>
              <b>{item.area}</b> · {item.pricePerNight} ₪/לילה
            </p>
            {item.availabilityText && <p>זמינות: {item.availabilityText}</p>}
            <p style={{ whiteSpace: "pre-wrap" }}>{item.description}</p>

            <div style={{ marginTop: 12 }}>
              <Link href={`/host/${item.ownerId}`}>לפרופיל המארח + המלצות</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
