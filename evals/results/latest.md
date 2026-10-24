# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261024_172130, lease 20261024_172130, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.7% | 66.7% | 98.7% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.5 s |
| insurance_certificate/clean | 12 | 100.0% | 98.3% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 12.5 s |
| insurance_certificate/injection | 3 | 100.0% | 100.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 10.2 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.3 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 9.5 s |
| lease/all | 20 | 100.0% | 97.1% | 100.0% | 100.0% | 100.0% | 100.0% | 76.0% | 70.0% | $0.0018 | 18.1 s |
| lease/clean | 12 | 100.0% | 97.0% | - | 100.0% | 100.0% | 100.0% | 75.0% | 66.7% | $0.0019 | 18.1 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 9.5 s |
| lease/late_page | 2 | 100.0% | 96.4% | - | 100.0% | 100.0% | 100.0% | 68.8% | 50.0% | $0.0025 | 20.0 s |
| lease/missing_field | 3 | 100.0% | 95.2% | 100.0% | 100.0% | 100.0% | 100.0% | 55.6% | 66.7% | $0.0013 | 9.9 s |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 1 | 100.0% | 0.62 |
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 372 | 98.7% | 1.00 |

ECE: 0.011 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 10 | 50.0% | 0.70 |
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 267 | 98.9% | 1.00 |

ECE: 0.017 over 277 fields.


### Top failure modes

- wrong_field: current_rent: 8
- action: 6
- brief_withheld: current_rent: 5
- brief_withheld: next_rent: 5
- brief_withheld: holdover_rent: 5
- brief_withheld: holdover_extra: 5
- brief_withheld: holdover_percent: 5
- wrong_field: type: 4

Failure log: `evals/results/failures_synthetic_20261024_172130.json`

- DROP synthetic/lease/all/action_correct: 85.0% -> 70.0% (-15.0 points)
- DROP synthetic/lease/all/brief_doc_correct: 65.0% -> 60.0% (-5.0 points)
- DROP synthetic/lease/all/brief_line_correct: 81.2% -> 76.0% (-5.19 points)
- DROP synthetic/lease/clean/action_correct: 83.3% -> 66.7% (-16.67 points)
- DROP synthetic/lease/clean/brief_doc_correct: 66.7% -> 58.3% (-8.33 points)
- DROP synthetic/lease/clean/brief_line_correct: 81.2% -> 75.0% (-6.25 points)
- DROP synthetic/lease/clean/usable_rate: 97.6% -> 95.2% (-2.38 points)
- DROP synthetic/lease/injection/usable_rate: 100.0% -> 97.6% (-2.38 points)
- DROP synthetic/lease/late_page/action_correct: 100.0% -> 50.0% (-50.0 points)
- DROP synthetic/lease/late_page/brief_line_correct: 81.2% -> 68.8% (-12.5 points)
- DROP synthetic/lease/late_page/field_accuracy: 100.0% -> 96.4% (-3.57 points)
- DROP synthetic/lease/late_page/field_accuracy_expected_present: 100.0% -> 96.4% (-3.57 points)

## generic (run 20261024_172154, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

Label guess right: 100.0%; field label right (of matched): 88.6%; email drafts proposed: 0; extra fields not in the ground truth: 0.

### Top failure modes

- none

Failure log: `evals/results/failures_generic_20261024_172154.json`


## cuad (run 20261024_172416, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 55.7% | 79.5% | 56.8% | 100.0% | 100.0% | 87.0% (23) | 100.0% |
| expiration_date | 39/50 | 83.9% | 38.9% | 48.3% | 87.2% | 36.4% | 83.3% (6) | 97.4% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 29.1% | 42.4% | 92.9% | 55.6% | - | 100.0% |
| parties | 50/50 | 43.9% | 74.8% | 50.4% | 100.0% | - | - | 84.0% |
| renewal_term | 18/50 | 94.4% | 37.4% | 50.3% | 94.4% | 37.5% | - | 100.0% |
| all | 159/250 | 66.6% | 58.9% | 50.7% | 95.6% | 52.7% | 86.2% (29) | 95.5% |

Cost per contract $0.0014, 8603 tokens, p95 latency 3.7 s.

### Top failure modes

- low_overlap: parties: 25
- unlabeled_extraction: notice_to_terminate_renewal: 20
- low_overlap: expiration_date: 17
- unlabeled_extraction: effective_date: 12
- unlabeled_extraction: renewal_term: 12
- low_overlap: effective_date: 12
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 8

Failure log: `evals/results/failures_cuad_20261024_172416.json`

- DROP cuad/expiration_date/detection_rate: 92.3% -> 87.2% (-5.13 points)
- DROP cuad/expiration_date/precision: 89.3% -> 83.9% (-5.45 points)

