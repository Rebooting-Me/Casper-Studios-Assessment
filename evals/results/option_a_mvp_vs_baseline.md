# Option A MVP — Before / After

**Change scope:** ~85 LOC across 4 production files. Allowed the LLM to return `List[ModificationObject]` per review; activated the already-existing-but-dead `RecipeModifier.apply_modifications_batch`; replaced the hardcoded `[modification_applied]` wrap in `EnhancedRecipeGenerator` with a list built from the per-modification change records.

No schema migration, no new methods, no other failure modes touched.

## Headline metrics

| Metric | Baseline | Option A MVP | Δ |
|---|---:|---:|---:|
| Extraction success rate | 100% | 100% | 0 |
| **Edit-count parity** | **76%** | **90%** | **+14 pp** |
| Modification-type accuracy | 94% | 96% | +2 pp |
| Groundedness per edit | 100% | 97.22% | −2.78 pp |
| Fully-grounded reviews | 100% | 96% | −4 pp |
| Predicted edits emitted | 54 | 72 | +18 |

Edit-count parity landed at the upper end of my 84-94% projection range. Type accuracy nudged up because each modification now carries its own scalar type. Groundedness regressed slightly — predicted in Phase 5 as a risk of giving the LLM "more rope."

## Review-level outcomes (50 reviews total)

```
RECOVERED   9   GR-041, GR-042, GR-043, GR-044, GR-045, GR-046, GR-047, GR-049, GR-050
REGRESSED   2   GR-007 (ungrounded), GR-014 (over-extraction)
SHIFTED     2   GR-040, GR-048 (still failing but differently)
UNCHANGED   3   GR-011, GR-013, GR-016 (R2/R3 failures, untouched by R1 fix)
```

### R1 (multi-tweak collapse): fully resolved at this scale
All 9 collapsed multi-mod reviews from the baseline now extract the expected number of modifications. The `collapsed_to_one_edit` bucket went from 9 → 0.

GR-046 (same-clause multi-intent: "Doubled the cheese, swapped cheddar for gruyere") — flagged in Phase 4 as a residual-risk case — **also recovered**: the LLM emitted both a quantity_adjustment edit and a separate ingredient_substitution edit. The list output relieved enough pressure that even tightly fused intents got teased apart.

GR-050 (same-line replace conflict) — also flagged as residual — likewise recovered with three distinct modifications, each well-formed. No more `"0 cup white sugar, 2 cups packed brown sugar"`-style compromise payloads.

### What regressed

| ID | What happened | Diagnosis |
|---|---|---|
| **GR-007** | `find` anchor was `"2 large apples"` but recipe has `"3 large apples"` | Quantity drift in the anchor — LLM paraphrased the recipe line. Not a groundedness gate failure of grammar; a hallucinated number. |
| **GR-014** | Removed thyme as requested, **also** removed the instruction step `"Sprinkle salt, pepper, and thyme over the chicken."` | R2 over-reach — consistency cascade. Same pattern as GR-016 in baseline. Now firing on a new review because the model has more output budget. |

### Shifted (still failing, but in different ways)

| ID | Baseline failure | Option A failure | Read |
|---|---|---|---|
| **GR-040** | over-extraction +1 | over-extraction +2, ungrounded | R2 grew (now changes temp + removes roast step + adds a new roast step). One of the new edits anchors on `"Preheat oven to 425 degrees F."` — text the LLM just *invented* in a prior edit. Sequence-dependent grounding. |
| **GR-048** | -3 edits (extracted 1 of 4) | -1 edit (extracted 3 of 4) | Big improvement. Missed merging the time change into the temp change instruction line. Close miss, not a structural failure. |

### Persistent failures (not addressed by R1 MVP)

GR-011, GR-013, GR-016 — all flagged in baseline as R2 (over-reach) or R3 (type taxonomy misfit). Untouched by Option A scope, as intended.

## Failure-bucket comparison

```
                          baseline  ->  option_a_mvp
collapsed_to_one_edit         9     ->     0    ✅
over_extraction               3     ->     4    R2 grew by 1
wrong_modification_type       3     ->     2    GR-041 recovered
ungrounded_find_string        0     ->     2    NEW
under_extraction              0     ->     1    GR-048 (close miss)
```

## Net read

The fix did exactly what Phase 5 projected:
- **R1 dissolved.** Multi-tweak collapse went from the dominant failure mode to absent.
- **R2 modestly amplified** (3 → 4 cases). Predicted: giving the LLM more output rope without anti-overreach anchoring lets cascades grow. This is the next prompt change.
- **Groundedness slipped** (100% → 97%). Two cases. One quantity-anchor paraphrase, one sequence-dependent invented anchor. Neither would have been caught by a `find` substring gate written naively against the *original* recipe — GR-040's anchor would have been valid against an intermediate state.
- **GR-050 compromise payload risk dissolved**, as predicted in Phase 4. The dreaded "0 cup white sugar, 2 cups packed brown sugar" pattern is gone.

Trade is overwhelmingly favorable: +14pp on the headline correctness metric, +2pp on type, -2.78pp on a metric that was a Phase 1c top-3 concern but turned out to be over-indexed in the baseline.

## What still doesn't work (intentionally out of MVP scope)

| Failure | Reviews | Fix |
|---|---|---|
| R2 over-reach / consistency cascades | GR-011, GR-014, GR-016, GR-040 | Prompt anchor: "Only extract changes the reviewer explicitly states; do not infer cascade or consistency edits." |
| R3 type taxonomy misfit | GR-013 | Either expand taxonomy, or accept as cosmetic |
| #4 single review per recipe | every recipe | Iterate over `featured_tweaks` or top-N rated reviews |
| #5 `featured_tweaks` ignored | every recipe | Pipeline reads the wrong field |
| #6 random selection | every recipe | Rank by rating/votes |
| #2 silent no-op replace | not yet measured (Layer 2 eval not built) | Stage 2 sanity gate |
