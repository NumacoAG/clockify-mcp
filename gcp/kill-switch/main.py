"""Cloud Function: disable billing on this project when the budget is exceeded.

Triggered by Pub/Sub messages from a Google Cloud Billing budget alert. The
function decodes the budget notification; if `costAmount > budgetAmount`, it
calls the Cloud Billing API to detach the billing account from the project,
which stops all paid resources from accruing further charges.

Deployed as a 2nd-gen Cloud Function in this project. Runtime SA needs
roles/billing.projectManager on the billing account.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import functions_framework
from googleapiclient import discovery

PROJECT_ID = os.environ.get("PROJECT_ID", "")
PROJECT_NAME = f"projects/{PROJECT_ID}"


@functions_framework.cloud_event
def stop_billing(cloud_event: Any) -> None:
    pubsub_data = _decode(cloud_event)
    cost = float(pubsub_data.get("costAmount", 0))
    budget = float(pubsub_data.get("budgetAmount", 0))
    threshold = pubsub_data.get("alertThresholdExceeded")

    print(
        f"Budget alert received: project={PROJECT_ID}, "
        f"costAmount={cost}, budgetAmount={budget}, threshold={threshold}"
    )

    if budget <= 0 or cost <= budget:
        print(f"No action: cost ({cost}) <= budget ({budget})")
        return

    billing = discovery.build("cloudbilling", "v1", cache_discovery=False)
    project_info = billing.projects().getBillingInfo(name=PROJECT_NAME).execute()
    if not project_info.get("billingEnabled"):
        print("Billing already disabled — nothing to do.")
        return

    billing.projects().updateBillingInfo(
        name=PROJECT_NAME, body={"billingAccountName": ""}
    ).execute()
    print(
        f"BILLING DISABLED on {PROJECT_NAME} (cost {cost} > budget {budget}). "
        "Re-enable manually in the Billing console when ready."
    )


def _decode(cloud_event: Any) -> dict[str, Any]:
    raw = cloud_event.data["message"]["data"]
    return json.loads(base64.b64decode(raw).decode("utf-8"))
