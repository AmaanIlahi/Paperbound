"""Schemas for the tabular document types. The model returns only a mapping (which column or which label means what);
every value in the document is parsed by code."""

from typing import Literal

from pydantic import BaseModel

RENT_ROLL_FIELDS = ("unit", "tenant", "sqft", "lease_start", "lease_end", "monthly_rent", "annual_rent", "rent_psf", "status")
NUMERIC_RENT_ROLL_FIELDS = ("sqft", "monthly_rent", "annual_rent", "rent_psf")

REVENUE_CATEGORIES = ("base_rent", "recoveries", "other_income", "vacancy_credit_loss")
OPEX_CATEGORIES = ("taxes", "insurance", "utilities", "repairs_maintenance", "management", "payroll", "general_admin", "other_opex")
BELOW_LINE_CATEGORIES = ("capex", "reserves", "debt_service", "depreciation")
T12_CATEGORIES = (*REVENUE_CATEGORIES, *OPEX_CATEGORIES, *BELOW_LINE_CATEGORIES, "subtotal", "ignore")


class ColumnAssignment(BaseModel):
    column: int
    field: Literal["unit", "tenant", "sqft", "lease_start", "lease_end", "monthly_rent", "annual_rent", "rent_psf", "status", "ignore"]


class RentRollMapping(BaseModel):
    columns: list[ColumnAssignment]


class LabelAssignment(BaseModel):
    line: int
    category: Literal[
        "base_rent", "recoveries", "other_income", "vacancy_credit_loss",
        "taxes", "insurance", "utilities", "repairs_maintenance", "management", "payroll", "general_admin", "other_opex",
        "capex", "reserves", "debt_service", "depreciation",
        "subtotal", "ignore",
    ]


class LabelMapping(BaseModel):
    lines: list[LabelAssignment]
