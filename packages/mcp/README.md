# aegis-mcp — MCP server for Aegis

Exposes Aegis to Model Context Protocol clients (assistants, IDEs, agent frameworks) so an agent can check an action with Runtime Guard before taking it, look up posture and findings, verify evidence, report changes and simulate policies.

```bash
pip install aegis-mcp          # or: pip install ./packages/sdk/python ./packages/mcp
AEGIS_API_KEY=aeg_live_... AEGIS_BASE_URL=https://aegis.example.com aegis-mcp
```

Client configuration:

```json
{
  "mcpServers": {
    "aegis": {
      "command": "aegis-mcp",
      "env": { "AEGIS_API_KEY": "aeg_live_...", "AEGIS_BASE_URL": "https://aegis.example.com" }
    }
  }
}
```

## Tools

| Tool | Purpose |
| --- | --- |
| `aegis_runtime_check` | Ask Runtime Guard for a decision before an action (`allow`, `flag`, `require_approval`, `block`) |
| `aegis_runtime_overview` | Runtime events and decisions over the last N hours |
| `aegis_get_risk_posture` | Workspace posture and dimension scores |
| `aegis_list_systems` | Registered AI systems |
| `aegis_create_audit`, `aegis_get_audit`, `aegis_run_test` | Start and inspect audits |
| `aegis_get_findings`, `aegis_get_evidence` | Findings (filterable) and their evidence |
| `aegis_verify_evidence` | Recompute an audit's evidence chain |
| `aegis_check_policy` | Controls of a compliance policy |
| `aegis_report_change` | Report a deployment / prompt / model / tool change (starts risk-selected tests) |
| `aegis_simulate_policy` | Replay recorded events through a runtime policy version |

## Security model

The server holds no credentials or privileges of its own: every tool call is an API request made with `AEGIS_API_KEY`, so the key's role, scopes and workspace bound what the MCP client can do. Give it the narrowest key that works (for example a `viewer` key with the `read` and `runtime` scopes). Tool inputs are length-bounded; tool outputs are JSON summaries of API responses.
