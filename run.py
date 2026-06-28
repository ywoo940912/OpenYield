"""
run.py — OpenYield application entry point.

Usage:
    uvicorn run:app --reload --port 8000
"""
from openyield.api.main import app  # noqa: F401
