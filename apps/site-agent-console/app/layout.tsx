import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "NXTektal Site Agent Console",
  description:
    "Local manager console for the fixture-backed NXTektal Pilot Site " +
    "Agent service. Simulated pilot scenario — not live customer data.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
