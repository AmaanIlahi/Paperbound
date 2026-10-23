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
