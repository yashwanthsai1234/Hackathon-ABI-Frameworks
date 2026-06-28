// Generates frontend/public/export.json — a realistic fixture matching SPEC §C.3.
// Highlight char offsets are computed from the assembled note_text via indexOf,
// so they ALWAYS index correctly into the same string the UI renders.
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, "..", "public", "export.json");

const RUN_ID = "run_2026-06-28T0410_a91f";
const GENERATED_AT = "2026-06-28T04:10:33Z";

// ---- highlight helper: find each (field,value) literal inside note_text ----
function highlightsFor(noteText, specs) {
  const out = [];
  for (const { field, value, from = 0 } of specs) {
    const start = noteText.indexOf(value, from);
    if (start === -1) continue; // never fabricate an offset
    out.push({ field, value, start, end: start + value.length });
  }
  return out;
}

// ---- evidence graph builder ----
// sources: [{id,type:'note'|'dx'|'assessment',label}]; relation per source to wound
function evidenceGraph(woundLabel, sources) {
  const nodes = [
    { id: "wound", type: "wound", label: woundLabel },
    ...sources.map((s) => ({ id: s.id, type: s.type, label: s.label })),
  ];
  const edges = sources.map((s) => ({
    id: `${s.id}->wound`,
    source: s.id,
    target: "wound",
    relation: s.relation, // 'agree' | 'conflict'
    field: s.field || null,
  }));
  const agreeing_sources = sources.filter((s) => s.relation === "agree").length;
  return { nodes, edges, agreeing_sources };
}

function checks({ wound, mcb, measure }) {
  return [
    { label: "Active wound diagnosis", code: wound.code || null, status: wound.ok ? "pass" : "fail", detail: wound.detail },
    { label: "Medicare Part B active", code: "MCB", status: mcb.ok ? "pass" : "fail", detail: mcb.detail },
    { label: "Measurements complete (L×W×D)", code: null, status: measure.ok ? "pass" : measure.warn ? "warn" : "fail", detail: measure.detail },
  ];
}

// ---------------------------------------------------------------------------
// Hand-crafted "hero" patients using the REAL API sample data.
// ---------------------------------------------------------------------------
const hero = [];

// FA-004 — Diabetic foot ulcer, full L×W×D, MCB active, two agreeing sources → AUTO_ACCEPT
{
  const note =
    "Subjective: Patient reports pain at Right plantar wound site, rates 9/10.\n" +
    "Objective: Wound assessment performed. Diabetic diabetic Right plantar measures 4.3 cm x 1.8 cm x 0.3 cm.\n" +
    "  Wound bed: 28% slough, 72% granulation tissue. Periwound: intact.\n" +
    "  Drainage: moderate. Odor: present, foul.\n" +
    "Assessment: Diabetic diabetic Right plantar — improving.\n" +
    "Plan: Debridement consult ordered. Alginate dressing applied.";
  hero.push({
    patient_id: "FA-004", id: 4, name: "R. Alvarez",
    payer: { code: "MCB", name: "Medicare Part B", type: "Medicare" },
    wound: { type: "Diabetic foot ulcer", stage: null, location: "Right plantar", L: 4.3, W: 1.8, D: 0.3, drainage: "moderate", format: "Wound (IDT) — SOAP" },
    route: "auto_accept", confidence: 0.93,
    field_confidence: { type: 0.95, location: 0.97, measurements: 0.96, drainage: 0.9, stage: 0.7 },
    reason: "Active DFU (E11.621) with Medicare Part B coverage; full L×W×D documented and corroborated across the IDT note and the wound assessment. Confidence 0.93 ≥ 0.80 threshold.",
    note_text: note,
    highlights: highlightsFor(note, [
      { field: "type", value: "Diabetic diabetic" },
      { field: "location", value: "Right plantar" },
      { field: "measurements", value: "4.3 cm x 1.8 cm x 0.3 cm" },
      { field: "drainage", value: "moderate" },
    ]),
    eligibility_checks: checks({
      wound: { ok: true, code: "E11.621", detail: "Type 2 diabetes w/ foot ulcer — active" },
      mcb: { ok: true, detail: "Effective 2024-01-01, no term date" },
      measure: { ok: true, detail: "4.3 × 1.8 × 0.3 cm — all three dimensions present" },
    }),
    evidence_graph: evidenceGraph("DFU · Right plantar · 4.3×1.8×0.3", [
      { id: "idt", type: "note", label: "Wound (IDT) note", relation: "agree", field: "measurements" },
      { id: "dx", type: "dx", label: "Dx E11.621 (active)", relation: "agree", field: "type" },
      { id: "asmt", type: "assessment", label: "HP Skin & Wound assessment", relation: "agree", field: "location" },
    ]),
  });
}

// FA-001 — Pressure ulcer, Stage 3, MCB active, but NO depth documented → FLAG
{
  const note =
    "*Envive Care Conference Review - V 4.0\n" +
    "Wound Status: Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3\n" +
    "Drainage present - serosanguineous, heavy. Odor present. Treatment: Foam dressing change daily.";
  hero.push({
    patient_id: "FA-001", id: 1, name: "M. Chen",
    payer: { code: "MCB", name: "Medicare Part B", type: "Medicare" },
    wound: { type: "Pressure ulcer", stage: "Stage 3", location: "Right hip", L: 2.9, W: 2.8, D: null, drainage: "serosanguineous, heavy", format: "Wound (SPN) — Envive" },
    route: "flag_for_review", confidence: 0.64,
    field_confidence: { type: 0.92, location: 0.9, measurements: 0.45, drainage: 0.85, stage: 0.88 },
    reason: "Wound depth not documented (only L×W captured), so measurements are incomplete. Eligibility otherwise met (L89.143 active, MCB active). Routed to review to confirm depth before billing.",
    note_text: note,
    highlights: highlightsFor(note, [
      { field: "type", value: "Pressure Ulcer" },
      { field: "location", value: "Right hip" },
      { field: "measurements", value: "2.9 cm x 2.8 cm" },
      { field: "stage", value: "Stage 3" },
      { field: "drainage", value: "serosanguineous, heavy" },
    ]),
    eligibility_checks: checks({
      wound: { ok: true, code: "L89.143", detail: "Pressure ulcer right hip, stage 3 — active" },
      mcb: { ok: true, detail: "Effective 2024-01-01, no term date" },
      measure: { ok: false, warn: true, detail: "Depth missing — only 2.9 × 2.8 cm documented" },
    }),
    evidence_graph: evidenceGraph("PU · Right hip · 2.9×2.8×?", [
      { id: "spn", type: "note", label: "Wound (SPN) note", relation: "agree", field: "type" },
      { id: "dx", type: "dx", label: "Dx L89.143 (active)", relation: "agree", field: "stage" },
      { id: "depth", type: "note", label: "Depth field", relation: "conflict", field: "measurements" },
    ]),
  });
}

// FA-002 — Multi-wound + Stage N/A conflict across two notes → FLAG
{
  const note =
    "*Envive Care Conference Review - V 4.0\n" +
    "Wound Status: Pressure Ulcer to Left buttock / Measures 5.9 cm x 4.5 cm / Stage: N/A\n" +
    "Drainage present - serosanguineous, heavy. No odor noted. Treatment: Foam dressing change daily.\n" +
    "--- HP Skin & Wound Note (2026-05-15) ---\n" +
    "Pt seen for wound eval. Pressure Ulcer Left buttock measures aprx 5.9 x 4.5cm, depth 1.8cm.\n" +
    "Min drainage serosanguineous. Heel wound also eval - L heel 3.5x2.7, 0.9cm deep, slight serous.";
  hero.push({
    patient_id: "FA-002", id: 2, name: "D. Okafor",
    payer: { code: "MCB", name: "Medicare Part B", type: "Medicare" },
    wound: { type: "Pressure ulcer", stage: "N/A", location: "Left buttock", L: 5.9, W: 4.5, D: 1.8, drainage: "serosanguineous", format: "Wound Care Progress Note — Envive" },
    route: "flag_for_review", confidence: 0.58,
    field_confidence: { type: 0.9, location: 0.88, measurements: 0.6, drainage: 0.8, stage: 0.3 },
    reason: "Two wounds documented (left buttock + L heel) and stage is recorded as N/A in the primary note while a second note adds depth — multi-wound + stage conflict. Needs reviewer to select billable wound and confirm stage.",
    note_text: note,
    highlights: highlightsFor(note, [
      { field: "type", value: "Pressure Ulcer" },
      { field: "location", value: "Left buttock" },
      { field: "measurements", value: "5.9 cm x 4.5 cm" },
      { field: "stage", value: "Stage: N/A" },
      { field: "measurements", value: "depth 1.8cm" },
      { field: "secondary_wound", value: "L heel 3.5x2.7, 0.9cm deep" },
    ]),
    eligibility_checks: checks({
      wound: { ok: true, code: "L89.323", detail: "Pressure ulcer left buttock — active" },
      mcb: { ok: true, detail: "Effective 2024-01-01, no term date" },
      measure: { ok: false, warn: true, detail: "Conflicting/partial: 5.9×4.5 (no depth) vs +1.8cm depth in 2nd note" },
    }),
    evidence_graph: evidenceGraph("PU · Left buttock · 5.9×4.5×1.8?", [
      { id: "spn", type: "note", label: "Progress note (Stage N/A)", relation: "conflict", field: "stage" },
      { id: "hp", type: "note", label: "HP Skin & Wound (+depth)", relation: "agree", field: "measurements" },
      { id: "heel", type: "note", label: "2nd wound · L heel", relation: "conflict", field: "secondary_wound" },
      { id: "dx", type: "dx", label: "Dx L89.323 (active)", relation: "agree", field: "type" },
    ]),
  });
}

// ---------------------------------------------------------------------------
// Procedurally generated patients to fill a realistic triage grid (~48 total).
// Every note_text is one of the 4 real formats; highlights computed by indexOf.
// ---------------------------------------------------------------------------
const FIRST = ["J","K","L","S","T","R","A","B","C","D","E","F","G","H","P","N","O","V","W","Y"];
const LAST = ["Nguyen","Patel","Garcia","Smith","Johnson","Williams","Brown","Davis","Martinez","Lopez","Wilson","Anderson","Taylor","Thomas","Moore","Jackson","Lee","Harris","Clark","Lewis","Walker","Hall","Young","King"];
const PAYERS = [
  { code: "MCB", name: "Medicare Part B", type: "Medicare", weight: 0.62 },
  { code: "MCA", name: "Medicare Advantage", type: "Medicare", weight: 0.18 },
  { code: "MCD", name: "Medicaid", type: "Medicaid", weight: 0.12 },
  { code: "HMO", name: "Commercial HMO", type: "Commercial", weight: 0.08 },
];

const WOUNDS = [
  { type: "Diabetic foot ulcer", code: "E11.621", locations: ["Right plantar","Left plantar","Right heel","Left great toe"] },
  { type: "Pressure ulcer", code: "L89.143", locations: ["Right hip","Sacrum","Left buttock","Right heel"] },
  { type: "Venous stasis ulcer", code: "I83.012", locations: ["Right medial ankle","Left lower leg","Right calf"] },
  { type: "Arterial ulcer", code: "I70.231", locations: ["Left lateral foot","Right toe"] },
  { type: "Non-pressure chronic ulcer", code: "L97.421", locations: ["Left heel","Right midfoot"] },
];
const STAGES = ["Stage 2","Stage 3","Stage 4","Unstageable", null];

// deterministic PRNG for stable fixture
let seed = 1337;
const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
const pick = (a) => a[Math.floor(rand() * a.length)];
const r2 = (n) => Math.round(n * 10) / 10;

function pickPayer() {
  const x = rand();
  let acc = 0;
  for (const p of PAYERS) { acc += p.weight; if (x <= acc) return p; }
  return PAYERS[0];
}

function soapNote(w, loc, L, W, D, drain) {
  return `Subjective: Patient reports discomfort at ${loc} wound site.\n` +
    `Objective: ${w.type} ${loc} measures ${L} cm x ${W} cm x ${D} cm. Drainage: ${drain}.\n` +
    `Assessment: ${w.type} ${loc} — stable.\nPlan: Dressing change, follow-up in 1 week.`;
}
function envive(w, loc, L, W, stage, drain) {
  return `*Envive Care Conference Review - V 4.0\n` +
    `Wound Status: ${w.type} to ${loc} / Measures ${L} cm x ${W} cm / Stage: ${stage ?? "N/A"}\n` +
    `Drainage present - ${drain}. Treatment: Foam dressing change daily.`;
}
function prose(w, loc, L, W, D, drain) {
  return `Pt seen for wound eval. ${w.type} ${loc} measures aprx ${L} x ${W}cm, depth ${D}cm.\n` +
    `${drain} drainage. Cleaned w/ NS, covered. Will follow up next visit.`;
}

const DRAINS = ["serous","serosanguineous, heavy","minimal serous","moderate purulent","scant"];
const generated = [];
let nextNum = 5;

for (let i = 0; i < 45; i++) {
  const w = pick(WOUNDS);
  const loc = pick(w.locations);
  const payer = pickPayer();
  const L = r2(1.5 + rand() * 6);
  const W = r2(1 + rand() * 4);
  const hasDepth = rand() > 0.22;
  const D = hasDepth ? r2(0.2 + rand() * 1.8) : null;
  const stage = w.type === "Pressure ulcer" ? pick(STAGES) : null;
  const drain = pick(DRAINS);
  const fmtRoll = rand();
  let format, note, mSpecs;
  if (fmtRoll < 0.34) {
    format = "Wound (IDT) — SOAP";
    note = soapNote(w, loc, L, W, D ?? 0.5, drain);
    mSpecs = [{ field: "measurements", value: `${L} cm x ${W} cm x ${D ?? 0.5} cm` }];
  } else if (fmtRoll < 0.67) {
    format = "Wound (SPN) — Envive";
    note = envive(w, loc, L, W, stage, drain);
    mSpecs = [{ field: "measurements", value: `${L} cm x ${W} cm` }];
    if (stage) mSpecs.push({ field: "stage", value: `Stage: ${stage}` });
  } else {
    format = "Wound Care Progress Note";
    note = prose(w, loc, L, W, D ?? 0.6, drain);
    mSpecs = [{ field: "measurements", value: `${L} x ${W}cm` }];
  }

  // routing logic
  const mcbActive = payer.code === "MCB";
  const activeWound = rand() > 0.06;
  const measComplete = D !== null;
  let route, reason, conf, checksObj, srcRel;

  if (!mcbActive) {
    route = "reject";
    reason = `Not billable under this workflow: payer is ${payer.name} (${payer.code}); Medicare Part B (MCB) is required for wound-care submission.`;
    conf = r2(0.55 + rand() * 0.2);
    checksObj = checks({ wound: { ok: activeWound, code: w.code, detail: `${w.type} — ${activeWound ? "active" : "no active dx"}` }, mcb: { ok: false, detail: `${payer.name} — MCB not on file` }, measure: { ok: measComplete, warn: !measComplete, detail: measComplete ? "complete" : "depth missing" } });
    srcRel = "conflict";
  } else if (!activeWound) {
    route = "reject";
    reason = `No active wound diagnosis on file (closest dx is resolved or out-of-scope). Eligibility gate not met; nothing to bill.`;
    conf = r2(0.5 + rand() * 0.2);
    checksObj = checks({ wound: { ok: false, code: null, detail: "No active wound dx (resolved/out-of-scope)" }, mcb: { ok: true, detail: "Effective 2024-01-01" }, measure: { ok: measComplete, warn: !measComplete, detail: measComplete ? "complete" : "depth missing" } });
    srcRel = "conflict";
  } else if (!measComplete || stage === "N/A" || rand() < 0.2) {
    route = "flag_for_review";
    reason = measComplete
      ? `Eligibility met (${w.code} active, MCB active) but cross-source confidence is borderline — extracted fields need a reviewer's confirmation before auto-submission.`
      : `Wound depth not documented (only L×W captured); measurements incomplete. Eligibility otherwise met — routed to review to confirm depth.`;
    conf = r2(0.5 + rand() * 0.25);
    checksObj = checks({ wound: { ok: true, code: w.code, detail: `${w.type} — active` }, mcb: { ok: true, detail: "Effective 2024-01-01" }, measure: { ok: measComplete, warn: !measComplete, detail: measComplete ? "complete" : "depth missing" } });
    srcRel = measComplete ? "agree" : "conflict";
  } else {
    route = "auto_accept";
    reason = `Active ${w.type.toLowerCase()} (${w.code}) with Medicare Part B; full L×W×D documented and corroborated across sources. Confidence ≥ 0.80 threshold — auto-accepted.`;
    conf = r2(0.82 + rand() * 0.16);
    checksObj = checks({ wound: { ok: true, code: w.code, detail: `${w.type} — active` }, mcb: { ok: true, detail: "Effective 2024-01-01" }, measure: { ok: true, detail: `${L} × ${W} × ${D} cm` } });
    srcRel = "agree";
  }

  const hSpecs = [
    { field: "type", value: w.type },
    { field: "location", value: loc },
    ...mSpecs,
  ];
  const name = `${pick(FIRST)}. ${pick(LAST)}`;
  generated.push({
    patient_id: `FA-${String(nextNum).padStart(3, "0")}`, id: nextNum, name,
    payer: { code: payer.code, name: payer.name, type: payer.type },
    wound: { type: w.type, stage, location: loc, L, W, D, drainage: drain, format },
    route, confidence: conf,
    field_confidence: { type: r2(0.7 + rand() * 0.28), location: r2(0.7 + rand() * 0.28), measurements: measComplete ? r2(0.78 + rand() * 0.2) : r2(0.35 + rand() * 0.2), drainage: r2(0.7 + rand() * 0.25), stage: stage ? r2(0.6 + rand() * 0.3) : 0.4 },
    reason,
    note_text: note,
    highlights: highlightsFor(note, hSpecs),
    eligibility_checks: checksObj,
    evidence_graph: evidenceGraph(`${w.type} · ${loc}`, [
      { id: "note", type: "note", label: format.split(" — ")[0], relation: srcRel, field: "measurements" },
      { id: "dx", type: "dx", label: `Dx ${w.code} (${activeWound ? "active" : "resolved"})`, relation: activeWound ? "agree" : "conflict", field: "type" },
      { id: "asmt", type: "assessment", label: "Wound assessment", relation: srcRel === "conflict" ? "agree" : "agree", field: "location" },
    ]),
  });
  nextNum++;
}

const patients = [...hero, ...generated];

// ---- aggregate counts from the patient set, scaled headline to 300 cohort ----
const n = patients.length;
const byRoute = (r) => patients.filter((p) => p.route === r).length;
const byPayer = (c) => patients.filter((p) => p.payer.code === c).length;

const TOTAL = 300;
const FETCHED = 300, RETRIED = 91, EXTRACTED = 287, RESOLVED = 300, ROUTED = 287;
const scale = EXTRACTED / n;
const sAuto = Math.round(byRoute("auto_accept") * scale);
const sFlag = Math.round(byRoute("flag_for_review") * scale);
const sReject = EXTRACTED - sAuto - sFlag;
const mcbActive = Math.round(byPayer("MCB") * scale);
const activeWound = Math.round(EXTRACTED * 0.78);
const hasMeasure = Math.round(EXTRACTED * 0.61);

const manifest = {
  run_id: RUN_ID,
  generated_at: GENERATED_AT,
  total_patients: TOTAL,
  duration_s: 287.4,
  rate_limit_hits: RETRIED,
  stages: [
    { id: "S0", label: "Ingest (PCC API)", in: TOTAL, out: FETCHED, retried: RETRIED, note: `${RETRIED} × 429 retried w/ backoff` },
    { id: "S1", label: "Resolve identity", in: FETCHED, out: RESOLVED, retried: 0, note: "patient_id ↔ id gate" },
    { id: "S2", label: "Normalize", in: RESOLVED, out: RESOLVED, retried: 0, note: "active mcb + wound dx" },
    { id: "S3", label: "Sniff format", in: RESOLVED, out: EXTRACTED, retried: 0, note: "4 note formats from text" },
    { id: "S4", label: "Extract", in: EXTRACTED, out: EXTRACTED, retried: 0, note: "regex L1 + Claude L2 + reconcile" },
    { id: "S5", label: "Route", in: EXTRACTED, out: ROUTED, retried: 0, note: "SQL eligibility view" },
    { id: "S6", label: "Publish", in: ROUTED, out: ROUTED, retried: 0, note: "export.json" },
  ],
  routes: { auto_accept: sAuto, flag_for_review: sFlag, reject: sReject },
};

const funnel = {
  total: TOTAL,
  mcb_active: mcbActive,
  active_wound: activeWound,
  has_measurements: hasMeasure,
  auto_accept: sAuto,
  flag_for_review: sFlag,
  reject: sReject,
  // sankey as a flat edge list (string node names) — SPEC §C.3 sankey[]
  sankey: [
    { source: "Medicare Part B", target: "Eligible", value: mcbActive },
    { source: "Medicare Advantage", target: "Ineligible", value: Math.round(EXTRACTED * 0.18) },
    { source: "Medicaid", target: "Ineligible", value: Math.round(EXTRACTED * 0.12) },
    { source: "Commercial HMO", target: "Ineligible", value: Math.round(EXTRACTED * 0.08) },
    { source: "Eligible", target: "Auto-accept", value: sAuto },
    { source: "Eligible", target: "Flag review", value: sFlag },
    { source: "Ineligible", target: "Reject", value: sReject },
  ],
};

const out = { generated_at: GENERATED_AT, run_id: RUN_ID, manifest, funnel, patients };

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(out, null, 2));
console.log(`Wrote ${OUT}`);
console.log(`patients=${patients.length} auto=${byRoute("auto_accept")} flag=${byRoute("flag_for_review")} reject=${byRoute("reject")}`);
// sanity: every highlight indexes back to its value
let bad = 0;
for (const p of patients) for (const h of p.highlights) {
  if (p.note_text.slice(h.start, h.end) !== h.value) bad++;
}
console.log(bad === 0 ? "highlight offsets: ALL VALID" : `highlight offsets: ${bad} BAD`);
