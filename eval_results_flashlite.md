# Evaluation results

5 run(s) per case, live model, deterministic scoring.

| case | run | status ok | escalation exact | cites ok | shipped issues | fabricated ids | self-repaired | pass |
|---|---|---|---|---|---|---|---|---|
| TC001 | 1 | yes | yes | yes | 0 | 0 | - | yes |
| TC001 | 2 | yes | yes | yes | 0 | 0 | - | yes |
| TC001 | 3 | yes | yes | yes | 0 | 0 | - | yes |
| TC001 | 4 | yes | yes | yes | 0 | 0 | - | yes |
| TC001 | 5 | yes | yes | yes | 0 | 0 | - | yes |
| _TC001: Should retrieve EQ001, RF101, H101-H103, SOP001 and recommend escalation._ |||||||||
| TC002 | 1 | yes | yes | yes | 0 | 0 | - | yes |
| TC002 | 2 | yes | yes | yes | 0 | 0 | - | yes |
| TC002 | 3 | yes | yes | yes | 0 | 0 | - | yes |
| TC002 | 4 | yes | yes | yes | 0 | 0 | - | yes |
| TC002 | 5 | yes | yes | yes | 0 | 0 | - | yes |
| _TC002: Should ask or infer alarm if possible; retrieve CMP205 context when matched to pressure alarm._ |||||||||
| TC003 | 1 | yes | yes | yes | 0 | 0 | - | yes |
| TC003 | 2 | yes | yes | yes | 0 | 0 | - | yes |
| TC003 | 3 | yes | yes | yes | 0 | 0 | yes | yes |
| TC003 | 4 | yes | yes | yes | 0 | 0 | yes | yes |
| TC003 | 5 | yes | yes | yes | 0 | 0 | yes | yes |
| _TC003: Should retrieve GAS012, CVD maintenance and SOP; escalate due to high severity and downtime._ |||||||||
| TC004 | 1 | yes | yes | yes | 0 | 0 | yes | yes |
| TC004 | 2 | yes | yes | yes | 0 | 0 | yes | yes |
| TC004 | 3 | yes | yes | yes | 0 | 0 | - | yes |
| TC004 | 4 | yes | yes | yes | 0 | 0 | - | yes |
| TC004 | 5 | yes | yes | yes | 0 | 0 | - | yes |
| _TC004: Should avoid over-escalation; recommend camera clean/calibration._ |||||||||
| TC005 | 1 | yes | - | - | 0 | 0 | - | yes |
| TC005 | 2 | yes | - | - | 0 | 0 | - | yes |
| TC005 | 3 | yes | - | - | 0 | 0 | - | yes |
| TC005 | 4 | yes | - | - | 0 | 0 | - | yes |
| TC005 | 5 | yes | - | - | 0 | 0 | - | yes |
| _TC005: Should handle missing equipment/alarm gracefully and request clarification._ |||||||||
| CUST-A | 1 | yes | yes | NO | 0 | 0 | - | NO |
| CUST-A | 2 | yes | yes | NO | 0 | 0 | - | NO |
| CUST-A | 3 | yes | yes | yes | 0 | 0 | - | yes |
| CUST-A | 4 | yes | yes | NO | 0 | 0 | - | NO |
| CUST-A | 5 | yes | yes | NO | 0 | 0 | - | NO |
| _CUST-A: unknown alarm code on a known equipment -> corrected from the incident record with an explicit note, not guessed_ |||||||||
| CUST-B | 1 | yes | yes | - | 0 | 0 | - | yes |
| CUST-B | 2 | yes | yes | - | 0 | 0 | - | yes |
| CUST-B | 3 | yes | yes | - | 0 | 0 | - | yes |
| CUST-B | 4 | yes | yes | - | 0 | 0 | - | yes |
| CUST-B | 5 | yes | yes | - | 0 | 0 | - | yes |
| _CUST-B: minimal information -> all facts recovered from the incident record_ |||||||||
| CUST-C | 1 | yes | - | - | 0 | 0 | - | yes |
| CUST-C | 2 | yes | - | - | 0 | 0 | - | yes |
| CUST-C | 3 | yes | - | - | 0 | 0 | - | yes |
| CUST-C | 4 | yes | - | - | 0 | 0 | - | yes |
| CUST-C | 5 | yes | - | - | 0 | 0 | - | yes |
| _CUST-C: known equipment without an open incident -> ask for incident details_ |||||||||

**36/40 passed.**
