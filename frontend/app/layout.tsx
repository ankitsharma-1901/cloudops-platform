import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Create server | CloudOps",
  description:
    "Configure and queue a server request in your local CloudOps workspace.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
