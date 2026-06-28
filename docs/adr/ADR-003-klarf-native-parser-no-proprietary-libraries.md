# ADR-003: Native KLARF 1.x Parser Without Proprietary Libraries

**Author:** Yeonkuk Woo
**Status:** Accepted
**Date:** 2024-11-15

---

## Context

KLARF (KLA Results File) is the semiconductor industry standard for transferring defect inspection results between yield management systems and inspection tools. KLA Tencor, Onto Innovation, and AMAT inspection platforms all produce KLARF-format output files. Any open-source yield management platform targeting domestic silicon wafer fabs must be capable of reading KLARF data from these tools.

The reference KLARF parser is a proprietary library distributed by KLA and not available for open-source redistribution. Third-party commercial parsers exist but carry licensing costs. Several open-source projects have attempted KLARF support using regex-based approaches that fail on multi-wafer files or non-standard field ordering.

OpenYield requires KLARF ingestion capability that is:
1. Available under an open-source license
2. Reproducible without proprietary libraries
3. Robust to the field ordering variations present in real fab output files

---

## Decision

OpenYield implements a native KLARF 1.x ASCII parser (`ingestion/adapters/klarf_adapter.py`) in approximately 300 lines of pure Python. The parser:

- Reads the `DefectRecordSpec` keyword to determine column order dynamically, rather than assuming a fixed column layout
- Converts KLARF coordinate units (µm) to OpenYield schema units (mm) based on the `Units` keyword
- Handles multi-wafer KLARF files by detecting `WaferID` sections and producing separate panel records
- Maps KLARF `CLASSNUMBER` integers to `defect_type` strings via a configurable class map
- Assigns a caller-supplied `confidence_score` (KLARF 1.x has no native confidence field)

The parser handles the KLARF keyword-value ASCII format without regular expressions; it uses string splitting and state tracking, which is more robust to whitespace variations in real tool output.

---

## Rationale

**1. Open-source redistribution requires a clean-room implementation.**
The Apache 2.0 license under which OpenYield is released prohibits including proprietary parser libraries. A clean-room implementation written entirely from scratch by the author is the only path to unrestricted redistribution and use by CHIPS Act beneficiaries including national laboratories, academic institutions, and emerging domestic fabs.

**2. `DefectRecordSpec`-driven parsing is necessary for real fab data.**
KLARF 1.x defines the column order of the `DefectList` section through the `DefectRecordSpec` keyword. Different tools (KLA 2920, Onto EDGE, AMAT Patterned Wafer Inspection) produce KLARF files with different column orders. A parser that assumes a fixed column layout will silently misread coordinates from tools whose column order differs from the assumption. Reading `DefectRecordSpec` at parse time and building a column index is the only correct approach.

**3. Unit conversion at parse time prevents downstream errors.**
KLARF files specify coordinates and sizes in microns by default. The OpenYield schema stores all spatial values in millimeters. Converting at parse time, in the adapter, ensures that no millimeter/micron confusion can propagate into the database. The conversion factor (0.001 for MICRON, 1.0 for MM) is derived from the KLARF `Units` keyword and applied uniformly to `XREL`, `YREL`, and `DEFECTSIZE`.

**4. A configurable class map enables fab-specific defect taxonomies.**
Different fabs use different KLARF `CLASSNUMBER` schemes. The `KlarfAdapter` accepts a `defect_class_map` parameter that overrides the default mapping. This is essential for SEMI consortium members who need OpenYield to interoperate with their existing defect classification schemes without modifying the parser source.

---

## Consequences

- The parser supports KLARF 1.x ASCII only. KLARF 2.0 (binary format) is not supported in this implementation. KLARF 2.0 adoption remains limited in the installed base of inspection tools at domestic fabs; this is an acceptable limitation for the current version.
- `confidence_score` is assigned as a fixed value by the caller rather than derived from KLARF fields. This is architecturally correct: KLARF 1.x has no confidence field, and assigning a fixed value makes the assumption explicit rather than silently defaulting to 0 or 1.
- Files that end without a `SummarySpec` or `EndOfFile` keyword (a known variation in some older tool outputs) are handled correctly: the parser checks for `in_defect_list` state at end-of-file and processes any buffered defect lines.
- The parser raises `ValueError` on malformed records rather than silently skipping them, consistent with the `BaseAdapter` contract. Callers that need fault tolerance can instantiate `CsvAdapter(skip_invalid=True)` for CSV data; a `skip_invalid` option for `KlarfAdapter` is a candidate for a future release.

---

## Alternatives Considered

**Third-party commercial KLARF library (e.g., GENIE by PDF Solutions)**: Rejected. Licensing costs and redistribution restrictions are incompatible with the open-source mandate.

**Regex-based line parsing**: Considered and rejected. Regex parsers for KLARF are brittle to whitespace variations and multi-line keyword values. State-machine parsing with string splitting is more robust and more legible to contributors unfamiliar with KLARF format details.

**KLA's open KLARF Python library (if available at the time)**: No maintained, license-clean Python KLARF library was available at the time of implementation. A clean-room implementation was the only viable path.
