import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { get } from "@shared/api";
import { Shell } from "@shared/Shell";
import { mk } from "./mk";
import { CampaignDetail, Campaigns } from "./pages/Campaigns";
import { Leads, Library, Review, Social, Studio } from "./pages/Channels";
import Autopilot from "./pages/Autopilot";
import Compose from "./pages/Compose";
import { AbTests, Analytics, Audience, Calendar } from "./pages/Insights";
import Overview from "./pages/Overview";

async function verify(): Promise<string | null> {
  const r = await get("/api/v1/marketing/campaigns");
  return r.status === 403 ? "Signed in, but this account has no access to marketing." : null;
}

/** Rendered inside the shell, so it only asks once someone is signed in. */
function HeldCounter({ onCount }: { onCount: (n: number) => void }) {
  const location = useLocation();
  useEffect(() => { mk.drafts(undefined, "held_for_review").then((d) => onCount(d.length)).catch(() => {}); }, [location.pathname, onCount]);
  return null;
}

export default function App() {
  const [held, setHeld] = useState(0);
  return (
    <Shell app="marketing" title="Marketing" defaultEmail="marketing.manager@voyqi.example" verify={verify} nav={() => [
      { title: "Marketing", links: [{ to: "/", label: "Command center" }, { to: "/campaigns", label: "Campaigns" }, { to: "/review", label: "Content review", badge: held }, { to: "/calendar", label: "Calendar" }] },
      { title: "Channels", links: [{ to: "/compose/email", label: "✉ Email" }, { to: "/compose/sms", label: "💬 SMS" }, { to: "/compose/push", label: "🔔 Push" }, { to: "/compose/voice", label: "📞 Voice" }, { to: "/social", label: "◎ Social" }, { to: "/autopilot", label: "◎ Social autopilot" }] },
      { title: "Create", links: [{ to: "/studio", label: "✦ AI studio" }, { to: "/library", label: "Library" }] },
      { title: "Audience & results", links: [{ to: "/audience", label: "Audience & suppression" }, { to: "/ab-tests", label: "A/B tests" }, { to: "/analytics", label: "Analytics" }, { to: "/leads", label: "Leads" }] },
    ]}>
      <HeldCounter onCount={setHeld} />
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/:id" element={<CampaignDetail />} />
        <Route path="/review" element={<Review />} />
        <Route path="/calendar" element={<Calendar />} />
        <Route path="/compose/:channel" element={<Compose />} />
        <Route path="/social" element={<Social />} />
        <Route path="/autopilot" element={<Autopilot />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/library" element={<Library />} />
        <Route path="/audience" element={<Audience />} />
        <Route path="/ab-tests" element={<AbTests />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/leads" element={<Leads />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Shell>
  );
}
