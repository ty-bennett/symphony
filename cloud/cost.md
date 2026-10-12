# Symphony cost estimates

AWS list prices, us-east-1, October 2026. Nothing in the cloud runs while idle;
Soloist answers on the box at no per-request cost.

| Item | Unit | Estimate |
| --- | --- | --- |
| Tenant cloud baseline | per business per month | $13–20 |
| Symphony shared accounts (split over 10 businesses) | per business per month | $2–4 |
| Jetson gateway, app, Soloist | per request | $0 |
| Document question (Orchestra agent, Kimi K2.5) | per request | ~$0.013 |
| Web page via Orchestra's browser | per page | ~$0.03 |
| Analysis in Code Interpreter | per request | ~$0.02 |
| Frontier step-up (Opus 4.5) | per request | ~$0.065 |
| Coding job on Orchestra, Kimi K2.5 (assumed 10 turns × 25k input) | per job | ~$0.18 |
| Coding job on Orchestra, Opus 4.5 (same size) | per job | ~$1.65 (prompt caching could cut ~60%) |
| New cloud pieces (chat and admin endpoints) | per request | under $0.0002 |
| Cognito sign-in | per user per month | under $0.02 |
| Typical seat | per seat per month | $10 |
| Heavy coding seat (+40 coding jobs a month) | per seat per month | ~$17 |
| Onboarding labor | per business, one time | $600–1,000 |
| Jetson: Orin Nano Super / Orin NX 16GB / AGX Orin | per device, one time | ~$500 / $1,300–1,500 / $3,700 |

Totals (defined 10-seat typical-usage scenario):

- Recurring monthly: **$115–124/month** = $13–20 tenant cloud baseline + $2–4 shared accounts + (10 seats × $10/month).
- One-time onboarding + device: **$1,100–1,500** with an Orin Nano Super, **$1,900–2,500** with an Orin NX 16GB, or **$4,300–4,700** with an AGX Orin = $600–1,000 onboarding labor + device cost.
- Heavy coding seats are not included in the typical-seat total; budget approximately **$17/month per heavy coding seat** instead of $10/month.
