import Link from "next/link";

export default function NotFound() {
  return <div className="empty">This page doesn&apos;t exist. <Link href="/">Back to the console</Link></div>;
}
