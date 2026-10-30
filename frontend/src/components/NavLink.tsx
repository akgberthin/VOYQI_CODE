"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { CSSProperties, ReactNode } from "react";

/** A link that knows whether it points at the current page. `exact` links
    (a console's home) are active only on that path, others also on the
    pages below them. */
export function NavLink({ href, exact, className = "", activeClassName = "active", inactiveClassName = "", style, children }: {
  href: string; exact?: boolean; className?: string; activeClassName?: string; inactiveClassName?: string; style?: CSSProperties; children: ReactNode;
}) {
  const path = usePathname() || "";
  const active = exact ? path === href : path === href || path.startsWith(`${href}/`);
  return <Link href={href} className={`${className} ${active ? activeClassName : inactiveClassName}`.trim()} style={style}>{children}</Link>;
}
