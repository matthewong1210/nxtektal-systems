import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const metadataBase = new URL(
  process.env.NEXT_PUBLIC_REPLAY_SITE_URL ?? "http://localhost:3000",
);

export const metadata: Metadata = {
  metadataBase,
  title: "NXTektal Replay Story",
  description:
    "A read-only investor storytelling layer for simulation-derived operational replay artifacts.",
  openGraph: {
    title: "NXTektal Operational Replay",
    description: "State → Risk → Recommendation → Simulated task → Outcome",
    type: "website",
    images: [{ url: "/og.png", width: 1659, height: 948 }],
  },
  twitter: {
    card: "summary_large_image",
    title: "NXTektal Operational Replay",
    description: "A read-only story layer over simulation-derived replay evidence.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
