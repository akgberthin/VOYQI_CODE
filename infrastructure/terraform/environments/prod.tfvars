project_id  = "voyqi-mobility-prod"
region      = "us-east4"
environment = "prod"

general_machine_type      = "e2-standard-4"
ai_machine_type           = "n2-standard-8"
ai_realtime_machine_type  = "n2-standard-4"

authorized_networks = [
  # CI/CD deploy runner + break-glass admin bastion CIDRs go here
  # (populated per SAP-004 / SEC-001 break-glass access policy)
]
