# ADR-005: Three Yield Models with Empirical Clustering-Alpha Estimation

**Author:** Yeonkuk Woo
**Status:** Accepted
**Date:** 2024-12-10

---

## Context

Semiconductor yield — the fraction of dies on a wafer or components on a panel that meet specification — is the primary economic metric of a manufacturing process. Computing yield from inspection data requires a mathematical model that relates defect density to expected functional yield.

Three models have been established in the semiconductor industry literature since the 1980s:

1. **Poisson model** (simplest): assumes random, independent defect distribution
2. **Murphy model**: assumes a triangular distribution of defect densities across the wafer
3. **Negative Binomial model** (Seeds/Stapper): parameterized by a clustering factor α that describes the degree of spatial overdispersion in the defect distribution

The appropriate model depends on the process node, substrate type, and the degree of defect clustering. A yield management platform that implements only one model either over-penalizes yield (Poisson on a clustered process) or under-estimates yield loss (Negative Binomial with high α on a random process).

OpenYield must report yield estimates that a fab engineer can trust for process control decisions.

---

## Decision

OpenYield implements all three models and reports estimates from all three simultaneously in `YieldEstimate`. The system also:

1. **Estimates the clustering parameter α empirically** from the observed per-die defect count distribution using the method of moments, when sufficient data is available
2. **Selects and annotates a recommended model** per panel based on substrate type, empirical α, and the A·D₀ product
3. **Persists all three estimates** to the `yield_estimates` table so historical trends can be plotted for all three models

---

## Rationale

**1. Different models are appropriate for different substrate types and process nodes.**
Glass panel AOI inspection typically produces near-random defect distributions (Poisson regime) because particles deposit randomly across large substrates. Silicon wafer inspection at advanced nodes (<28nm) typically shows clustered defects driven by lithography excursions, equipment contamination events, and edge ring non-uniformity — the Negative Binomial model is appropriate in these cases. Reporting only one model without substrate-aware selection would produce incorrect yield estimates for at least one of the two primary substrate types OpenYield supports.

**2. Empirical α estimation enables accurate yield prediction without substrate-specific calibration curves.**
The clustering parameter α is typically determined from historical data or substrate-specific calibration runs — information that a new fab or an academic user does not have. The method-of-moments estimator implemented in `yield_engine/models.py:estimate_alpha_empirical()` derives α from the observed per-die defect variance:

```
α = μ² / (σ² − μ)
```

When the defect distribution is Poisson-like (σ² ≤ μ), the estimator returns α = 50 (near-random limit) rather than failing. This makes the yield engine self-calibrating: it adapts to the observed defect distribution without requiring the user to supply substrate-specific parameters.

**3. Reporting all three estimates enables model comparison and trust calibration.**
A fab process engineer who receives only a single yield number cannot assess model uncertainty. By reporting Poisson, Murphy, and Negative Binomial estimates simultaneously, OpenYield allows the engineer to see how sensitive the yield prediction is to the choice of model. A large spread between Poisson and Negative Binomial estimates indicates highly clustered defects and high model uncertainty; a small spread indicates a near-random distribution where all three models agree.

**4. The recommended model annotation guides non-expert users.**
Academic researchers and national laboratory staff using OpenYield for the first time may not have expertise in yield modeling. The `recommended_model` field in `YieldEstimate` provides a model selection recommendation with a human-readable explanation (`model_notes`) based on the substrate type, α, and A·D₀. This lowers the barrier to correct use without hiding the underlying model choice.

---

## Model Reference

All three models are implemented in `yield_engine/models.py`:

| Model | Formula | Reference |
|---|---|---|
| Poisson | `Y = exp(−A·D₀)` | Standard result; see Stapper 1983 |
| Murphy | `Y = ((1−exp(−A·D₀)) / (A·D₀))²` | Murphy 1964 |
| Negative Binomial | `Y = (1 + A·D₀/α)^(−α)` | Seeds 1956; Stapper 1983 |

Where:
- `A` = die critical area (mm²)
- `D₀` = defect density (defects/mm²) from system_a inspection data on active dies
- `α` = clustering factor (α → ∞: Poisson limit; α = 1: moderate clustering; α = 0.5: high clustering)

**References:**
- C.H. Stapper, "Modeling of Integrated Circuit Defect Sensitivities," *IBM Journal of Research and Development*, 27(6), 1983.
- W. Maly, "Modeling of Lithography Related Yield Losses for CAD of VLSI Circuits," *IEEE Trans. CAD*, 4(3), 1985.

---

## Consequences

- Die area is computed as `pitch²` (component pitch squared), which equals the full die area. The critical area fraction (the fraction of die area that, if struck by a defect, causes a functional failure) is not separately parameterized in this version. This is a simplification: for a first-order yield estimate, `A = full_die_area` is standard practice for mature and mid-range nodes. A critical area extraction capability is a candidate for Phase 2 (see roadmap).
- The empirical α estimator requires at least 4 active dies with non-zero defect counts to produce a meaningful estimate. Below this threshold, it falls back to the substrate profile default α. This is logged as a warning.
- All three yield values are stored to the database even when only one is recommended. This is intentional: the full history of all three estimates enables retrospective model comparison as process data accumulates.

---

## Alternatives Considered

**Single model (Negative Binomial only)**: Rejected. The Negative Binomial model degenerates to Poisson as α → ∞, but the numerical behavior near α = ∞ is poorly conditioned. More importantly, reporting only one model removes the transparency that fab engineers need to assess model validity.

**Fixed α from literature (α = 0.5 for advanced wafer, α = 1.0 for glass panel)**: Partially used as the fallback when empirical estimation is not possible (insufficient die count). The substrate profiles encode default α values. Empirical estimation is always preferred when the data permits it.

**Yield integration over the full wafer map**: A more accurate yield estimate would integrate the yield model over a spatial defect density map, accounting for within-wafer non-uniformity. This is the approach used in KLA Klarity's yield prediction module. This capability is deferred to Phase 3 of the roadmap, where spatial yield prediction is scoped as a deliverable.
