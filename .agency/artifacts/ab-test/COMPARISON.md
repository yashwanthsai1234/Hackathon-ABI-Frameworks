# A/B Design Test — Command Center Dashboard
_Two distinct white+teal glass directions, both self-contained HTML. Open each in a browser to compare._

| | **Variant A — "Calm Clinical Glass"** | **Variant B — "Teal Command Deck"** |
|---|---|---|
| File | `variant-A.html` | `variant-B.html` |
| Vibe | Airy, white-dominant, restrained — "Apple Health meets Stripe" | Dense, saturated, mission-control — "Bloomberg terminal in teal glass" |
| Background | White→pale-teal radial mesh, single teal accent (#0d9488) | Teal→cyan→emerald gradient header band, teal-forward |
| Hero | Floating frosted KPI cards above a horizontal S0→S6 pipeline strip | **Left→right animated pipeline graph as the hero** (glass node chips, flowing particles, 429 amber→green) |
| Data-flow visual | Gentle dots on a rail + 429 glow | **Centerpiece** — particle tracks + Sankey side-by-side |
| Density | Low (generous whitespace) | High (data-rich tiles) |
| Routing viz | Donut + funnel | Sankey (payer→eligibility→route) + tiles |
| Best for | Clean, premium, easy-to-scan first impression | Wowing judges with data-intensive motion |

## Recommendation: **B's data-flow hero + A's triage legibility (hybrid)**
The user explicitly asked for **"amazing glass visuals, amazing gradients"** and **"data flow visuals … as this is a data-intensive project"** with a **"beast"** frontend and **"perception as reality."** Variant **B** leans hardest into exactly those asks — the animated pipeline-as-hero and the richer teal gradients are the differentiator judges rarely see. Variant **A** wins on calm legibility for the triage table itself.

**Decision for the shipped React app:** the production dashboard already follows the B "command-center" structure (live React Flow pipeline + Sankey). Keep B's data-flow hero and saturated teal gradients; adopt A's hairline-bordered, generously-spaced **triage table** treatment for the row-scanning surface (where calm legibility beats density). This is the best of both — beast on the hero, clarity on the worklist.

> Both are concept mockups. The real, functional dashboard is the React app in `frontend/` (reads live `export.json`).
