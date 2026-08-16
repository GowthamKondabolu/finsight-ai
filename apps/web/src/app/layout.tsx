import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "FinSight AI | Analyst Workspace",
  description: "Evidence-first financial-risk investigations grounded in public SEC filings.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
