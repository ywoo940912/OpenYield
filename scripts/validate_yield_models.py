"""
scripts/validate_yield_models.py
---------------------------------
Author: Yeonkuk Woo

Validates OpenYield's three yield models against their published
closed-form analytical formulas.

WHY THIS EXISTS
---------------
Semiconductor yield prediction is based on well-established models from
the academic and industrial literature:

  Poisson     — Cunningham (1990), Seeds (1967)
  Murphy      — Murphy (1964), triangular defect density distribution
  NegBinom    — Stapper (1973), clustering parameterized by α

A yield engineering platform making yield predictions must demonstrate that
its calculations are mathematically correct. This script provides that proof
by testing the OpenYield engine against independent Python implementations
of the exact same published formulas at a sweep of (D0, A, α) values.

Every test case must pass within a tolerance of 1e-9 (floating-point rounding
only). A failure here means the engine has diverged from the published math.

PETITION RELEVANCE
------------------
This file serves as verifiable technical evidence that OpenYield's yield
calculations are grounded in peer-reviewed semiconductor engineering
literature and are independently reproducible — a requirement for any
platform intended for U.S. domestic fab yield monitoring under the
CHIPS and Science Act of 2022.

USAGE
-----
    python scripts/validate_yield_models.py

    Exit code 0 = all tests passed.
    Exit code 1 = one or more failures (details printed to stdout).
"""

import math
import sys

# ── Reference implementations (pure math, no OpenYield imports) ───────────────
# These are the canonical formulas from the literature, implemented
# independently to serve as ground truth for comparison.

def ref_poisson(A: float, D0: float) -> float:
    """
    Poisson yield model.
    Source: Seeds (1967), Cunningham (1990).
        Y = exp(-A × D0)
    """
    return math.exp(-A * D0)


def ref_murphy(A: float, D0: float) -> float:
    """
    Murphy (1964) triangular distribution yield model.
        Y = ((1 - exp(-A × D0)) / (A × D0))²
    Limit as A×D0 → 0: Y → 1.0  (L'Hôpital)
    """
    AD = A * D0
    if AD < 1e-12:
        return 1.0
    return ((1.0 - math.exp(-AD)) / AD) ** 2


def ref_negbinom(A: float, D0: float, alpha: float) -> float:
    """
    Negative binomial (Seeds–Stapper) yield model.
    Source: Stapper (1973).
        Y = (1 + A × D0 / α)^(−α)
    Limit as α → ∞: degenerates to Poisson.
    """
    return (1.0 + (A * D0) / alpha) ** (-alpha)


# ── Test matrix ───────────────────────────────────────────────────────────────
# Each row is (description, A_mm2, D0_per_mm2, alpha)
# Covers: clean process, typical production, high-defect excursion,
# small/large die, advanced node clustering, and degenerate limits.

TEST_CASES = [
    # (label, A, D0, alpha)
    # --- Near-clean process ---
    ("Clean process, 1 mm² die",          1.0,   0.01,  2.0),
    ("Clean process, 10 mm² die",        10.0,   0.01,  2.0),
    # --- Typical production ---
    ("Typical wafer, 28 nm node",         4.0,   0.05,  1.5),
    ("Typical glass panel die",           9.0,   0.08,  3.0),
    ("Moderate defect density",          16.0,   0.12,  1.0),
    # --- Advanced node clustering ---
    ("7nm, high clustering α=0.5",        2.0,   0.30,  0.5),
    ("5nm, very high clustering α=0.3",   1.5,   0.50,  0.3),
    ("3nm, extreme clustering α=0.1",     1.0,   0.80,  0.1),
    # --- High defect / excursion ---
    ("Excursion event, D0=1.0",           4.0,   1.00,  1.0),
    ("Severe excursion, D0=5.0",          4.0,   5.00,  2.0),
    # --- Large die (reticle-size) ---
    ("Large reticle, 858 mm²",          858.0,   0.003, 2.0),
    # --- NegBinom → Poisson degeneration ---
    ("NegBinom α=1000 ≈ Poisson",         4.0,   0.10,  1000.0),
    # --- Murphy degenerate limit (AD → 0) ---
    ("Murphy AD→0 limit",                 0.01,  0.001, 2.0),
    # --- Zero defect density ---
    ("Zero D0, perfect yield",            4.0,   0.00,  2.0),
]

TOLERANCE = 1e-9   # max allowed absolute error (floating-point rounding only)


# ── Engine under test ─────────────────────────────────────────────────────────

def load_engine():
    """Import OpenYield's yield model functions."""
    try:
        from openyield.yield_engine.models import (
            poisson_yield, murphy_yield, negbinom_yield,
        )
        return poisson_yield, murphy_yield, negbinom_yield
    except ImportError as e:
        print(f"\n[ERROR] Could not import OpenYield: {e}")
        print("  Run from the project root: python scripts/validate_yield_models.py")
        sys.exit(1)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_validation():
    poisson_yield, murphy_yield, negbinom_yield = load_engine()

    print("=" * 72)
    print("  OpenYield — Yield Model Validation")
    print("  Testing against published analytical formulas")
    print("  Tolerance: {:.0e} (floating-point rounding only)".format(TOLERANCE))
    print("=" * 72)

    header = f"{'Case':<42} {'Model':<10} {'Reference':>12} {'Engine':>12} {'ΔErr':>10} {'Status':>7}"
    print(header)
    print("-" * 97)

    failures = []
    n_tests = 0

    for label, A, D0, alpha in TEST_CASES:
        pairs = [
            ("Poisson",  ref_poisson(A, D0),         poisson_yield(A, D0) if D0 > 0 or True else 1.0),
            ("Murphy",   ref_murphy(A, D0),           murphy_yield(A, D0)),
            ("NegBinom", ref_negbinom(A, D0, alpha),  negbinom_yield(A, D0, alpha)),
        ]

        for model_name, ref_val, eng_val in pairs:
            err = abs(ref_val - eng_val)
            status = "PASS" if err <= TOLERANCE else "FAIL"
            n_tests += 1

            # Truncate label if too long
            short_label = label[:41]
            print(f"{short_label:<42} {model_name:<10} {ref_val:>12.8f} {eng_val:>12.8f} {err:>10.2e}  {status}")

            if status == "FAIL":
                failures.append((label, model_name, ref_val, eng_val, err))

        print()  # blank line between test groups

    print("=" * 72)

    # Special check: NegBinom → Poisson degeneration
    print("\nDegeneration checks:")
    A, D0, big_alpha = 4.0, 0.10, 1e6
    nb = negbinom_yield(A, D0, big_alpha)
    p  = poisson_yield(A, D0)
    diff = abs(nb - p)
    degen_ok = diff < 1e-5
    print(f"  NegBinom(α=1e6) → Poisson:  NB={nb:.8f}  P={p:.8f}  Δ={diff:.2e}  {'PASS' if degen_ok else 'FAIL'}")
    if not degen_ok:
        failures.append(("Degeneration: NegBinom→Poisson", "NegBinom", p, nb, diff))

    # Murphy → 1.0 as AD→0
    y_murphy_zero = murphy_yield(1.0, 0.0)
    murphy_zero_ok = abs(y_murphy_zero - 1.0) < 1e-9
    print(f"  Murphy(D0=0) → 1.0:         Y={y_murphy_zero:.8f}  Δ={abs(y_murphy_zero-1.0):.2e}  {'PASS' if murphy_zero_ok else 'FAIL'}")
    if not murphy_zero_ok:
        failures.append(("Murphy D0=0 limit", "Murphy", 1.0, y_murphy_zero, abs(y_murphy_zero - 1.0)))

    print()
    print("=" * 72)
    print(f"  Results: {n_tests} formula comparisons")

    if failures:
        print(f"  FAILED: {len(failures)} test(s)")
        print()
        for label, model, ref, eng, err in failures:
            print(f"  ✗ {label} / {model}")
            print(f"    Reference: {ref:.10f}")
            print(f"    Engine:    {eng:.10f}")
            print(f"    Error:     {err:.2e}")
        print("=" * 72)
        sys.exit(1)
    else:
        print(f"  ALL {n_tests} TESTS PASSED")
        print()
        print("  OpenYield yield models are mathematically consistent with:")
        print("  • Poisson model  — Seeds (1967), Cunningham (1990)")
        print("  • Murphy model   — Murphy (1964)")
        print("  • NegBinom model — Stapper (1973), Seeds-Stapper formulation")
        print("=" * 72)
        sys.exit(0)


if __name__ == "__main__":
    run_validation()
