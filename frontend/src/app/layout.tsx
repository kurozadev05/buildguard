import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Providers } from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "BUILDGUARD", template: "%s · BUILDGUARD" },
  description: "Construction material testing, traceability and durability records.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icons/icon-32.png", apple: "/icons/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "BUILDGUARD", statusBarStyle: "black-translucent" },
};
export const viewport: Viewport = { themeColor: "#111827", width: "device-width", initialScale: 1, viewportFit: "cover" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  await headers(); // renders every page per request so Next can stamp the CSP nonce onto its scripts
  return (
    <html lang="en">
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
