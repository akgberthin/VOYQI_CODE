/* Forwards every /api/* request to the API gateway, so the browser only
   ever talks to this app's own origin and the HttpOnly session cookie the
   gateway sets stays same-origin. Read at request time, so one image works
   in every environment: set VOYQI_GATEWAY_URL. */

const GATEWAY = process.env.VOYQI_GATEWAY_URL || "http://localhost:8000";

export const dynamic = "force-dynamic";

// Hop-by-hop headers, and ones fetch() handles itself.
const DROP_REQUEST = ["host", "connection", "content-length", "accept-encoding"];
const DROP_RESPONSE = ["content-encoding", "content-length", "transfer-encoding", "connection", "set-cookie"];

async function proxy(req: Request): Promise<Response> {
  const incoming = new URL(req.url);
  const target = new URL(incoming.pathname + incoming.search, GATEWAY);

  const headers = new Headers(req.headers);
  for (const h of DROP_REQUEST) headers.delete(h);
  const forwardedFor = req.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);

  let res: Response;
  try {
    res = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
    });
  } catch (err) {
    return Response.json({ detail: `API gateway unreachable at ${GATEWAY} (${(err as Error).message})` }, { status: 502 });
  }

  const out = new Headers(res.headers);
  for (const h of DROP_RESPONSE) out.delete(h);
  // Login, refresh and logout set several cookies; pass each one through.
  for (const cookie of res.headers.getSetCookie()) out.append("set-cookie", cookie);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as HEAD, proxy as OPTIONS };
