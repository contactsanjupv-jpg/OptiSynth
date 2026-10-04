import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "OptiSynth — Forced-Substitution Qualification Diagnostic",
  description: "Structure your qualification evidence, estimate candidate substitutes, and plan targeted physical validation.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
