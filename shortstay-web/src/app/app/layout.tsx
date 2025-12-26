import type { Metadata } from "next";
import "../globals.css";
import { AuthProvider } from "../components/AuthProvider";

export const metadata: Metadata = { title: "ShortStay" };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
} 
