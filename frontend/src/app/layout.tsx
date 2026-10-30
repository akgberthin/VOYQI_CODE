import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: { template: "%s · VOYQI", default: "VOYQI Console" },
  description: "VOYQI super admin and marketing consoles.",
  icons: { icon: "data:," },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Apply the saved theme before first paint. */}
        <script dangerouslySetInnerHTML={{ __html: `try{var t=localStorage.getItem("voyqi.theme");if(t==="light"||t==="system"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}` }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
