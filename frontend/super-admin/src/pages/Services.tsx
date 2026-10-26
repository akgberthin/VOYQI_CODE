import { Link } from "react-router-dom";
import { get, must } from "../api";
import { Badge, ErrorNote, Loading, PageHead, useLoad } from "../components/ui";

export type ServiceInfo = { name: string; base_url: string; prefixes: string[]; status: "up" | "down" | "timeout"; healthy: boolean; latency_ms: number; error: string | null };

export const loadServices = () => must<ServiceInfo[]>(get("/api/v1/platform/services"));

export default function Services() {
  const { data, error, loading, reload } = useLoad(loadServices);
  const up = (data || []).filter((s) => s.healthy).length;
  return (
    <>
      <PageHead title="Services & APIs" text="Every backend service behind the gateway, live. Open one to see and call every API it exposes — forms are generated from the service's own API description, so nothing here goes stale.">
        {data && <Badge kind={up === data.length ? "ok" : "warn"}>{up}/{data.length} healthy</Badge>}
        <button className="ghost" onClick={reload}>Refresh</button>
      </PageHead>
      <ErrorNote error={error} />
      {loading && !data ? <Loading what="Checking every service" /> : (
        <div className="grid cols-3">
          {(data || []).sort((a, b) => a.name.localeCompare(b.name)).map((s) => (
            <Link key={s.name} to={`/services/${s.name}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
              <div className="row between">
                <h3 className="row"><span className={`dot ${s.status === "up" ? "ok" : s.status === "timeout" ? "warn" : "bad"}`} /> {s.name}</h3>
                <span className="muted mono">{s.status === "up" ? `${s.latency_ms} ms` : s.status === "timeout" ? "slow to answer" : s.error}</span>
              </div>
              <div className="sub mt mono">{s.prefixes.join("  ")}</div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
