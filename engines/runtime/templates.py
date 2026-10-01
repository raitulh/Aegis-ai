"""Built-in runtime policy templates (the Policy Library).

Templates are starting points. Using one creates a workspace policy the customer owns, edits, versions and
publishes. A template is a technical control, not a compliance attestation: adopting one does not make a
system compliant with any regulation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    category: str
    summary: str
    source: str


TEMPLATES: tuple[Template, ...] = (
    Template(
        key="prevent-sensitive-exfiltration",
        name="Prevent sensitive data exfiltration",
        category="data_protection",
        summary="Confidential or PII-bearing data sent to external destinations requires approval; restricted data is blocked.",
        source="""name: Prevent sensitive data exfiltration
description: Confidential data must not leave the organization without a human decision.
params:
  internal_domains: []   # add your own domains, e.g. [acme.example]
rules:
  - id: restricted-external-block
    description: Restricted data never leaves the organization.
    when:
      event: [network.request, tool.call, mcp.tool.call]
      destination: external
      data_classification: restricted
    action: block
    severity: critical
    message: Restricted data cannot be sent to an external destination.
  - id: confidential-external-approval
    when:
      event: [network.request, tool.call, mcp.tool.call]
      destination: external
      data_classification: confidential
    action: require_approval
    severity: high
    message: Confidential data sent outside the organization requires approval.
  - id: pii-external-flag
    when:
      event: [network.request, tool.call, mcp.tool.call]
      destination: external
      contains_pii: true
    action: flag
    severity: medium
    message: Personal data was sent to an external destination.
""",
    ),
    Template(
        key="block-secret-leakage",
        name="Block secret leakage",
        category="secrets",
        summary="Credentials or keys in tool arguments, outbound requests or model output are blocked.",
        source="""name: Block secret leakage
description: Detected credentials must never leave the agent.
rules:
  - id: secret-in-outbound-call
    when:
      event: [network.request, tool.call, mcp.tool.call]
      contains_secret: true
    action: block
    severity: critical
    message: A credential was detected in an outbound call.
  - id: secret-in-model-output
    when:
      event: model.response
      contains_secret: true
    action: flag
    severity: high
    message: A credential appeared in model output.
""",
    ),
    Template(
        key="human-approval-for-irreversible-actions",
        name="Human approval for irreversible actions",
        category="human_approval",
        summary="Destructive database statements and file deletions require a human decision.",
        source="""name: Human approval for irreversible actions
description: Actions that cannot be undone require a person in the loop.
rules:
  - id: destructive-sql
    when:
      event: database.query
      statement: [delete, drop, truncate, alter]
      approved: false
    action: require_approval
    severity: high
    message: Destructive database statements require approval.
  - id: file-delete
    when:
      event: file.write
      conditions:
        - {field: payload.operation, op: in, value: [delete, remove, unlink]}
      approved: false
    action: require_approval
    severity: high
    message: Deleting files requires approval.
""",
    ),
    Template(
        key="tool-allowlist",
        name="Tool allowlist",
        category="tool_access",
        summary="Only explicitly listed tools may be called; anything else is blocked.",
        source="""name: Tool allowlist
description: Agents may only call approved tools. Edit the list before publishing.
rules:
  - id: unlisted-tool
    when:
      event: [tool.call, mcp.tool.call]
      conditions:
        - {field: tool, op: not_in, value: [search, read_document, summarize]}
    action: block
    severity: high
    message: This tool is not on the approved list.
""",
    ),
    Template(
        key="filesystem-boundaries",
        name="Filesystem boundaries",
        category="agent_access",
        summary="File reads and writes outside the workspace directory are blocked.",
        source="""name: Filesystem boundaries
description: Agents may only touch files under the permitted workspace path.
rules:
  - id: outside-workspace
    when:
      event: [file.read, file.write]
      conditions:
        - {field: payload.path, op: exists, value: true}
        - {field: payload.path, op: matches, value: "^(?!/workspace/)"}
    action: block
    severity: high
    message: File access outside /workspace/ is not permitted.
""",
    ),
    Template(
        key="production-network-egress",
        name="Production network egress review",
        category="network",
        summary="Flags every external network request made by production agents.",
        source="""name: Production network egress review
description: Visibility into what production agents reach on the internet.
params:
  internal_domains: []
rules:
  - id: prod-external-request
    when:
      event: network.request
      environment: production
      destination: external
    action: flag
    severity: low
    message: Production agent made an external network request.
""",
    ),
    Template(
        key="mcp-tool-governance",
        name="MCP tool governance",
        category="mcp",
        summary="MCP tools that write or execute require approval; MCP calls carrying secrets are blocked.",
        source="""name: MCP tool governance
description: Govern Model Context Protocol tool use.
rules:
  - id: mcp-secret
    when:
      event: mcp.tool.call
      contains_secret: true
    action: block
    severity: critical
    message: An MCP tool call carried a credential.
  - id: mcp-mutating-tool
    when:
      event: mcp.tool.call
      conditions:
        - {field: tool, op: matches, value: "(write|delete|exec|run|shell|deploy)"}
      approved: false
    action: require_approval
    severity: high
    message: Mutating MCP tools require approval.
""",
    ),
    Template(
        key="pii-in-model-output",
        name="Personal data in model output",
        category="privacy",
        summary="Flags model responses that contain personal data.",
        source="""name: Personal data in model output
description: Visibility into personal data surfacing in model responses.
rules:
  - id: pii-response
    when:
      event: model.response
      contains_pii: true
    action: flag
    severity: medium
    message: The model response contained personal data.
""",
    ),
)

TEMPLATES_BY_KEY = {t.key: t for t in TEMPLATES}
