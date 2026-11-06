import type { Metadata } from "next";
import { Big_Shoulders_Stencil, Space_Mono } from "next/font/google";
import "./globals.css";

const stencil = Big_Shoulders_Stencil({ variable: "--font-stencil", subsets: ["latin"], weight: ["700", "900"] });
const mono = Space_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "700"] });

export const metadata: Metadata = {
  title: "VIVA LA TRACE",
  description: "The labs keep your traces. Take them back.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${stencil.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
