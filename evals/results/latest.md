# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261025_132132, lease 20261025_132132, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.4% | 66.7% | 98.1% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.5 s |
| insurance_certificate/clean | 12 | 100.0% | 98.3% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 12.9 s |
| insurance_certificate/injection | 3 | 100.0% | 98.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.6 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.1 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 95.9% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 9.5 s |
| lease/all | 20 | 100.0% | 97.9% | 100.0% | 99.3% | 100.0% | 100.0% | 80.5% | 80.0% | $0.0017 | 16.4 s |
| lease/clean | 12 | 100.0% | 97.6% | - | 99.4% | 100.0% | 100.0% | 80.2% | 75.0% | $0.0018 | 17.2 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 10.8 s |
| lease/late_page | 2 | 100.0% | 100.0% | - | 96.4% | 100.0% | 100.0% | 81.2% | 100.0% | $0.0017 | 11.0 s |
| lease/missing_field | 3 | 100.0% | 95.2% | 100.0% | 100.0% | 100.0% | 100.0% | 55.6% | 66.7% | $0.0013 | 11.3 s |

**Usable view** (beside the metrics above, which are unchanged): a field carrying a blocking flag counts as abstained, not extracted, because the pipeline shows it but never uses it. "Wrong among answered" is the share of unblocked answers that are wrong, the errors that reach the user.

| Group | Field acc. | Usable-view acc. | Missing ok | Usable-view missing ok | Wrong among answered | Abstained |
|---|---|---|---|---|---|---|
| insurance_certificate/all | 98.4% | 98.1% | 66.7% | 100.0% | 0.0% | 8 |
| insurance_certificate/clean | 98.3% | 98.3% | - | - | 0.0% | 4 |
| insurance_certificate/injection | 98.0% | 98.0% | - | - | 0.0% | 1 |
| insurance_certificate/late_page | 100.0% | 100.0% | - | - | 0.0% | 0 |
| insurance_certificate/missing_field | 98.0% | 96.1% | 66.7% | 100.0% | 0.0% | 3 |
| lease/all | 97.9% | 95.7% | 100.0% | 100.0% | 1.1% | 9 |
| lease/clean | 97.6% | 95.8% | - | - | 0.6% | 6 |
| lease/injection | 100.0% | 97.6% | - | - | 0.0% | 1 |
| lease/late_page | 100.0% | 96.4% | - | - | 0.0% | 1 |
| lease/missing_field | 95.2% | 92.9% | 100.0% | 100.0% | 5.3% | 1 |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 1 | 0.0% | 0.50 |
| 0.7-0.9 | 6 | 100.0% | 0.87 |
| >=0.9 | 366 | 98.6% | 1.00 |

ECE: 0.013 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 7 | 57.1% | 0.70 |
| 0.7-0.9 | 2 | 100.0% | 0.88 |
| >=0.9 | 268 | 98.9% | 1.00 |

ECE: 0.014 over 277 fields.


### Top failure modes

- wrong_field: current_rent: 6
- wrong_field: type: 5
- action: 4
- brief_withheld: holdover_rent: 4
- brief_withheld: holdover_extra: 4
- brief_withheld: holdover_percent: 4
- brief_wrong: current_rent: 3
- brief_wrong: next_rent: 3

Failure log: `evals/results/failures_synthetic_20261025_132132.json`

- DROP synthetic/insurance_certificate/missing_field/grounding_rate: 100.0% -> 95.9% (-4.08 points)
- DROP synthetic/insurance_certificate/missing_field/usable_rate: 100.0% -> 95.8% (-4.17 points)
- DROP synthetic/lease/injection/usable_rate: 100.0% -> 97.6% (-2.38 points)

## generic (run 20261025_132159, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

**Usable view** (beside the metrics above, which are unchanged): a field carrying a blocking flag counts as abstained, not extracted, because the pipeline shows it but never uses it. "Wrong among answered" is the share of unblocked answers that are wrong, the errors that reach the user.

Usable view: field acc. 100.0% -> 97.7%; wrong among answered 0.0%; abstained 1.

Label guess right: 100.0%; field label right (of matched): 88.6%; email drafts proposed: 0; extra fields not in the ground truth: 0.

### Top failure modes

- none

Failure log: `evals/results/failures_generic_20261025_132159.json`


## cuad (run 20261025_132434, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 56.2% | 80.6% | 57.7% | 100.0% | 100.0% | 80.8% (26) | 100.0% |
| expiration_date | 39/50 | 83.9% | 39.4% | 49.5% | 87.2% | 36.4% | 66.7% (6) | 97.4% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 32.2% | 45.8% | 92.9% | 61.1% | - | 94.3% |
| parties | 50/50 | 42.7% | 71.1% | 49.2% | 100.0% | - | - | 86.0% |
| renewal_term | 18/50 | 94.4% | 38.2% | 50.8% | 94.4% | 40.6% | - | 90.0% |
| all | 159/250 | 66.3% | 58.4% | 51.2% | 95.6% | 56.0% | 78.1% (32) | 93.6% |

**Usable view** (beside the metrics above, which are unchanged): a field carrying a blocking flag counts as abstained, not extracted, because the pipeline shows it but never uses it. "Wrong among answered" is the share of unblocked answers that are wrong, the errors that reach the user.

| Category | Date exact | Usable-view date exact | Wrong among answered dates | Abstained |
|---|---|---|---|---|
| effective_date | 80.8% | 73.1% | 9.5% | 20 |
| expiration_date | 66.7% | 66.7% | 0.0% | 32 |
| notice_to_terminate_renewal | - | - | - | 8 |
| parties | - | - | - | 43 |
| renewal_term | - | - | - | 15 |
| all | 78.1% | 71.9% | 8.0% | 118 |

Cost per contract $0.0014, 8603 tokens, p95 latency 4.2 s.

### Top failure modes

- low_overlap: parties: 23
- unlabeled_extraction: notice_to_terminate_renewal: 22
- low_overlap: expiration_date: 14
- unlabeled_extraction: renewal_term: 13
- unlabeled_extraction: effective_date: 12
- low_overlap: effective_date: 12
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 7

Failure log: `evals/results/failures_cuad_20261025_132434.json`

- DROP cuad/all/date_exact: 86.2% -> 78.1% (-8.08 points)
- DROP cuad/effective_date/date_exact: 87.0% -> 80.8% (-6.19 points)
- DROP cuad/expiration_date/date_exact: 83.3% -> 66.7% (-16.67 points)
- DROP cuad/notice_to_terminate_renewal/grounding_rate: 97.1% -> 94.3% (-2.77 points)
- DROP cuad/renewal_term/grounding_rate: 96.7% -> 90.0% (-6.67 points)

