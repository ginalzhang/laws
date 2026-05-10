"""Multi-agent ensemble extraction for petition rows.

Two vision passes (Haiku + Sonnet) → reconciliation (Sonnet) → deterministic
validation. Used as an alternative to per-field Google Vision OCR for messy
handwriting on petition signature sheets.
"""
from .ensemble import extract_row_ensemble

__all__ = ["extract_row_ensemble"]
