"""
api/routers/validation_router.py
----------------------------------
Author: Yeonkuk Woo

Validation suite endpoint.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends
from openyield.api.dependencies import get_db
from openyield.api.schemas import ValidationResponse, CheckResult
from openyield.validation.checks import run_all_checks

router = APIRouter(prefix="/validation", tags=["validation"])
Connection = Any


@router.get("", response_model=ValidationResponse)
def run_validation(conn: Connection = Depends(get_db)):
    """Run the full 10-check validation suite against the current database."""
    results = run_all_checks(conn)
    passed  = sum(1 for r in results if r.passed)
    return ValidationResponse(
        passed=passed,
        total=len(results),
        all_passed=(passed == len(results)),
        results=[
            CheckResult(
                check_name=r.check_name,
                passed=r.passed,
                metric=r.metric,
                detail=r.detail,
            )
            for r in results
        ],
    )
