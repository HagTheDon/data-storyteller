# Writing the data story

## Structure: answer first, then evidence

Readers decide in the first 10 seconds whether to keep reading. Put the conclusion at the top (pyramid principle); support it below.

```markdown
# <Dataset name>: <headline as a full sentence with a number>

> **TL;DR** — 2–3 sentences: what the data is, the biggest finding, the recommended action.

## Key findings

### 1. <Claim, stated as a takeaway>
<1–3 sentences with the specific numbers, n, and comparison baseline.>
![<alt text describing the takeaway>](charts/finding_1.png)
**Why it matters:** <implication for the reader's decisions.>

### 2. …

## Data at a glance
| | |
|---|---|
| Source | … |
| One row = | … |
| Size | rows × cols |
| Time span | … |
| Key fields | … |

## Data quality & caveats
- <issue> — <impact on conclusions> — <suggested fix>

## Recommended next steps
1. <action> — <why / what it would unlock>

## Appendix
- Method notes (sampling, filters, definitions)
- Column dictionary (name, type, meaning or "unclear", notes)
```

## Writing a good finding

| Weak | Strong |
|---|---|
| "Sales vary by region." | "The West sells 2.1× more per store than the East ($84k vs $40k median, n=212 stores)." |
| "There is a correlation between X and Y." | "Customers who use feature X renew at 81% vs 52% — though heavy users may simply be more engaged." |
| "Some data is missing." | "18% of orders lack a ship date, all from the legacy system before March 2023 — delivery-time analysis covers only post-migration orders." |

Every finding needs: **a comparison** (vs. what?), **a magnitude** (how much?), **a scope** (how many rows/what share?), and **a caveat when warranted**.

## Charts

- One message per chart; the title states it.
- Sort bars by value; highlight the bar that matters, mute the rest.
- Label axes with units; start bar axes at zero.
- Prefer: bar (compare categories), line (change over time), histogram/box (distribution), scatter (relationship), heatmap (two categoricals × a measure).
- Avoid: pie charts with > 4 slices, 3D, dual axes, rainbow palettes.
- Use the automated `charts/0X_*.png` in the appendix at most — findings deserve purpose-made charts.

## Tone

Plain language, active voice, no jargon without a gloss ("skewed — a few very large values pull the average up"). Confident where the data is strong, explicit about uncertainty where it isn't. Don't pad with "interesting" or "it is worth noting".
