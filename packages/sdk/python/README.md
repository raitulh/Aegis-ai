# aegis-ai (Python SDK)


## Scientist Lab

```python
from aegis_ai import Aegis

with Aegis(api_key="aegis_...", base_url="http://localhost:8000") as client:
    mission = client.lab.missions.create(
        project_id=project_id,
        title="Annealing vs random search",
        objective="Determine whether simulated annealing beats random search on 5-D Rastrigin.",
    )
    client.lab.missions.launch(mission["id"], idempotency_key="launch-001")  # safe to retry
    for event in client.lab.missions.stream_events(mission["id"]):  # SSE, resumable
        print(event["id"], event["event_type"], event["message"])
    for claim in client.lab.claims.list(mission_id=mission["id"]):
        print(claim["status"], claim["statement"])
    client.lab.experiments.reproducibility_package(experiment_id, "package.zip")
```

Namespaces: `missions`, `hypotheses`, `experiments`, `claims`, `discoveries`, `approvals`, `research`, `memory`,
`knowledge`, `artifacts`, `strategies`, `reports`, plus `usage()` and `workflow_run()`.

API keys act as automation: they can create missions up to autonomy L1 and launch them; raising autonomy,
deciding approvals, reviewing discoveries or memories and promoting strategies require an interactive human.
POSTs are retried only when safe (connection failures, or with an `Idempotency-Key`).
