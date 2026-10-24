from pydantic import BaseModel, Field

from paperbound.schemas.lease import StrField


class GenericContractFields(BaseModel):
    """Five fields any commercial contract can carry. Every value is text; a full date is written as ISO."""

    parties: StrField = Field(description="Names of all parties to the agreement, as the contract names them")
    effective_date: StrField = Field(
        description="When the agreement takes effect: YYYY-MM-DD if a full date is stated, otherwise the wording used"
    )
    expiration_date: StrField = Field(
        description="When the initial term ends: YYYY-MM-DD if a full date is stated, otherwise the wording used"
    )
    renewal_term: StrField = Field(
        description="Length and conditions of any renewal or extension after the initial term, as worded"
    )
    notice_to_terminate_renewal: StrField = Field(
        description="Advance notice a party must give to stop the agreement renewing, as worded"
    )


# ---- generic fallback: documents of a type Paperbound has no schema for ----


class FallbackParty(BaseModel):
    name: str = Field(description="Name of a person or organisation the document names, as printed")
    role: str = Field(description="Its role in this document, in a few words (for example 'issuer', 'recipient', 'investor')")
    source_quote: str = Field(description="Text copied verbatim from the page that names this party, at most 30 words")
    page: int = Field(description="1-based page number the quote appears on")


class FallbackDate(BaseModel):
    value: str = Field(description="A full calendar date as ISO 8601, YYYY-MM-DD")
    label: str = Field(description="What the date is, in a few words (for example 'due date', 'notice date')")
    source_quote: str = Field(description="Text copied verbatim from the page that states the date, at most 30 words")
    page: int = Field(description="1-based page number the quote appears on")


class FallbackAmount(BaseModel):
    value: float = Field(description="Plain number without currency symbol or separators; cents kept as printed")
    currency: str | None = Field(description="ISO 4217 code such as USD or EUR; null if the document does not say")
    label: str = Field(description="What the amount is, in a few words (for example 'capital call amount')")
    source_quote: str = Field(description="Text copied verbatim from the page that states the amount, at most 30 words")
    page: int = Field(description="1-based page number the quote appears on")


class FallbackObligation(BaseModel):
    description: str = Field(description="Something a party must do, in one short sentence")
    due_date: str | None = Field(description="ISO 8601 date YYYY-MM-DD by which it must be done; null if no full date is stated")
    source_quote: str = Field(description="Text copied verbatim from the page that states the obligation (and its date), at most 30 words")
    page: int = Field(description="1-based page number the quote appears on")


class FallbackFields(BaseModel):
    parties: list[FallbackParty]
    dates: list[FallbackDate]
    amounts: list[FallbackAmount]
    obligations: list[FallbackObligation]
