# Billing kill switch

A Cloud Function that disables billing on this GCP project when a Cloud Billing
budget alert says the cost has exceeded the budget. Triggered via Pub/Sub from
the budget itself.

When billing is disabled, all paid resources (Cloud Run, Cloud Build, etc.)
stop accruing charges. Re-enable manually in the Billing console once you've
fixed whatever caused the overrun.

## Files

- `main.py` — the function (`stop_billing`).
- `requirements.txt` — minimal deps.

## Deploy

See the main repo `README.md` for the end-to-end walk-through. Summary:

1. Create a Pub/Sub topic (`billing-kill-switch`).
2. Deploy this function with `--trigger-topic=billing-kill-switch`.
3. Grant the function's runtime service account `roles/billing.projectManager`
   on the billing account.
4. Configure the existing budget alert (Billing → Budgets) to publish to that
   Pub/Sub topic.
