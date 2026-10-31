# Interview v4 deployment
Use a new Aiven database and telemetry topic. The original Railway deployment and data are not migrated or reset. v3 plans can contain exceptions rejected by v4; load a fresh scenario.

Render deploys branch `codex/interview-render-aiven`. Secrets stay in hosting settings. Reset cascades active demo state, SQL telemetry history and receipts; old Kafka messages are fenced by run ID.
