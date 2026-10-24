# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261024_162251, lease 20261024_162251, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.9% | 66.7% | 98.7% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.4 s |
| insurance_certificate/clean | 12 | 100.0% | 98.7% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.4 s |
| insurance_certificate/injection | 3 | 100.0% | 100.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 15.1 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 8.0 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 11.2 s |
| lease/all | 20 | 100.0% | 98.2% | 100.0% | 99.3% | 100.0% | 100.0% | 83.1% | 80.0% | $0.0017 | 15.9 s |
| lease/clean | 12 | 100.0% | 97.6% | - | 99.4% | 100.0% | 100.0% | 80.2% | 75.0% | $0.0018 | 16.1 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 11.8 s |
| lease/late_page | 2 | 100.0% | 100.0% | - | 96.4% | 100.0% | 100.0% | 81.2% | 100.0% | $0.0017 | 11.9 s |
| lease/missing_field | 3 | 100.0% | 97.6% | 100.0% | 100.0% | 100.0% | 100.0% | 77.8% | 66.7% | $0.0013 | 11.5 s |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 2 | 100.0% | 0.62 |
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 371 | 98.9% | 1.00 |

ECE: 0.010 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 7 | 57.1% | 0.70 |
| 0.7-0.9 | 2 | 100.0% | 0.88 |
| >=0.9 | 268 | 99.3% | 1.00 |

ECE: 0.010 over 277 fields.


### Top failure modes

- wrong_field: current_rent: 5
- action: 4
- brief_withheld: holdover_rent: 4
- brief_withheld: holdover_extra: 4
- brief_withheld: holdover_percent: 4
- wrong_field: type: 3
- brief_withheld: current_rent: 3
- brief_withheld: next_rent: 3

Failure log: `evals/results/failures_synthetic_20261024_162251.json`

- DROP synthetic/lease/all/action_correct: 85.0% -> 80.0% (-5.0 points)
- DROP synthetic/lease/clean/action_correct: 83.3% -> 75.0% (-8.33 points)
- DROP synthetic/lease/clean/usable_rate: 97.6% -> 95.2% (-2.38 points)

## generic (run 20261024_162316, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 93.0% | 93.0% | 100.0% | 100.0% | 66.7% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

Label guess right: 100.0%; field label right (of matched): 84.4%; email drafts proposed: 0; extra fields not in the ground truth: 3.

### Top failure modes

- missed_field: amount: 3
- extra_field: amount: 3

Failure log: `evals/results/failures_generic_20261024_162316.json`


## cuad (run 20261024_162550, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 55.6% | 80.8% | 57.2% | 100.0% | 100.0% | 77.3% (22) | 100.0% |
| expiration_date | 39/50 | 83.9% | 39.3% | 48.9% | 87.2% | 36.4% | 83.3% (6) | 97.4% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 31.3% | 45.0% | 92.9% | 63.9% | - | 97.2% |
| parties | 50/50 | 43.5% | 71.9% | 50.2% | 100.0% | - | - | 86.0% |
| renewal_term | 18/50 | 94.4% | 35.9% | 49.5% | 94.4% | 43.8% | - | 96.8% |
| all | 159/250 | 66.4% | 58.4% | 51.0% | 95.6% | 58.2% | 78.6% (28) | 95.1% |

Cost per contract $0.0014, 8603 tokens, p95 latency 4.2 s.

### Top failure modes

- low_overlap: parties: 23
- unlabeled_extraction: notice_to_terminate_renewal: 23
- low_overlap: expiration_date: 16
- unlabeled_extraction: renewal_term: 14
- unlabeled_extraction: effective_date: 12
- low_overlap: effective_date: 12
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 7

Failure log: `evals/results/failures_cuad_20261024_162550.json`

- DROP cuad/expiration_date/detection_rate: 92.3% -> 87.2% (-5.13 points)
- DROP cuad/expiration_date/precision: 89.3% -> 83.9% (-5.45 points)
- DROP cuad/renewal_term/recall: 38.1% -> 35.9% (-2.16 points)

