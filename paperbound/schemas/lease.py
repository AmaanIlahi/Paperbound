from typing import Literal

from pydantic import BaseModel, Field


class _Extracted(BaseModel):
    source_quote: str | None = Field(
        description="Text copied verbatim from the page that supports the value, at most 30 words. Null if value is null."
    )
    page: int | None = Field(description="1-based page number the quote appears on. Null if value is null.")


class StrField(_Extracted):
    value: str | None


class DateField(_Extracted):
    value: str | None = Field(description="ISO 8601 date, YYYY-MM-DD")


class AmountField(_Extracted):
    value: float | None = Field(description="Plain number without currency symbol or separators")


class PeriodField(_Extracted):
    value: Literal["monthly", "quarterly", "annual"] | None


class DaysField(_Extracted):
    value: int | None = Field(description="Number of days as an integer")


class PercentField(_Extracted):
    value: float | None = Field(description="Percentage as a plain number, e.g. 150 for 150%, without the % sign")


# Words that must appear in a field's quote, or in the sentence around it, for the quote to be about that field. Each is
# matched case-insensitively at the start of a word ("expir" covers expires, expiration), after hyphens become spaces.
# General domain wording, used by validate.py (field_context_mismatch).
LEASE_ANCHORS: dict[str, tuple[str, ...]] = {
    "landlord": ("landlord", "lessor", "owner", "between"),
    "tenant": ("tenant", "lessee", "occupant", "between"),
    "premises_address": ("premises", "property", "located", "situated", "address", "building", "suite", "demised"),
    "start_date": ("commenc", "begin", "start", "effective", "term"),
    "end_date": ("expir", "terminat", "end", "through", "until", "term", "conclude"),
    "rent_amount": ("rent", "installment", "payable", "per month", "monthly"),
    "rent_period": ("rent", "monthly", "quarterly", "annual", "per month", "per year", "installment", "payable", "in advance"),
    "notice_period_days": ("notice", "notif", "prior to", "before", "advance"),
    "holdover_rate_percent": (
        "holdover", "holding over", "hold over", "holds over", "overholding", "remains in possession",
        "remain in possession", "remains in occupancy", "continues to occupy", "retains possession", "at sufferance",
    ),
    "security_deposit": ("deposit", "security", "bond", "retainer"),
    "notice_method": ("notice", "notif", "mail", "deliver", "courier", "writing", "written", "registered"),
    "notice_section": ("notice", "notif", "renew", "terminat"),
    "rent_escalation_percent": ("escalat", "increase", "adjust", "annual", "each year", "per annum", "cpi", "thereafter", "subsequent"),
    "current_rent": ("rent", "monthly", "per month", "payable", "schedule", "lease year"),
}


class LeaseFields(BaseModel):
    landlord: StrField
    tenant: StrField
    premises_address: StrField
    start_date: DateField
    end_date: DateField
    rent_amount: AmountField = Field(
        description="Base rent for the first lease year, not later escalated amounts"
    )
    rent_period: PeriodField
    notice_period_days: DaysField = Field(
        description="Days of advance written notice the tenant must give before expiration to renew or terminate"
    )
    holdover_rate_percent: PercentField = Field(
        description="Holdover rent if the tenant stays past expiration, as a percent of the base rent (150 means 150%)"
    )
    security_deposit: AmountField = Field(description="Security deposit amount the tenant pays the landlord")
    notice_method: StrField = Field(
        description="How the required notice must be delivered, e.g. written notice by certified mail"
    )
    notice_section: StrField = Field(
        description="The lease section that contains the notice requirement, written like 'Section 11'"
    )
    rent_escalation_percent: PercentField = Field(
        description="Annual percentage increase of base rent (3 means 3% per year)"
    )
    current_rent: AmountField = Field(
        description="Monthly base rent in effect today: the amount in the rent table row whose period contains today's date"
    )
