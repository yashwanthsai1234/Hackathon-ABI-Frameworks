# 🩹 woundpipe — Architecture, Explained From Scratch

> A story-driven, zero-prior-knowledge walkthrough of the whole system: the domain,
> the data, every pipeline stage, the concurrency model, edge cases, and an honest
> note on incremental sync. Read top to bottom; nothing assumed.

---

## Table of contents
1. [The world this lives in (no jargon left behind)](#part-1--the-world-this-lives-in-no-jargon-left-behind)
2. [The 30,000-foot view](#part-2--the-30000-foot-view)
3. [Follow one patient through the whole line](#part-3--follow-one-patient-through-the-whole-line)
4. [How the data is stored (tables & relationships)](#part-4--how-the-data-is-stored)
5. [Deep dive: concurrency & the "permit during sleep" rule](#part-5--deep-dive-concurrency--the-permit-during-sleep-rule)
6. [Every edge case, and how it's handled](#part-6--every-edge-case-and-how-its-handled)
7. [How the biller sees it (the dashboard)](#part-7--how-the-biller-sees-it-the-dashboard)
8. [Incremental sync: 0→N vs delta (the honest answer)](#part-8--incremental-sync-0n-vs-delta-the-honest-answer)
9. [The one-paragraph version](#the-one-paragraph-version)

---

## Part 1 — The world this lives in (no jargon left behind)

Imagine a **nursing home** (industry term: *SNF* — Skilled Nursing Facility). Elderly residents,
many bedridden, develop **wounds** — most commonly **pressure ulcers** (bedsores). Treating those
wounds costs money, and the home wants to be **reimbursed** by the patient's insurance.

Meet **Dana**, a **medical biller** (job title: *RCM specialist* — Revenue Cycle Management). Dana is
*not* a nurse. Her job: read each patient's chart and decide **"can we bill insurance for this wound,
or not?"**

| TERM | WHAT IT ACTUALLY MEANS (plain English) |
|---|---|
| **PCC** | PointClickCare — the software a nursing home stores all patient records in. "Google Docs for nursing homes." We pull from a mock copy of its API. |
| **Payer** | Whoever pays the bill (the insurance). Each patient has a `payer_code`: `MCB`=Medicare Part B · `MCA`=Medicare Part A · `MCD`=Medicaid · `HMO`=managed care. |
| **Medicare** | US govt health insurance for 65+. **Part A** = hospital/inpatient. **Part B** = OUTPATIENT (doctor visits, wound-care supplies). **Wound care is billed under Part B**, so a patient is only billable here with active **Part B** (`MCB`). |
| **ICD-10** | The universal "barcode" for a diagnosis. `L89.143` = "Pressure ulcer, right hip, stage 3." The code encodes wound TYPE, LOCATION, STAGE. |
| **Wound staging** | How bad a pressure ulcer is: Stage 1 (mild) → 4 (severe), plus "unstageable" and "N/A". |
| **Drainage** | Fluid leaking from the wound: `none` / `light` / `moderate` / `heavy`. |
| **Measurements** | Wound size in cm: Length × Width × Depth. Medicare **requires** these documented to pay. |

> **Why this is hard, in one sentence:** Medicare *denies* a wound claim if documentation is incomplete
> — and the #1 missing thing is the **depth** measurement. A denied claim costs ~$25 to rework and ~65%
> never get reworked. We're automating Dana's eyeballs.

The mission of `woundpipe`: read every patient's messy records, figure out the wound facts, and tell
Dana — per patient — one of three things:

```
   ✅ auto_accept     "All facts are clear and consistent. Safe to bill."
   ⚠️  flag_for_review "Something's ambiguous or missing. Dana, take a look."
   ⛔ reject          "Not billable — wrong insurance, no wound, or unreadable."
```

---

## Part 2 — The 30,000-foot view

Data flows **left to right** through 7 stations:

```
        ┌──────────────────────────────────────────────────────────────────────────────┐
        │                          THE RUN MANIFEST (a scoreboard)                       │
        │     counts every call, every 429, every retry — feeds the dashboard's numbers  │
        └──────────────────────────────────────────────────────────────────────────────┘
              ▲          ▲          ▲          ▲          ▲          ▲          ▲
   ┌────────┐ │          │          │          │          │          │          │
   │  PCC   │ │   S0     │   S1     │   S2     │   S3     │   S4     │   S5     │   S6
   │  API   │─┼─ INGEST ─→ RESOLVE ─→NORMALIZE─→ SNIFF  ─→ EXTRACT ─→ ROUTE  ─→ PUBLISH ─→ 💻 UI
   │(flaky) │ │  fetch    two-ID    flag MCB   detect    pull wound  decide    export.json  React
   └────────┘ │  +retry   gate      + wounds   format    facts       accept/   (1 file)     dashboard
              │                                from text  (regex+AI)  flag/      │           white+teal
              │                                                       reject     │           glass
              ▼                                                                   ▼
        ┌──────────────────────────────────────────────────────────────┐    the biller
        │   🗄️  SQLite database  (one local file: woundpipe.db)         │    sees decisions
        │   raw tables · extracted wounds · live "eligibility" views     │    at a glance
        └──────────────────────────────────────────────────────────────┘
```

It's a **factory assembly line** with 7 stations. A patient's data is the raw material; it enters at S0
and exits at S6 as a finished **billing decision**. The database is the conveyor belt — every station
reads from and writes to it, so if the factory loses power, we restart exactly where we stopped.

---

## Part 3 — Follow one patient through the whole line

Protagonist: **Agnes Dunbar, patient `FA-001`**.

### 🏁 S0 — INGEST: *"Go get the data (the API fights back)"*

The PCC API is deliberately cruel: **every request has a 30% chance of `429 "Too Many Requests"`.**
Fetching everything = ~1,200 requests, so ~360 fail. A naive program crashes or hangs.

```
   our code                         the flaky API
   GET /pcc/patients?facility_id=101
        │  ──────────────────────────────▶  ❌ 429 "slow down!" (Retry-After: 3s)
        │  😌 wait 3 seconds, try again
        │  ──────────────────────────────▶  ❌ 429 again
        │  😌 wait with backoff + jitter
        │  ──────────────────────────────▶  ✅ 200 OK  [Agnes, id=1, payer=MCB, ...]
        ▼
   write to database + tick the checkpoint:  "patients:101 = DONE"
```

```
  ┌─ RETRY POLICY ────────────────────┐   ┌─ CHECKPOINT (fetch_log) ──────────┐
  │ 429  → wait the time it tells us   │   │ Every call is a row:               │
  │ 500  → wait exponentially+jitter   │   │   patients:101  → done             │
  │ 422  → STOP (that's OUR bug)       │   │   diagnoses:FA-001 → done          │
  │ hold a "permit" while sleeping so  │   │   notes:1       → pending          │
  │ we never stampede the server       │   │ Crash at call 700? Restart only    │
  │ (max 8 requests in flight at once) │   │ runs the 500 not-yet-done ones.    │
  └────────────────────────────────────┘   └────────────────────────────────────┘
```

> **Real numbers from the actual run:** 1,693 calls fired, **485 got 429'd**, 490 retries, **all 1,200
> tasks completed**, 3 transient blips auto-recovered. 300 patients in 280 seconds.

### 🔑 S1 — RESOLVE: *"The two-names trap"*

**Every patient has TWO IDs**, used inconsistently:

```
                     Agnes Dunbar
                    ┌─────────────┐
        "FA-001" ───┤  ONE PERSON ├─── 1  (integer)
        (string)    └─────────────┘
            │                            │
            ▼                            ▼
    use FA-001 to ask for:        use 1 to ask for:
      • /diagnoses                   • /notes
      • /coverage                    • /assessments

    ⚠️ Ask /notes with "FA-001"  →  💥 422 error (wrong key type)
```

S1 is a **hard gate**: fetch the patient list, build the `FA-001 ⇄ 1` map, and **refuse to continue**
until the map is complete. Then fan out per-patient requests with the correct key. Wrong-key errors
become *structurally impossible*.

### 🧹 S2 — NORMALIZE: *"Tidy up + answer two yes/no questions"*

```
   Q1: Does Agnes have ACTIVE Medicare Part B?
       payer_code == "MCB"  AND  effective_to IS NULL (NULL = still active)
       Agnes: ✅ yes

   Q2: Does she have an ACTIVE wound diagnosis?
       an active wound ICD-10 code (L89*, E11.62*, ...)
       Agnes: ✅ L89.143 "Stage 3 Pressure Ulcer, Right hip" (active)
```

```
   ⚠️ EDGE CASE — the docs lied:
   Docs said check "payer_type" == "Medicare B". The REAL data has
   payer_type = just "Medicare". Trusting the docs → everyone looks ineligible.
   → We key off payer_CODE ("MCB"). Reality wins over docs.
```

### 👃 S3 — SNIFF: *"What kind of note is this?"*

Notes come in **four formats**, and the note's `type` label does **NOT** tell you its format. We sniff
from the actual **text**:

```
  TEXT STARTS WITH...                          → FORMAT       → how hard?
  "*Envive Care Conference Review..."          → ENVIVE       😬 prose, often 2D only
  "Subjective:... Objective:..."               → SOAP         🙂 structured, full L×W×D
  "...measures aprx 5.9 x 4.5cm, depth 1.8cm"  → PROSE        😬 shorthand, multi-wound
  "Measures 2.9 cm x 2.8 cm / Stage: Stage 3"  → LABELED-SPN  🙂 slash-delimited
  {nested JSON with a narrative inside}         → ASSESSMENT   😐 unwrap, then re-sniff
```

Agnes's note is **ENVIVE**: length and width, but **no depth**. Hold that thought.

### 🧬 S4 — EXTRACT: *"Pull the wound facts out of the mess"* — the heart of the system

Three lanes working together:

```
   the note text
        │
        ├──────────────▶ LANE 1: REGEX (the rule-follower) ──────┐
        │                Owns NUMBERS. Returns a real substring   │
        │                from the text, or nothing. NEVER invents.│
        │                                                          ▼
        ├──────────────▶ LANE 2: CLAUDE AI (the comprehender) ─▶ LANE 3: RECONCILER
        │                understands messy prose, picks the         "do the sources agree?
        │                primary wound — behind a hallucination     compute a confidence."
        │                GATE                                       │
        │                                                          ▼
        └──────────────▶ the active ICD-10 diagnosis ───────────▶  one trustworthy wound
                          (a free 2nd opinion)                       + a confidence score
```

**Why three lanes?** Numbers and language fail differently:

```
  REGEX great at:  4.3 cm x 1.8 cm x 0.3 cm   (exact, literal)
  REGEX bad at:    "primary is the sacral one" (needs comprehension)
  AI great at:     comprehension, primary-wound selection
  AI DANGEROUS at: numbers — it will HALLUCINATE a plausible depth never written.

  So: regex OWNS numbers. AI is NEVER trusted for a measurement unless that
  exact number appears verbatim in the note (the GATE).
```

**The verbatim-span gate** (the single most important safety rule):

```
   AI says: "depth_cm = 0.5,  I saw it at: 'depth 0.5cm'"
        ▼
   Is "depth 0.5cm" ACTUALLY in the note?  ──NO──▶ 🗑️ DROP IT. depth = null.
        │                                            (better no answer than a fabricated one)
       YES ──▶ keep it ✓
```

This is the motto in code: **"flag, don't hallucinate."**

**Confidence is NOT the AI's opinion.** AI models are overconfident. We score confidence by **agreement
across independent sources** — the *corroboration graph*:

```
        📋 DIAGNOSIS  "L89.143: pressure ulcer, right hip, stage 3"  ──agree✓──┐
        📝 NOTE       "Pressure Ulcer to Right hip ... Stage 3"      ──agree✓──┼──▶ 🩹 WOUND
        📊 ASSESSMENT "Pressure Ulcer to Right hip ... Stage 3"      ──agree✓──┘   right hip, stage 3

        all three point to the SAME wound  → HIGH confidence (real consensus)
        one says "hip" another "heel"      → CONFLICT → low confidence → FLAG
```

> The system trusts an extraction the same way it asks *you* to trust it: not by self-rating, but by
> independent witnesses corroborating. Same logic a detective uses.

### ⚖️ S5 — ROUTE: *"Make the call"*

Strict checklist, **top to bottom, first match wins**:

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  Q1: Active Medicare Part B?         NO ─────────────────▶ ⛔ REJECT  │
   │  Q2: Any wound at all?               NO ─────────────────▶ ⛔ REJECT  │
   │  Q3: Could we read any measurement?  NO ─────────────────▶ ⛔ REJECT  │
   │  Q4: Is EVERYTHING perfect?                                          │
   │        • all fields present (incl. DEPTH)?                           │
   │        • sources agree (no conflict)?                                │
   │        • confidence ≥ 0.80?                                          │
   │        • diagnosis corroborates?                                     │
   │        ALL YES ──────────────────────────────────────────▶ ✅ AUTO   │
   │        anything short of perfect ────────────────────────▶ ⚠️ FLAG   │
   └─────────────────────────────────────────────────────────────────────┘

   Agnes: MCB ✓, wound ✓, measurements readable ✓ ... but DEPTH is missing.
          → Q4 fails  →  ⚠️ FLAG: "Missing depth measurement"
```

> **Why the FLAG zone is deliberately huge** (*Chow's rule*): the two mistakes aren't equal.
> Auto-accepting a bad claim = denial, clawback, audit (expensive). Flagging a good claim = Dana spends
> 30 seconds (cheap). When one error is far costlier, the rational policy is to **abstain often** — only
> auto-accept when *everything* lines up.

Across 300 patients: **15 auto-accept · 92 flag · 196 reject** (most rejects = not on Medicare Part B).

### 📤 S6 — PUBLISH: *"Freeze it into one file"*

Turns the result into a single `export.json`. The dashboard reads *only* that file — no live database,
no server at demo time. A static file can't crash mid-presentation.

---

## Part 4 — How the data is stored

One SQLite file. The **two-identity trap** is baked into the schema:

```
                          ┌────────────────────────┐
                          │      pcc_patient        │
                          │  patient_id "FA-001" PK │◀── string key
                          │  id          1       UK │◀── integer key   (BOTH live here)
                          └────────────────────────┘
            string key  ┌──────────┴───────────┐  integer key
        ┌───────────────┴──┐                ┌───┴──────────────────┐
        ▼                  ▼                ▼                      ▼
  ┌────────────┐   ┌────────────┐   ┌──────────────┐    ┌──────────────────┐
  │pcc_diagnosis│   │pcc_coverage│   │progress_note │    │ pcc_assessment   │
  │ ICD-10 codes│   │ MCB? active?│   │ free-text    │    │ semi-structured  │
  └─────┬──────┘   └────────────┘   └──────┬───────┘    └────────┬─────────┘
        │  all three feed extraction        │                     │
        └───────────────┬───────────────────┴─────────────────────┘
                        ▼
              ┌──────────────────────┐        ┌──────────────────────────┐
              │  wound_extraction    │───────▶│  wound_field_evidence    │
              │  one row per source's │        │  per-field char offsets  │
              │  view of the wound +  │        │  (so the UI can HIGHLIGHT│
              │  a confidence score   │        │   "2.9 cm" inside the    │
              └──────────┬───────────┘        │   original note text)    │
                         ▼                     └──────────────────────────┘
       ┌─────────────────────────────────────────────────────────┐
       │  v_patient_eligibility   (a VIEW = a live saved query)   │
       │  one row per patient: wound facts + route + reason       │
       │  Recomputed fresh every read, so it's never stale.       │
       └─────────────────────────────────────────────────────────┘
```

> **A "view" is the cleverest part of the data design.** `v_patient_eligibility` isn't a table you fill
> in — it's a *saved question* the database answers live. The routing logic lives **in the database as
> SQL**, computed on every read, so it can never drift out of sync with the data. (A parallel Python
> version exists purely as a *test* that the two always agree.)

---

## Part 5 — Deep dive: concurrency & the "permit during sleep" rule

A semaphore is a **bouncer with 8 wristbands** 🎟️. The PCC server is the overcrowded club (that's why
it throws 429). A request must: get a wristband → go in → if 429'd, step outside and wait, then retry →
return the wristband when done.

The whole question: **while a 429'd request waits to retry, does it keep its wristband or hand it back?**

### ❌ THE BUG: hand the wristband back while sleeping

```
 TIME →     t0          t1 (429!)        t2..t3 (sleeping)        t4 (WAKE)
 R1 🎟️▶server❌  →  💤(gave back 🎟️)  ─────────────────────────▶ R1 needs 🎟️ ▶❌
 R2 🎟️▶server❌  →  💤(gave back 🎟️)  ─────────────────────────▶ R2 needs 🎟️ ▶❌
 ...R8                                                            ...R8
       MEANWHILE the 8 freed wristbands get grabbed by NEW work:
 R9..R16 🎟️▶server   (waiting tasks rush into the "idle" slots)

 LOAD ON SERVER:
   requests │              ╱╲          ← R9..R16 already pounding
            │   ████      ╱  ╲  ████    ← then R1..R8 ALL wake at t4 and pile on
          8 │───████────╱──────╲████───   = a SPIKE to 16+ at once 💥
            └───────────────────────────▶ time
                t0      sleep      t4 STAMPEDE
```

> **Why the spike is guaranteed:** freeing wristbands lets *new* requests flood the slots, AND all
> sleepers wake at ~the same time and re-rush together. The semaphore was supposed to cap concurrency at
> 8, but sleeping requests escaped the cap → the real ceiling became *unbounded*. You built a rate
> limiter that doesn't limit during exactly the moments the server is begging you to slow down.

### ✅ THE FIX: keep the wristband while sleeping

```
 TIME →     t0          t1 (429!)        t2..t3 (sleeping)        t4 (WAKE)
 R1 🎟️▶server❌  →  💤(keeps 🎟️)  ──────────────────────────────▶ 🎟️▶server ✅
 ...R8                                                            ...
       NEW work R9..R16?  →  🚪 NO wristband → they WAIT politely

 LOAD ON SERVER:
   requests │
            │   ████              ████        ← never more than 8, ever
          8 │───████────────────████───────     (the line just moves slower during
            │   ████              ████            backoff — exactly what we want)
            └───────────────────────────▶ time
                t0    quiet backoff    t4   steady, no spike
```

The accounting rule:

```
   alive  =  (requests actively hitting)  +  (requests backing off to retry)
              └──────── the BUG ignores this ────────┘  the FIX counts BOTH
   Cap "alive" at 8  →  the server never sees a burst, no matter how many 429s.
```

In code it's **one structural choice** — the retry loop lives *inside* the permit's lifetime:

```python
def run_task(...):
    with semaphore:              # 🎟️ acquire wristband
        fetch_one(...)           #    ...the RETRY LOOP lives INSIDE here
        #   ├─ try → 429
        #   ├─ sleep(retry_after)   ← still inside `with semaphore:` = STILL HOLDING 🎟️
        #   └─ try again → 200
    #  🎟️ returned only when the task fully succeeds or gives up
```

> **The deeper lesson:** a semaphore doesn't limit "active work" — it limits "whatever holds a permit."
> A unit of work should hold its slot for its **entire lifetime — including the waiting** — not just the
> busy parts.

---

## Part 6 — Every edge case, and how it's handled

```
  EDGE CASE                          HOW woundpipe HANDLES IT
  ─────────────────────────────────────────────────────────────────────────────
  🔁 API returns 429 (30% of time)   Retry honoring Retry-After; backoff+jitter;
                                      max 8 in flight; checkpoint each call.
  💀 Crash halfway through ingest     fetch_log remembers what's done; restart
                                      runs ONLY the unfinished calls.
  🆔 Two different patient IDs        Hard gate resolves FA-001 ⇄ 1 first; each
                                      endpoint queried with its correct key.
  🏷️ note_type lies about format      Format detected from the TEXT, not the label.
  📦 "Structured" assessment that's   Unwrap the nested JSON to its narrative
     actually free text inside JSON    string, then run the text extractors on it.
  📏 Missing depth (Envive 2D notes)  depth = null (never faked) → FLAG
                                      "Missing depth measurement". (← Agnes)
  ➿ Multi-wound note (hip + heel)     Extract BOTH; pick the PRIMARY (diagnosis-
                                      matched → most-documented → largest).
                                      Secondaries kept but not billed. Ambiguous → FLAG.
  🏚️ "Stage: N/A" / unstageable        Recorded as a real state, not blanked; a
                                      pressure ulcer with no usable stage → FLAG.
  ✍️ Typos: "Diabetic diabetic" "aprx" Duplicate-word collapser + abbreviation strip
                                      before parsing.
  📐 Impossible numbers               depth>length flagged as anatomically suspect;
                                      >50cm dropped.
  🤖 AI hallucinates a measurement    Verbatim-span gate: not literally in the note → discarded.
  🤝 Sources disagree (hip vs heel)   Corroboration finds the conflict edge → confidence
                                      drops → FLAG "sources disagree".
  🚫 Patient not on Medicare Part B    REJECT immediately ("No active Part B").
  🔌 No AI API key available           The regex lane is a complete floor — the whole
                                      pipeline runs and routes with zero AI calls.
  📭 Wound dx has no measurement       A separate attribute-only extractor still makes
     (just type/location/stage)        the diagnosis an evidence node. (A real bug we fixed.)
```

---

## Part 7 — How the biller sees it (the dashboard)

`export.json` → a React app (**white + teal glass**), four screens:

```
   ┌─ COMMAND CENTER ─────────────────────────────────────────────────┐
   │  300 patients · 62% MCB · 15 ✅ / 92 ⚠️ / 196 ⛔                  │
   │  [live pipeline graph: API→Fetch→Sniff→Extract→DB→Route→outcomes]│
   │  [Sankey: payer → eligible → routed]    ← shows "only MCB flows"  │
   └──────────────────────────────────────────────────────────────────┘
   ┌─ TRIAGE QUEUE ───────────────────────────────────────────────────┐
   │  FA-001 Agnes Dunbar  PU/right hip  2.9×2.8×—  ⚠️ FLAG  conf▓▓▓▓░ │
   │     reason: "Missing depth measurement"                           │
   │  ...sortable, filterable, color + ICON (never color alone)        │
   └──────────────────────────────────────────────────────────────────┘
   ┌─ PATIENT DETAIL (click a row) ───────────────────────────────────┐
   │  Original note with the extracted facts HIGHLIGHTED in place:     │
   │    "...Pressure Ulcer to [Right hip] / Measures [2.9 cm x 2.8 cm] │
   │       / Stage: [Stage 3] ... [heavy]"                             │
   │  ✓ Active wound   ✓ MCB active   ✗ Measurements (depth missing)   │
   │  Evidence graph:  📋dx ─✓─┐                                        │
   │                   📝note ─✓─┼─▶ 🩹wound   (why we trust it)        │
   │                   📊assess ─✓─┘                                    │
   └──────────────────────────────────────────────────────────────────┘
   ┌─ PIPELINE FLOW ──────────────────────────────────────────────────┐
   │  Per-stage counts; the 429-retry rendered amber→green;           │
   │  funnel: 300 → 145 MCB → 104 with wounds → 15 auto-accept         │
   └──────────────────────────────────────────────────────────────────┘
```

The **highlighting inside the original note** is the trust moment — Dana sees the exact phrase each fact
came from. The **evidence graph** answers her unspoken question: *"why should I believe this?"*

---

## Part 8 — Incremental sync: 0→N vs delta (the honest answer)

> Honest status: we built rock-solid **full-fetch + within-run resume**. True day-2 incremental
> ("grab only the new 100") is **designed and half-wired, not finished**.

### Three possible modes

```
  MODE                         WHAT IT DOES                          STATUS
  A. First run (0→N)           fetch everything                      ✅ works
  B. Re-run, same DB           skip already-done tasks (resume)      ✅ works (but see ⚠️)
  C. Incremental (--since)      fetch only records changed since X    🟡 designed, NOT closed
```

### What `fetch_log` does (the checkpoint)

`seed` inserts new tasks as `pending` but **leaves existing rows untouched** (`ON CONFLICT DO NOTHING`);
the fetcher only runs `status != 'done'`:

```
   fetch_log (the work ledger)
   ┌──────────────────────┬─────────┐
   │ task_id              │ status  │
   ├──────────────────────┼─────────┤
   │ patients:101         │ done ✅ │ ← never re-run once done
   │ notes:2              │ pending │ ← only THIS gets fetched on re-run
   └──────────────────────┴─────────┘
```

Perfect for **crash-resume within one run**. But watch the day-2 scenario.

### 🎬 Day 1 = 300, Day 2 = 400

```
   Day 1 — first run
     ingest → fetch patients:101/102/103 → discover 300 → fan out 1,200 calls → all 'done'
     DB: 300 patients.  fetch_log: 1,203 rows, all 'done'.

   Day 2 — add 100 patients, run ingest again
     seed(patients:101/102/103) → already 'done' → DO NOTHING ───────┐
     select_open() → []  → phase-1 fetch does NOTHING               │ ⚠️ THE TRAP
     resolve_gate → ✅ (lists 'done', 300 exist)                     │ never RE-FETCH the
     plan_fanout() reads pcc_patient → still only 300 from Day 1     │ list → never SEE the
        → their tasks 'done' → DO NOTHING                            │ 100 new patients ❌
     RESULT: a complete NO-OP. The 100 new patients are invisible. ──┘
```

> **The checkpoint that makes resume safe is the same thing that blinds incremental sync.** "Skip what's
> already done" is right *during* a run, but *across* runs it means "patient-list discovery is done
> forever," so we never re-knock to find new patients. *Resume-an-interrupted-run* and *discover-what-
> changed* pull in opposite directions; `fetch_log` currently serves only the first.

### How you actually load the 400 today

```
   Start a FRESH fetch ledger (new run / clear fetch_log) → ingest
     → re-fetches all 400 (0→N again, ~1,600 calls)
     → BUT the UPSERT is idempotent + monotonic-guarded, so the 300 unchanged
       patients are cheap no-op writes (identical data → skipped).
   ✅ Correct result (400), ❌ but you paid to re-fetch the 300 you had.
```

**So today the answer is: "from 0" (a full idempotent re-fetch)** — correct and safe, but not yet
"only the remaining 100."

### What `--since` is supposed to do — and what's missing

The API supports a `since` filter, and the parameter is plumbed through (`params_for` adds `since=...`
for `/patients`, `/notes`, `/assessments`). The intended design:

```
   sync_state (watermark table — EXISTS, but nothing reads/writes it yet 🟡)
   ┌──────────────┬─────────────────────────┐
   │ scope        │ watermark               │
   │ patients:101 │ 2026-06-28T17:28:00     │ ← "last time I synced up to here"
   └──────────────┴─────────────────────────┘

   Day-2 incremental flow (intended):
     1. read watermark W = yesterday's finish time
     2. GET /pcc/patients?facility_id=101&since=W  → ONLY the 100 new/changed
     3. fan out dx/coverage/notes/assessments for just those 100
     4. UPSERT (unchanged = no-op, changed = update in place)
     5. re-extract + re-route ONLY the touched patients
     6. advance watermark W = now   (only if the run was clean)
```

```
   ✅ sync_state table exists
   ✅ --since parameter flows to the API query
   ✅ idempotent UPSERT with monotonic guard (re-fetch always safe)
   ✅ since-filter works on a FRESH ledger
   🟡 MISSING: discovery tasks (patients:*) re-run each sync instead of 'done' forever
   🟡 MISSING: reading + advancing the sync_state watermark automatically
   🟡 MISSING: re-opening changed tasks so select_open picks them up
```

The spec listed incremental `since` sync explicitly as a **bonus criterion**; the build prioritized a
bulletproof full run first (the MVP cut-line).

### The honest one-liner

> **Today:** every full ingest is a clean, idempotent **0→N** fetch (re-running is always *safe* —
> unchanged rows are no-ops — but not yet *cheap*). True "fetch only the new 100 since yesterday" is
> **designed, with the table and `--since` plumbing in place, but the watermark loop isn't wired**, so it
> doesn't happen automatically. Closing it is ~1 hour of work.

---

## The one-paragraph version

> `woundpipe` pulls 300 patients' messy records from a deliberately-flaky nursing-home EHR (surviving a
> 30% failure rate with patient retries and checkpoints), untangles a two-identity-per-patient trap,
> reads wound facts out of four inconsistent note formats using **regex for numbers** and **AI for
> prose** (with a hard gate that forbids the AI from inventing measurements), scores its confidence by
> whether the **diagnosis, the note, and the assessment independently agree**, and routes every patient
> to **auto-accept / flag / reject** — heavily biased toward *flagging* anything doubtful because a wrong
> bill is far costlier than a second look. It stores everything in one queryable SQLite file, exposes the
> decision as a live SQL view, and surfaces it to the biller as a white-and-teal glass dashboard that
> highlights the source text and shows *why* each decision was made. **Flag, don't hallucinate.**
