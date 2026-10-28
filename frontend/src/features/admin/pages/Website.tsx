"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { api, del, get, must, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, useAction, useLoad } from "@/lib/ui";

/* SAP-006: pages, the section-by-section website builder, themes, media,
   AI assist and platform settings — all stored in rbac-svc. */

const R = "/api/v1/rbac";
const useActor = () => useSession().me?.email || "super.admin";

// ── Page management ───────────────────────────────────────────────────
const TEMPLATES: [string, string][] = [["default", "Default"], ["landing", "Landing"], ["full_width", "Full width"]];
const EMPTY_PAGE = { page_key: "", title: "", slug: "", template: "default", seo_title: "", seo_description: "", body: "" };

export function Pages() {
  const run = useAction();
  const actor = useActor();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`${R}/page-content`)));
  const [f, setF] = useState<typeof EMPTY_PAGE | null>(null);
  const editing = !!f && (data || []).some((p) => p.page_key === f.page_key);
  const save = () => run("Page saved", () => must(post(`${R}/page-content`, { ...f, updated_by: actor }))).then((ok) => { if (ok) { setF(null); reload(); } });
  const act = (p: any, what: "publish" | "unpublish") => run(what === "publish" ? "Published" : "Unpublished", () => must(post(`${R}/page-content/${p.id}/${what}?actor_id=${encodeURIComponent(actor)}`))).then(reload);
  const remove = (p: any) => confirm(`Delete “${p.title}” and all its sections?`) && run("Page deleted", () => must(del(`${R}/page-content/${p.id}?actor_id=${encodeURIComponent(actor)}`))).then(reload);
  return (
    <>
      <PageHead title="Pages" text="Page metadata — title, slug, SEO and template. A page's section-by-section content is built in the Website builder. Publishing is autonomous; it carries none of the access-control risk RBAC and ABAC govern.">
        <button onClick={() => setF(f ? null : { ...EMPTY_PAGE })}>{f ? "Cancel" : "New page"}</button>
      </PageHead>
      <ErrorNote error={error} />
      {f && (
        <div className="card mb" style={{ borderColor: "var(--gold)" }}>
          <h3>{editing ? `Edit “${f.title}”` : "New page"}</h3>
          <div className="form mt">
            <label className="field">Page key<input className="mono" disabled={editing} placeholder="home" value={f.page_key} onChange={(e) => setF({ ...f, page_key: e.target.value })} /></label>
            <label className="field">Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value, slug: f.slug || slugify(e.target.value) })} /></label>
            <label className="field">Slug<input className="mono" placeholder="/membership" value={f.slug} onChange={(e) => setF({ ...f, slug: e.target.value })} /></label>
            <label className="field">Template<select value={f.template} onChange={(e) => setF({ ...f, template: e.target.value })}>{TEMPLATES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="field">SEO title<input value={f.seo_title} onChange={(e) => setF({ ...f, seo_title: e.target.value })} /><span className={f.seo_title.length > 60 ? "counter over" : "counter"}>{f.seo_title.length}/60</span></label>
            <label className="field" style={{ gridColumn: "span 2" }}>SEO description<input value={f.seo_description} onChange={(e) => setF({ ...f, seo_description: e.target.value })} /><span className={f.seo_description.length > 160 ? "counter over" : "counter"}>{f.seo_description.length}/160</span></label>
          </div>
          <label className="field mt">Body — for simple pages with no section structure<textarea style={{ fontFamily: "inherit" }} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></label>
          <button className="mt" disabled={!f.page_key || !f.title} onClick={save}>Save page</button>
        </div>
      )}
      {loading ? <Loading /> : !(data || []).length ? <Empty>No pages yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Page</th><th>Slug</th><th>Template</th><th>SEO</th><th>Status</th><th /></tr></thead>
          <tbody>{(data || []).map((p) => (
            <tr key={p.id}>
              <td><b>{p.title}</b><div className="mono muted">{p.page_key}</div></td>
              <td className="mono">{p.slug || "—"}</td>
              <td>{p.template}</td>
              <td className="muted" style={{ maxWidth: 280 }}>{p.seo_title || <Badge kind="warn">no SEO title</Badge>}</td>
              <td><StatusBadge value={p.status} /></td>
              <td><div className="row">
                <Link href={`/admin/website/builder?page=${p.id}`}><button className="sm ghost">Sections</button></Link>
                <button className="sm ghost" onClick={() => setF({ page_key: p.page_key, title: p.title, slug: p.slug, template: p.template, seo_title: p.seo_title, seo_description: p.seo_description, body: p.body })}>Edit</button>
                {p.status === "published" ? <button className="sm ghost" onClick={() => act(p, "unpublish")}>Unpublish</button> : <button className="sm ok" onClick={() => act(p, "publish")}>Publish</button>}
                <button className="sm danger" onClick={() => remove(p)}>Delete</button>
              </div></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

const slugify = (s: string) => "/" + s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

// ── Website builder ───────────────────────────────────────────────────

/** Moves item i one place and saves 0..n-1 as the order for every item
    whose position changed (older rows can share an order_index). */
function reorder(run: ReturnType<typeof useAction>, items: any[], i: number, dir: -1 | 1, save: (id: string, order: number) => Promise<any>) {
  if (!items[i + dir]) return Promise.resolve(false);
  const next = items.slice();
  [next[i], next[i + dir]] = [next[i + dir], next[i]];
  return run("Reordered", async () => {
    for (const [pos, item] of next.entries()) if (item.order_index !== pos) await must(save(item.id, pos));
  });
}

const SECTION_TYPES = ["hero", "text", "image", "gallery", "testimonial", "cta", "features", "footer", "custom"];
const BLOCK_TYPES = ["heading", "text", "image", "button", "quote", "html"];

export function Builder() {
  const run = useAction();
  const actor = useActor();
  const pages = useLoad(() => must<any[]>(get(`${R}/page-content`)));
  const media = useLoad(() => must<any[]>(get(`${R}/media`)));
  const [pageId, setPageId] = useState("");
  // Arriving from Pages → Sections opens that page.
  useEffect(() => { const p = new URLSearchParams(window.location.search).get("page"); if (p) setPageId(p); }, []);
  useEffect(() => { if (!pageId && pages.data?.length) setPageId(pages.data[0].id); }, [pages.data, pageId]);
  const sections = useLoad(() => (pageId ? must<any[]>(get(`${R}/sections?page_id=${pageId}`)) : Promise.resolve([])), [pageId]);
  const [f, setF] = useState({ section_key: "", section_type: "hero", title: "" });
  const [open, setOpen] = useState<string | null>(null);
  const list = (sections.data || []).slice().sort((a, b) => a.order_index - b.order_index);
  const patch = (id: string, body: any, label = "Saved") => run(label, () => must(api("PATCH", `${R}/sections/${id}`, { ...body, updated_by: actor }))).then(sections.reload);
  const move = (i: number, dir: -1 | 1) => reorder(run, list, i, dir, (id, order_index) => api("PATCH", `${R}/sections/${id}`, { order_index, updated_by: actor })).then(sections.reload);
  // New sections go to the end (rbac-svc numbers them).
  const add = () => run("Section added", () => must(post(`${R}/sections`, { ...f, page_id: pageId, updated_by: actor })))
    .then((ok) => { if (ok) { setF({ section_key: "", section_type: "hero", title: "" }); sections.reload(); } });
  const page = (pages.data || []).find((p) => p.id === pageId);
  return (
    <>
      <PageHead title="Website builder" text="Every page, section by section and block by block. Visibility, order and parallax are independent at every level. Changes are live for the page as soon as it's published.">
        <select value={pageId} onChange={(e) => { setPageId(e.target.value); setOpen(null); }}>
          {!(pages.data || []).length && <option value="">no pages</option>}
          {(pages.data || []).map((p) => <option key={p.id} value={p.id}>{p.title} ({p.status})</option>)}
        </select>
        <Link href="/admin/website/pages"><button className="ghost">Pages</button></Link>
      </PageHead>
      <ErrorNote error={pages.error || sections.error} />
      {!pages.loading && !(pages.data || []).length ? <Empty>Create a page first under <Link href="/admin/website/pages">Pages</Link>.</Empty> : (
        <>
          <div className="card mb">
            <h3>Add section{page ? ` to “${page.title}”` : ""}</h3>
            <div className="form mt">
              <label className="field">Section key<input className="mono" placeholder="hero_main" value={f.section_key} onChange={(e) => setF({ ...f, section_key: e.target.value })} /></label>
              <label className="field">Type<select value={f.section_type} onChange={(e) => setF({ ...f, section_type: e.target.value })}>{SECTION_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
              <label className="field">Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
            </div>
            <button className="mt" disabled={!pageId || !f.section_key} onClick={add}>Add section</button>
          </div>
          {sections.loading ? <Loading /> : !list.length ? <Empty>No sections on this page yet.</Empty> : list.map((s, i) => (
            <div key={s.id} className="card mb" style={{ opacity: s.visible ? 1 : 0.6, borderColor: open === s.id ? "var(--gold)" : undefined }}>
              <div className="row between">
                <div className="row">
                  <span className="muted mono">{i + 1}</span>
                  <b>{s.title || s.section_key}</b><Badge>{s.section_type}</Badge>
                  {!s.visible && <Badge kind="warn">hidden</Badge>}{s.parallax_enabled && <Badge kind="info">parallax ×{s.parallax_speed}</Badge>}
                </div>
                <div className="row">
                  <button className="sm ghost" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                  <button className="sm ghost" disabled={i === list.length - 1} onClick={() => move(i, 1)}>↓</button>
                  <button className="sm ghost" onClick={() => patch(s.id, { visible: !s.visible }, s.visible ? "Hidden" : "Shown")}>{s.visible ? "Hide" : "Show"}</button>
                  <button className="sm ghost" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Close" : "Edit"}</button>
                  <button className="sm danger" onClick={() => confirm(`Delete section “${s.title || s.section_key}” and its blocks?`) && run("Section deleted", () => must(del(`${R}/sections/${s.id}?actor_id=${encodeURIComponent(actor)}`))).then(sections.reload)}>Delete</button>
                </div>
              </div>
              {open === s.id && <SectionEditor s={s} media={media.data || []} onPatch={(b) => patch(s.id, b)} />}
            </div>
          ))}
        </>
      )}
    </>
  );
}

function SectionEditor({ s, media, onPatch }: { s: any; media: any[]; onPatch: (body: any) => void }) {
  const run = useAction();
  const actor = useActor();
  const [title, setTitle] = useState(s.title);
  const [px, setPx] = useState({ parallax_enabled: s.parallax_enabled, parallax_speed: s.parallax_speed, parallax_image_url: s.parallax_image_url });
  const subs = useLoad(() => must<any[]>(get(`${R}/sections/subsections?section_id=${s.id}`)), [s.id]);
  const [nb, setNb] = useState({ subsection_key: "", block_type: "text", content: "", image_url: "" });
  const blocks = (subs.data || []).slice().sort((a, b) => a.order_index - b.order_index);
  const patchSub = (id: string, body: any, label = "Saved") => run(label, () => must(api("PATCH", `${R}/sections/subsections/${id}`, { ...body, updated_by: actor }))).then(subs.reload);
  const moveSub = (i: number, dir: -1 | 1) => reorder(run, blocks, i, dir, (id, order_index) => api("PATCH", `${R}/sections/subsections/${id}`, { order_index, updated_by: actor })).then(subs.reload);
  const images = media;
  return (
    <div className="mt">
      <div className="split">
        <div>
          <label className="field">Title<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
          <button className="sm mt" disabled={title === s.title} onClick={() => onPatch({ title })}>Save title</button>
        </div>
        <div>
          <label className="row"><input type="checkbox" checked={px.parallax_enabled} onChange={(e) => setPx({ ...px, parallax_enabled: e.target.checked })} /> Parallax background</label>
          {px.parallax_enabled && (
            <div className="form mt">
              <label className="field">Speed ({px.parallax_speed})<input type="range" min={0.1} max={1} step={0.05} value={px.parallax_speed} onChange={(e) => setPx({ ...px, parallax_speed: Number(e.target.value) })} /></label>
              <label className="field">Image<select value={px.parallax_image_url} onChange={(e) => setPx({ ...px, parallax_image_url: e.target.value })}><option value="">— none —</option>{images.map((m) => <option key={m.id} value={m.url}>{m.filename} ({m.category})</option>)}</select></label>
            </div>
          )}
          <button className="sm mt" onClick={() => onPatch(px)}>Save parallax</button>
        </div>
      </div>
      <h4 className="mt">Blocks</h4>
      {subs.loading ? <Loading /> : !blocks.length ? <div className="muted">No blocks yet.</div> : blocks.map((b, i) => <Block key={b.id} b={b} first={i === 0} last={i === blocks.length - 1} media={images} onMove={(d) => moveSub(i, d)} onPatch={(body, label) => patchSub(b.id, body, label)} onDelete={() => run("Block deleted", () => must(del(`${R}/sections/subsections/${b.id}?actor_id=${encodeURIComponent(actor)}`))).then(subs.reload)} />)}
      <div className="card mt" style={{ background: "var(--panel-2)" }}>
        <div className="form">
          <label className="field">Block key<input className="mono" placeholder="headline" value={nb.subsection_key} onChange={(e) => setNb({ ...nb, subsection_key: e.target.value })} /></label>
          <label className="field">Type<select value={nb.block_type} onChange={(e) => setNb({ ...nb, block_type: e.target.value })}>{BLOCK_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          {nb.block_type === "image" && <label className="field">Image<select value={nb.image_url} onChange={(e) => setNb({ ...nb, image_url: e.target.value })}><option value="">— choose —</option>{images.map((m) => <option key={m.id} value={m.url}>{m.filename}</option>)}</select></label>}
        </div>
        {nb.block_type !== "image" && <textarea className="mt" style={{ minHeight: 70, fontFamily: nb.block_type === "html" ? undefined : "inherit" }} placeholder="Content" value={nb.content} onChange={(e) => setNb({ ...nb, content: e.target.value })} />}
        <button className="sm mt" disabled={!nb.subsection_key} onClick={() => run("Block added", () => must(post(`${R}/sections/subsections`, { ...nb, section_id: s.id, updated_by: actor }))).then((ok) => { if (ok) { setNb({ subsection_key: "", block_type: "text", content: "", image_url: "" }); subs.reload(); } })}>Add block</button>
      </div>
    </div>
  );
}

function Block({ b, first, last, media, onMove, onPatch, onDelete }: {
  b: any; first: boolean; last: boolean; media: any[]; onMove: (d: -1 | 1) => void; onPatch: (body: any, label?: string) => void; onDelete: () => void;
}) {
  const [content, setContent] = useState(b.content);
  const [image, setImage] = useState(b.image_url);
  const dirty = content !== b.content || image !== b.image_url;
  return (
    <div className="card mt" style={{ opacity: b.visible ? 1 : 0.6 }}>
      <div className="row between">
        <div className="row"><span className="mono">{b.subsection_key}</span><Badge>{b.block_type}</Badge>{!b.visible && <Badge kind="warn">hidden</Badge>}</div>
        <div className="row">
          <button className="sm ghost" disabled={first} onClick={() => onMove(-1)}>↑</button>
          <button className="sm ghost" disabled={last} onClick={() => onMove(1)}>↓</button>
          <button className="sm ghost" onClick={() => onPatch({ visible: !b.visible }, b.visible ? "Hidden" : "Shown")}>{b.visible ? "Hide" : "Show"}</button>
          <button className="sm danger" onClick={onDelete}>×</button>
        </div>
      </div>
      {b.block_type === "image" ? (
        <div className="row mt">
          <select value={image} onChange={(e) => setImage(e.target.value)}><option value="">— none —</option>{media.map((m) => <option key={m.id} value={m.url}>{m.filename}</option>)}</select>
          {image && <img src={image} alt="" style={{ height: 48, borderRadius: 6 }} />}
        </div>
      ) : <textarea className="mt" style={{ minHeight: 60, fontFamily: b.block_type === "html" ? undefined : "inherit" }} value={content} onChange={(e) => setContent(e.target.value)} />}
      {dirty && <button className="sm mt" onClick={() => onPatch({ content, image_url: image })}>Save block</button>}
    </div>
  );
}

// ── Themes ────────────────────────────────────────────────────────────
const COLOR_KEYS: [string, string][] = [
  ["primary", "Primary"], ["secondary", "Secondary"], ["accent", "Accent"], ["background", "Background"], ["surface", "Surface"], ["text", "Text"],
  ["text_muted", "Muted text"], ["success", "Success"], ["warning", "Warning"], ["error", "Error"], ["info", "Info"], ["border", "Border"],
];

export function Themes() {
  const run = useAction();
  const actor = useActor();
  const themes = useLoad(() => must<any[]>(get(`${R}/themes`)));
  const [sel, setSel] = useState("");
  useEffect(() => { if (!sel && themes.data?.length) setSel((themes.data.find((t) => t.is_active) || themes.data[0]).id); }, [themes.data, sel]);
  const colors = useLoad(() => (sel ? must<any[]>(get(`${R}/themes/${sel}/colors`)) : Promise.resolve([])), [sel]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { setDraft({}); }, [sel]);
  const [nf, setNf] = useState({ name: "", seed_defaults: true });
  const value = (k: string) => draft[k] ?? (colors.data || []).find((c) => c.color_key === k)?.hex_value ?? "";
  const changed = Object.keys(draft).filter((k) => draft[k] !== (colors.data || []).find((c) => c.color_key === k)?.hex_value);
  const save = () => run(`${changed.length} colour(s) saved`, async () => {
    for (const k of changed) await must(post(`${R}/themes/${sel}/colors`, { color_key: k, hex_value: draft[k], updated_by: actor }));
  }).then(() => { setDraft({}); colors.reload(); });
  const theme = (themes.data || []).find((t) => t.id === sel);
  const v = (k: string) => value(k) || "#888888";
  return (
    <>
      <PageHead title="Colours & theme" text="The platform's colour palettes. Exactly one theme is active at a time — activating one deactivates the rest automatically." />
      <ErrorNote error={themes.error} />
      <div className="split mb">
        <div className="card">
          <h3>Themes</h3>
          {themes.loading ? <Loading /> : !(themes.data || []).length ? <Empty>No themes yet.</Empty> : (
            <table className="mt"><tbody>{(themes.data || []).map((t) => (
              <tr key={t.id} className="click" onClick={() => setSel(t.id)} style={t.id === sel ? { outline: "1px solid var(--gold)" } : undefined}>
                <td><b>{t.name}</b></td><td>{t.is_active ? <Badge kind="ok">active</Badge> : <button className="sm ghost" onClick={(e) => { e.stopPropagation(); run(`${t.name} is now active`, () => must(post(`${R}/themes/${t.id}/activate?actor_id=${encodeURIComponent(actor)}`))).then(themes.reload); }}>Activate</button>}</td>
                <td>{!t.is_active && <button className="sm danger" onClick={(e) => { e.stopPropagation(); if (confirm(`Delete theme “${t.name}”?`)) run("Theme deleted", () => must(del(`${R}/themes/${t.id}?actor_id=${encodeURIComponent(actor)}`))).then(() => { setSel(""); themes.reload(); }); }}>Delete</button>}</td>
              </tr>
            ))}</tbody></table>
          )}
          <div className="row mt">
            <input placeholder="New theme name" value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} />
            <label className="row muted" style={{ fontSize: 12 }}><input type="checkbox" checked={nf.seed_defaults} onChange={(e) => setNf({ ...nf, seed_defaults: e.target.checked })} /> start from default palette</label>
            <button disabled={!nf.name} onClick={() => run("Theme created", async () => { const t = await must<any>(post(`${R}/themes`, { ...nf, created_by: actor })); setSel(t.id); }).then(() => { setNf({ name: "", seed_defaults: true }); themes.reload(); })}>Create</button>
          </div>
        </div>
        <div className="card" style={{ background: v("background"), borderColor: v("border"), color: v("text") }}>
          <div style={{ fontSize: 11, letterSpacing: ".12em", color: v("text_muted") }}>PREVIEW · {theme?.name || "—"}</div>
          <div style={{ background: v("surface"), border: `1px solid ${v("border")}`, borderRadius: 10, padding: 16, marginTop: 10 }}>
            <div style={{ fontWeight: 800, fontSize: 18, color: v("primary") }}>Your mobility travels with you</div>
            <div style={{ color: v("text_muted"), marginTop: 6 }}>Pick a plan, choose a vehicle class, collect it at a hub.</div>
            <div className="row mt">
              <span style={{ background: v("primary"), color: v("background"), padding: "6px 12px", borderRadius: 8, fontWeight: 700 }}>Join now</span>
              <span style={{ border: `1px solid ${v("secondary")}`, color: v("secondary"), padding: "6px 12px", borderRadius: 8 }}>See plans</span>
              <span style={{ color: v("accent") }}>Learn more →</span>
            </div>
            <div className="row mt">{["success", "warning", "error", "info"].map((k) => <span key={k} style={{ color: v(k), fontSize: 12 }}>● {k}</span>)}</div>
          </div>
        </div>
      </div>
      {sel && (
        <div className="card">
          <div className="row between"><h3>Palette — {theme?.name}</h3><button disabled={!changed.length} onClick={save}>Save {changed.length || ""} change{changed.length === 1 ? "" : "s"}</button></div>
          {colors.loading ? <Loading /> : (
            <div className="form mt">{COLOR_KEYS.map(([k, label]) => (
              <label key={k} className="field">{label}
                <div className="row" style={{ flexWrap: "nowrap" }}>
                  <input type="color" style={{ width: 44, padding: 2, height: 32 }} value={/^#[0-9a-f]{6}$/i.test(value(k)) ? value(k) : "#000000"} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
                  <input className="mono" style={{ flex: 1 }} value={value(k)} placeholder="#rrggbb" onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
                </div>
              </label>
            ))}</div>
          )}
        </div>
      )}
    </>
  );
}

// ── Media library ─────────────────────────────────────────────────────
const MEDIA_CATS = ["hero", "gallery", "icon", "background", "general"];

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export function Media() {
  const run = useAction();
  const actor = useActor();
  const [cat, setCat] = useState("");
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`${R}/media${cat ? `?category=${cat}` : ""}`)), [cat]);
  const [f, setF] = useState({ filename: "", url: "", category: "general", alt_text: "" });
  const add = () => run("Added to the library", () => must(post(`${R}/media`, { ...f, uploaded_by: actor }))).then((ok) => { if (ok) { setF({ filename: "", url: "", category: f.category, alt_text: "" }); reload(); } });
  // Files are hosted by marketing-svc (which serves them publicly), then listed here.
  const upload = (file: File) => run("Uploaded", async () => {
    const hosted = await must<any>(post("/api/v1/marketing/media", { title: file.name, content_type: file.type, data_base64: await fileToBase64(file), created_by: actor }));
    if (!hosted.url) throw new Error("Uploaded, but marketing-svc has no public media URL configured.");
    await must(post(`${R}/media`, { filename: file.name, url: hosted.url, category: f.category, alt_text: f.alt_text, uploaded_by: actor }));
  }).then(reload);
  return (
    <>
      <PageHead title="Media library" text="Images every section and block can use — hero backgrounds, gallery images, icons.">
        <select value={cat} onChange={(e) => setCat(e.target.value)}><option value="">All categories</option>{MEDIA_CATS.map((c) => <option key={c}>{c}</option>)}</select>
      </PageHead>
      <ErrorNote error={error} />
      <div className="card mb">
        <h3>Add image</h3>
        <div className="form mt">
          <label className="field">Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{MEDIA_CATS.map((c) => <option key={c}>{c}</option>)}</select></label>
          <label className="field">Alt text<input value={f.alt_text} placeholder="Describe the image" onChange={(e) => setF({ ...f, alt_text: e.target.value })} /></label>
        </div>
        <div className="row mt">
          <label className="btn ghost" style={{ cursor: "pointer" }}>Upload a file…<input type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
          <span className="muted">or by URL:</span>
          <input placeholder="Filename" value={f.filename} onChange={(e) => setF({ ...f, filename: e.target.value })} />
          <input style={{ flex: 1 }} placeholder="https://…" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />
          <button disabled={!f.filename || !f.url} onClick={add}>Add</button>
        </div>
      </div>
      {loading ? <Loading /> : !(data || []).length ? <Empty>No images{cat ? ` in ${cat}` : ""}.</Empty> : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>{(data || []).map((m) => (
          <div key={m.id} className="card" style={{ padding: 10 }}>
            <img src={m.url} alt={m.alt_text} style={{ width: "100%", aspectRatio: "4/3", objectFit: "cover", borderRadius: 8, background: "var(--panel-2)" }} />
            <div className="row between mt"><span className="clip" style={{ maxWidth: 120 }} title={m.filename}>{m.filename}</span><Badge>{m.category}</Badge></div>
            <div className="row mt">
              <button className="sm ghost" onClick={() => navigator.clipboard?.writeText(m.url)}>Copy URL</button>
              <button className="sm danger" onClick={() => confirm(`Remove ${m.filename}?`) && run("Removed", () => must(del(`${R}/media/${m.id}?actor_id=${encodeURIComponent(actor)}`))).then(reload)}>Remove</button>
            </div>
          </div>
        ))}</div>
      )}
    </>
  );
}

// ── AI assist settings ────────────────────────────────────────────────
const ASSIST_DEFAULT = { brand_voice: "", model: "claude-sonnet-5", auto_generate: false, require_review: true };

export function AiAssist() {
  const run = useAction();
  const actor = useActor();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`${R}/settings?category=ai_content_assist`)));
  const [f, setF] = useState<typeof ASSIST_DEFAULT | null>(null);
  useEffect(() => { if (data && !f) setF({ ...ASSIST_DEFAULT, ...(data.find((s) => s.key === "website_builder")?.value || {}) }); }, [data, f]);
  if (loading || !f) return <Loading />;
  const saved = data?.find((s) => s.key === "website_builder");
  return (
    <>
      <PageHead title="AI assist" text="How AI-drafted copy behaves in the Website builder — brand voice, model, whether new sections get a first draft, and whether a person must review AI copy before it's published. Stored as platform settings (ai_content_assist)." />
      <ErrorNote error={error} />
      <div className="card">
        <label className="field">Brand voice / tone prompt<textarea style={{ fontFamily: "inherit", minHeight: 110 }} placeholder="Confident, warm, never pushy. Short sentences. Say 'members', not 'customers'." value={f.brand_voice} onChange={(e) => setF({ ...f, brand_voice: e.target.value })} /></label>
        <div className="form mt">
          <label className="field">Default model<select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}>{[["claude-sonnet-5", "Claude Sonnet 5"], ["claude-opus-5", "Claude Opus 5"], ["claude-haiku-4-5", "Claude Haiku 4.5"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="field">On a new section<select value={String(f.auto_generate)} onChange={(e) => setF({ ...f, auto_generate: e.target.value === "true" })}><option value="false">Off — draft manually</option><option value="true">AI drafts a first pass</option></select></label>
        </div>
        <label className="row mt"><input type="checkbox" checked={f.require_review} onChange={(e) => setF({ ...f, require_review: e.target.checked })} /> Require a person to review AI-drafted copy before it&apos;s published <Badge kind="ok">recommended</Badge></label>
        {!f.require_review && <div className="notice warn mt">AI copy will go live without anyone reading it first.</div>}
        <div className="row mt">
          <button onClick={() => run("AI assist saved", () => must(post(`${R}/settings`, { category: "ai_content_assist", key: "website_builder", value: f, updated_by: actor }))).then(reload)}>Save settings</button>
          {saved && <span className="muted">Last saved by {saved.updated_by}</span>}
        </div>
      </div>
    </>
  );
}

// ── Platform settings ─────────────────────────────────────────────────
const SETTING_CATS: [string, string, string][] = [
  ["payment_gateway", "Payment gateways", '{"provider": "stripe", "publishable_key": "pk_live_…", "enabled": true}'],
  ["branding", "Branding / UI-UX", '{"logo_url": "https://…", "product_name": "VOYQI"}'],
  ["feature_flag", "Feature flags", '{"enabled": true}'],
  ["general", "General", '{"default_market": "Miami"}'],
];

export function Settings() {
  const run = useAction();
  const actor = useActor();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`${R}/settings`)));
  const [tab, setTab] = useState("feature_flag");
  const [f, setF] = useState({ key: "", value: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [edit, setEdit] = useState("");
  // A flag shows its new state while the change is being saved.
  const [flagging, setFlagging] = useState<Record<string, boolean>>({});
  const flip = (s: any, enabled: boolean) => {
    setFlagging((p) => ({ ...p, [s.key]: enabled }));
    save(s.key, { ...s.value, enabled }, enabled ? `${s.key} on` : `${s.key} off`).finally(() => setFlagging(({ [s.key]: _, ...rest }) => rest));
  };
  let parsed: any = null, parseError = "";
  try { parsed = f.value.trim() ? JSON.parse(f.value) : null; if (parsed !== null && (typeof parsed !== "object" || Array.isArray(parsed))) parseError = "Value must be a JSON object, e.g. {\"enabled\": true}"; } catch (e: any) { parseError = e.message; }
  const rows = (data || []).filter((s) => s.category === tab);
  const save = (key: string, value: any, label = "Setting saved") => run(label, () => must(post(`${R}/settings`, { category: tab, key, value, updated_by: actor }))).then(reload);
  const cat = SETTING_CATS.find(([k]) => k === tab)!;
  return (
    <>
      <PageHead title="Platform settings" text="Payment gateways, branding, feature flags and general configuration. None of this touches access control — that stays with roles and ABAC. Third-party credentials live under Integrations, encrypted." />
      <ErrorNote error={error} />
      <div className="tabs">{SETTING_CATS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setOpen(null); }}>{l} <span className="muted">({(data || []).filter((s) => s.category === k).length})</span></button>)}</div>
      <div className="card mb">
        <h3>Add {cat[1].toLowerCase()} setting</h3>
        <div className="form mt">
          <label className="field">Key<input className="mono" placeholder={tab === "feature_flag" ? "new_checkout" : "key"} value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} /></label>
        </div>
        <label className="field mt">Value (JSON object)<textarea style={{ minHeight: 70 }} placeholder={cat[2]} value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} /></label>
        {parseError && f.value && <div className="notice bad mt">{parseError}</div>}
        <button className="mt" disabled={!f.key || !parsed || !!parseError} onClick={() => save(f.key, parsed).then(() => setF({ key: "", value: "" }))}>Save setting</button>
      </div>
      {loading ? <Loading /> : !rows.length ? <Empty>No {cat[1].toLowerCase()} settings.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Key</th><th>Value</th><th>Updated by</th><th /></tr></thead>
          <tbody>{rows.map((s) => (
            <Fragment key={s.id}>
              <tr>
                <td className="mono"><b>{s.key}</b></td>
                <td>{tab === "feature_flag" && typeof s.value?.enabled === "boolean"
                  ? (() => { const v = flagging[s.key] ?? s.value.enabled; return <label className="row"><input type="checkbox" checked={v} onChange={(e) => flip(s, e.target.checked)} /> {v ? <Badge kind="ok">on</Badge> : <Badge>off</Badge>}</label>; })()
                  : <span className="mono clip" title={JSON.stringify(s.value)}>{JSON.stringify(s.value)}</span>}</td>
                <td className="muted">{s.updated_by}</td>
                <td><div className="row">
                  <button className="sm ghost" onClick={() => { setOpen(open === s.id ? null : s.id); setEdit(JSON.stringify(s.value, null, 2)); }}>{open === s.id ? "Close" : "Edit"}</button>
                  <button className="sm danger" onClick={() => confirm(`Delete ${s.key}?`) && run("Deleted", () => must(del(`${R}/settings/${s.id}?actor_id=${encodeURIComponent(actor)}`))).then(reload)}>Delete</button>
                </div></td>
              </tr>
              {open === s.id && (
                <tr><td colSpan={4}>
                  <textarea value={edit} onChange={(e) => setEdit(e.target.value)} />
                  <button className="sm mt" onClick={() => { let v; try { v = JSON.parse(edit); } catch (e: any) { return alert(`Invalid JSON: ${e.message}`); } save(s.key, v).then(() => setOpen(null)); }}>Save</button>
                </td></tr>
              )}
            </Fragment>
          ))}</tbody>
        </table></div>
      )}
      <div className="muted mt" style={{ fontSize: 12 }}>Every change is recorded in the <Link href="/admin/security/audit">audit log</Link>.</div>
    </>
  );
}
