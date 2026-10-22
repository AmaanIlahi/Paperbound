from pydantic import BaseModel, Field

from paperbound.schemas.lease import AmountField, DateField, StrField

MAX_POLICIES = 6


class PolicyFields(BaseModel):
    type: StrField = Field(description="Kind of coverage, e.g. 'General Liability' or 'Umbrella Liability'")
    insurer: StrField = Field(description="Insurance company that issued this policy")
    number: StrField = Field(description="Policy number exactly as printed")
    effective_date: DateField = Field(description="Policy effective date")
    expiration_date: DateField = Field(description="Policy expiration date")
    each_occurrence_limit: AmountField = Field(description="Each occurrence limit in dollars")
    aggregate_limit: AmountField = Field(description="Aggregate limit in dollars (general aggregate or annual aggregate)")


class InsuranceCertFields(BaseModel):
    insured: StrField = Field(description="The insured party named on the certificate")
    certificate_holder: StrField = Field(description="The certificate holder named on the certificate")
    producer: StrField = Field(description="The producer (insurance agency or broker) that issued the certificate")
    policies: list[PolicyFields] = Field(
        description=f"One entry per policy listed on the certificate (at most {MAX_POLICIES}), in the order printed"
    )
