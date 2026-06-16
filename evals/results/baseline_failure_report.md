# Baseline Extraction Failure Report

**Model:** `gpt-4o-mini` (overrides repo default of `gpt-3.5-turbo`)
**Dataset:** [`evals/golden_reviews.json`](../golden_reviews.json) — 50 hand-labeled reviews across 8 recipes
**Raw output:** [`baseline_extraction_report.json`](baseline_extraction_report.json)
**Production code modified:** none

## Headline metrics

| Metric | Score | Detail |
|---|---:|---|
| Extraction success rate | **100%** | 50 / 50 returned a valid `ModificationObject` |
| Edit-count parity | **76%** | 38 / 50 reviews matched expected edit count |
| Modification-type accuracy | **94%** | 47 / 50 had `modification_type` in the expected set |
| Groundedness (per predicted edit) | **100%** | 54 / 54 `find` strings present in recipe |
| Fully-grounded reviews | **100%** | every review's edits all resolved to recipe text |

## Distribution of failures (13 unique reviews failed at least one check)

```
collapsed_to_one_edit   : 9  GR-042, GR-043, GR-044, GR-045, GR-046, GR-047, GR-048, GR-049, GR-050
over_extraction         : 3  GR-011, GR-016, GR-040
wrong_modification_type : 3  GR-011, GR-013, GR-041
```

GR-011 fires in two buckets (over-extraction + wrong type).

---

## Failures grouped by root cause

### R1 — Single-`modification_type` schema + single-theme prompt forces multi-tweak collapse
**Affects:** 9 reviews (all multi-mod reviews except GR-041) — 18% of dataset, **90% of multi-mod reviews**

The schema's scalar `modification_type: Literal[...]` field plus the prompt's instruction to pick *one* category pushes gpt-4o-mini to choose a single "theme" per review and emit one edit, even when the review explicitly enumerates 3-4 distinct tweaks.

Representative cases:

| ID | Review | Expected | Predicted |
|---|---|---|---|
| GR-047 | "Stuffed the cavity with a halved lemon and a few sprigs of fresh rosemary, brushed with olive oil instead of butter, and roasted at 425 instead of 375" | 4 edits (2 additions + sub + temp) | 1 addition merging lemon and rosemary into a single `add` string |
| GR-048 | "Halved the sugar, doubled the cinnamon, baked at 325 for an extra 15 minutes" | 4 edits (2 quantity + temp + time) | 1 edit (only the sugar halving) |
| GR-050 | "Added egg yolk, replaced white sugar with another cup of brown sugar, used half chips half chunks" | 3 edits | 1 edit with payload `"0 cup white sugar, 2 cups packed brown sugar"` — LLM tried to encode two intents into one nonsensical replacement string |

GR-050 is the most diagnostic: when the model wants to express multiple changes but the schema only allows one edit, it produces malformed payloads that would also fail at Stage 2 (Stage 2 does `str.replace("1 cup white sugar", "0 cup white sugar, 2 cups packed brown sugar")` — syntactically a substitution, semantically broken).

GR-041 is the multi-mod exception: two quantity adjustments to *separate* ingredient lines were both emitted. The model handles parallel edits of the same category better than mixed categories.

### R2 — LLM domain reasoning fabricates unrequested edits
**Affects:** 3 reviews — a failure mode **not predicted in Phase 1c**

The model exceeds the user's stated intent and infers cascading or "completing" edits a reviewer didn't ask for. This is a different shape of failure than hallucination — the edits are domain-plausible, just not what the review described.

| ID | Review | Expected | Spurious extra edit |
|---|---|---|---|
| GR-011 | "No kidney beans for me, I prefer just the black beans" | 1 removal (kidney beans) | Also doubled the black beans — interpreted "I prefer just the black beans" as a quantity boost |
| GR-016 | "My partner is allergic to garlic so I left it out entirely" | 1 ingredient removal | Also removed the instruction step `"Add garlic and cook for 1 minute"` — consistency cascade |
| GR-040 | "Roasted at 425 instead of 375. Crispier skin, juicier inside." | 1 temp change | Also reduced cook time 1h15→1h to "compensate" for higher temp |

GR-016 is actually defensible chef-think (if you remove an ingredient you should remove the step that uses it). But it represents domain inference the user did not authorize. There is no representation in the schema for "consistency cascade" vs "user-intended edit," so any product-level decision about whether to surface these has to happen outside the schema.

### R3 — Modification-type taxonomy maps poorly to nuanced reviews
**Affects:** 3 reviews (GR-011, GR-013, GR-041)

The categorical taxonomy (`ingredient_substitution | quantity_adjustment | technique_change | addition | removal`) doesn't survive natural language framing where the same change can be plausibly read multiple ways.

| ID | Review | Expected type | Predicted | Likely cause |
|---|---|---|---|---|
| GR-013 | "Skipped the Dijon mustard, my husband doesn't like it. Just as good without." | removal | ingredient_substitution | "Just as good without" reads as "substituted with nothing" |
| GR-041 | "Halved the white sugar and used 1.5 cups of brown sugar instead" | quantity_adjustment (×2) | ingredient_substitution | The phrase "used X instead" triggers substitution semantics even though both changes are quantity |
| GR-011 | "No kidney beans, I prefer just the black beans" | removal | ingredient_substitution | Driven by the R2 over-extraction — once a second edit was invented, the top-level type became sub |

R3 is largely a downstream symptom of the same prompt+schema design that drives R1. The LLM has to compress potentially mixed intent into one scalar field.

---

## What surprised vs. what confirmed Phase 1c predictions

**Confirmed (high signal):**
- **#1 multi-tweak collapse** is the dominant failure mode at exactly the rate the schema would predict. 9 of 10 multi-mod reviews collapsed.

**Surprised — failure mode predicted to fire, didn't:**
- **#7 ungrounded `find` strings**: 0/54 predicted edits had ungrounded `find`. gpt-4o-mini reuses recipe text verbatim with high fidelity. This pushes the *grounding gate* fix lower in priority — at this dataset scale, the LLM isn't paraphrasing the recipe.

  **Caveat:** the recipes here have clean, machine-formatted ingredient lines. Real AllRecipes recipes have more variation. Also, even with grounded `find`, Stage 2 can still fail: GR-050 has a grounded `find` ("1 cup white sugar") but a malformed `replace` value ("0 cup white sugar, 2 cups packed brown sugar") that Stage 2's `str.replace` will faithfully execute, producing a broken recipe.

**New failure mode discovered — not in Phase 1c:**
- **R2: Domain over-reach**. The LLM helpfully infers cascading edits. This wasn't on the radar. Implication: simply asking the model to "extract all modifications" without anchoring strictly to the review's literal claims could *worsen* R2.

---

## Implications for the priority order

Revised top-3 fix targets (informed by empirical data, not just code reading):

1. **Schema + prompt redesign to enumerate multiple modifications per review.** Single biggest correctness lever — would lift 9 of 13 failed reviews.
2. **Stage 2 grounding+sanity gate.** Not because `find` is ungrounded (it isn't), but because `replace`/`add` payloads can encode multi-intent compromises (GR-050) that produce structurally valid but semantically broken edits.
3. **Anchor extraction to literal review claims.** Counter to R2. Add an instruction like "only extract changes the reviewer explicitly states; do not infer consistency edits."

Lower-priority than Phase 1c suggested:
- Pure groundedness validation gate (because grounding rate is already 100% on this data).
