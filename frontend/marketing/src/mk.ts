/* Typed access to marketing-svc through the gateway. */
import { api, del, get, must, post } from "@shared/api";

export const CHANNELS = ["email", "sms", "push", "voice", "social"] as const;
export type Channel = (typeof CHANNELS)[number];
export const CHANNEL_LABEL: Record<string, string> = { email: "Email", sms: "SMS", push: "Push", voice: "Voice", social: "Social" };
export const CHANNEL_ICON: Record<string, string> = { email: "✉", sms: "💬", push: "🔔", voice: "📞", social: "◎" };

export type Campaign = { id: string; name: string; channel: Channel; objective: string; status: string; audience_segment_id: string | null; created_by: string };
export type Draft = { id: string; campaign_id: string; channel: Channel; subject_line: string; body_text: string; media_asset_ids: string[]; status: string; guardrail_findings: string[]; reviewed_by: string | null; created_by: string };
export type Send = { id: string; campaign_id: string; content_draft_id: string; channel: Channel; scheduled_at: string; status: string; recipients_targeted: number; recipients_suppressed: number; recipients_failed: number; recipients_console: number; failure_detail: string; sent_at: string | null };
export type Segment = { id: string; name: string; filter_criteria: Record<string, unknown>; member_count: number; created_by: string };
export type Snapshot = { id: string; campaign_id: string; channel: Channel; sent_count: number; open_count: number; click_count: number; conversion_count: number; revenue_usd: number };
export type CalendarEntry = { id: string; channel: Channel; title: string; scheduled_at: string; reference_id: string; reference_type: string };

const M = "/api/v1/marketing";
const q = (params: Record<string, string | undefined | null>) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return s ? `?${s}` : "";
};

export const mk = {
  campaigns: (channel?: string) => must<Campaign[]>(get(`${M}/campaigns${q({ channel })}`)),
  campaign: (id: string) => must<Campaign>(get(`${M}/campaigns/${id}`)),
  createCampaign: (body: Partial<Campaign>) => must<Campaign>(post(`${M}/campaigns`, body)),
  setStatus: (id: string, status: string) => must<Campaign>(api_patch(`${M}/campaigns/${id}/status`, { status })),

  drafts: (campaign_id?: string, status?: string) => must<Draft[]>(get(`${M}/content${q({ campaign_id, status })}`)),
  createDraft: (body: Partial<Draft>) => must<Draft>(post(`${M}/content`, body)),
  aiDraft: (campaign_id: string, brief: string) => must<{ draft: Draft | null }>(post(`${M}/content/ai-draft`, { campaign_id, brief })),
  approveDraft: (id: string, reviewed_by: string) => must<Draft>(post(`${M}/content/${id}/approve`, { reviewed_by })),
  rejectDraft: (id: string, reviewed_by: string) => must<Draft>(post(`${M}/content/${id}/reject`, { reviewed_by })),

  sends: (campaign_id?: string) => must<Send[]>(get(`${M}/sends${q({ campaign_id })}`)),
  scheduleSend: (body: { campaign_id: string; content_draft_id: string; channel: string; scheduled_at: string }) => must<Send>(post(`${M}/sends`, body)),
  executeSend: (id: string, recipient_contacts: string[] = []) => must<Send>(post(`${M}/sends/${id}/execute`, { recipient_contacts })),

  segments: () => must<Segment[]>(get(`${M}/audience/segments`)),
  createSegment: (body: Partial<Segment>) => must<Segment>(post(`${M}/audience/segments`, body)),
  members: (id: string) => must<any[]>(get(`${M}/audience/segments/${id}/members`)),
  addMembers: (id: string, members: { name?: string; email?: string; phone?: string; push_token?: string }[]) => must<{ added: number; skipped: number }>(post(`${M}/audience/segments/${id}/members`, { members })),
  removeMember: (id: string, memberId: string) => must(del(`${M}/audience/segments/${id}/members/${memberId}`)),
  setMemberCount: (id: string, member_count: number) => must<Segment>(api_patch(`${M}/audience/segments/${id}/member-count`, { member_count })),

  suppression: (channel?: string) => must<any[]>(get(`${M}/suppression${q({ channel })}`)),
  suppress: (body: { channel: string; contact_identifier: string; reason: string }) => must(post(`${M}/suppression`, body)),
  checkSuppressed: (channel: string, contact_identifier: string) => must<{ suppressed: boolean; reason: string | null }>(get(`${M}/suppression/check${q({ channel, contact_identifier })}`)),
  unsuppress: (id: string, removed_by: string) => must(del(`${M}/suppression/${id}${q({ removed_by })}`)),

  calendar: () => must<CalendarEntry[]>(get(`${M}/calendar`)),
  conflicts: (channel: string, proposed_at: string) => must<CalendarEntry[]>(get(`${M}/calendar/check-conflict${q({ channel, proposed_at })}`)),

  snapshots: (campaign_id?: string) => must<Snapshot[]>(get(`${M}/analytics/snapshots${q({ campaign_id })}`)),
  summary: (campaign_id: string) => must<any>(get(`${M}/analytics/campaigns/${campaign_id}/summary`)),
  recordSnapshot: (body: Partial<Snapshot>) => must(post(`${M}/analytics/snapshots`, body)),

  library: (channel?: string) => must<any[]>(get(`${M}/library${q({ channel })}`)),
  saveToLibrary: (body: { title: string; channel: string; body_text: string; tags: string[]; created_by: string }) => must(post(`${M}/library`, body)),
  deleteFromLibrary: (id: string) => must(del(`${M}/library/${id}`)),

  accounts: () => must<any[]>(get(`${M}/social/accounts`)),
  connectAccount: (body: { platform: string; handle: string }) => must(post(`${M}/social/accounts`, body)),
  disconnectAccount: (id: string) => must(post(`${M}/social/accounts/${id}/disconnect`)),
  posts: () => must<any[]>(get(`${M}/social/posts`)),
  schedulePost: (body: { social_account_id: string; content_draft_id: string; scheduled_at: string; media_asset_id?: string | null }) => must(post(`${M}/social/posts`, body)),
  assets: () => must<{ id: string; title: string; content_type: string; source: string; url: string; created_at: string }[]>(get(`${M}/media`)),
  uploadAsset: (body: { title: string; content_type: string; data_base64: string; created_by: string }) => must<{ id: string; url: string }>(post(`${M}/media`, body)),
  publishPost: (id: string) => must(post(`${M}/social/posts/${id}/publish`, {})),

  abTests: (campaign_id?: string) => must<any[]>(get(`${M}/ab-tests${q({ campaign_id })}`)),
  createAbTest: (body: { campaign_id: string; variant_a_draft_id: string; variant_b_draft_id: string; metric: string }) => must(post(`${M}/ab-tests`, body)),
  recordAbResult: (id: string, body: { variant: string; sent_delta: number; converted_delta: number }) => must(post(`${M}/ab-tests/${id}/record-result`, body)),
  evaluateAbTest: (id: string) => must<any>(post(`${M}/ab-tests/${id}/evaluate`, {})),

  media: () => must<any[]>(get(`${M}/media-generation`)),
  requestMedia: (body: { prompt: string; media_type: string; requested_by: string }) => must(post(`${M}/media-generation`, body)),
  completeMedia: (id: string, result_url: string) => must(post(`${M}/media-generation/${id}/complete`, { result_url })),

  notes: (campaign_id?: string) => must<any[]>(get(`${M}/notes${q({ campaign_id })}`)),
  addNote: (body: { author: string; body: string; campaign_id?: string }) => must(post(`${M}/notes`, body)),
  deleteNote: (id: string) => must(del(`${M}/notes/${id}`)),

  compose: (body: { channel: string; mobility_class: string; objective: string; tones: string[]; context: string }) => must<{ variants: { tone: string; source: string; subject: string; body: string }[] }>(post(`${M}/content/compose`, body)),
  autopilot: () => must<any>(get(`${M}/autopilot`)),
  saveAutopilot: (body: any) => must<any>(api("PUT", `${M}/autopilot`, body)),
  planNow: () => must<{ planned: number }>(post(`${M}/autopilot/plan-now`, {})),
  slots: () => must<any[]>(get(`${M}/autopilot/slots`)),
  approveSlot: (id: string) => must(post(`${M}/autopilot/slots/${id}/approve`, {})),
  rejectSlot: (id: string) => must(post(`${M}/autopilot/slots/${id}/reject`, {})),
  leads: (campaign_id?: string) => must<any[]>(get(`${M}/lead-captures${q({ campaign_id })}`)),
};

function api_patch(path: string, body: unknown) { return api("PATCH", path, body); }

export const pct = (n: number, d: number) => (d ? `${((n / d) * 100).toFixed(1)}%` : "—");
export const money = (n: number) => "$" + Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
/** datetime-local input value → ISO string in UTC. */
export const toIso = (local: string) => new Date(local).toISOString();
export const inHours = (h: number) => { const d = new Date(Date.now() + h * 3600e3); d.setMinutes(0, 0, 0); return new Date(d.getTime() - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 16); };

/** File → base64 (without the data: prefix) for upload. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export const CLASSES: [string, string][] = [
  ["urban", "Urban ($449)"], ["family", "Family ($649)"], ["executive", "Executive ($859)"], ["luxury", "Luxury ($1,499)"],
  ["performance", "Performance ($2,700)"], ["adventure", "Adventure ($549)"], ["luxury_plus", "Luxury Plus ($5,800)"], ["performance_plus", "Performance Plus ($5,800)"],
];
export const OBJECTIVES: [string, string][] = [
  ["acquisition", "Acquisition"], ["upgrade", "Upgrade class"], ["retention", "Retention"], ["referral", "Referral — earn GU"],
  ["winback", "Reactivation / winback"], ["term_conversion", "Term membership conversion"], ["service_upgrade", "Service level upgrade"], ["travel_mode", "Travel mode activation"],
];
export const TONES: [string, string][] = [["premium", "Premium & aspirational"], ["personal", "Warm & personal"], ["urgent", "Urgent & action-focused"], ["exclusive", "Exclusive & invitation-only"], ["friendly", "Friendly & accessible"]];
