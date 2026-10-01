# Evidence and verification

Every judgment Aegis makes is backed by evidence: prompts, model outputs, sources, traces, tool calls, metric and evaluator results, policy excerpts and configuration snapshots.

## Integrity model

- **Content hash** — `sha256(canonical_json({kind, title, content, …}))` for each record.
- **Hash chain** — `chain_hash = sha256((previous chain_hash or "GENESIS") + ":" + content_hash)`. Chains are per audit and, for Runtime Guard decisions, per system. The audit stores its final chain head (`evidence_head_hash`).
- **Append-only storage** — PostgreSQL triggers reject `UPDATE` and `DELETE` on evidence (and on the audit log and usage ledger). The application role cannot bypass them; only a purge path run by a non-application role may remove content under a retention policy, and purged records stay in the chain as placeholders so verification reports them instead of breaking.
- **Masking** — sensitive content is masked in the console and API by default; revealing it requires the `evidence:reveal` permission and is audit-logged.

## Verifying in Aegis

- **Evidence → Integrity** recomputes the chains of the most recent completed audits.
- **Audit → Evidence** shows the chain status for one audit: records, content hashes recomputed, computed vs. recorded head, and any problems.
- API: `GET /audits/{id}/evidence/verify`, `GET /evidence/integrity`, `GET /systems/{id}/runtime/verify`.

Statuses: `VERIFIED`, `TAMPERED` (a hash, link or signature does not match), `INCOMPLETE` (something listed is missing), `UNSIGNED` (integrity holds but no verifiable signature), `EMPTY`, `PENDING`.

## Exporting a signed package

`POST /audits/{id}/evidence/export` (permission `evidence:export`; included in all plans, counted against the plan's export quota) returns `aegis-evidence-<audit>-<timestamp>.zip`:

```
<prefix>/manifest.json        format aegis.evidence-package.v1, audit, chain head, file digests, root hash
<prefix>/manifest.sig.json    Ed25519 signature over the canonical manifest, key id, public key
<prefix>/evidence/*.json      one file per evidence record
<prefix>/findings.json        findings observed by the audit
<prefix>/controls.json        controls assessed
<prefix>/report.json          the audit report (when generated)
<prefix>/VERIFY.md            how to verify
<prefix>/verify.py            the standalone verifier (standard library + optional `cryptography`)
```

The response headers carry `X-Aegis-Root-Hash` and `X-Aegis-Export-Id`; every export is listed under **Evidence → Exports**.

## Verifying offline

```bash
unzip -j aegis-evidence-<audit>.zip '*/verify.py'
python3 verify.py aegis-evidence-<audit>.zip --public-key <base64 public key>
```

The verifier checks file digests, content hashes, the hash chain, the root hash and the signature, prints a JSON report and exits `0` only for `VERIFIED`. Obtain the public key out of band (**Evidence → Verify a package** shows it, or `GET /evidence/signing-key`); passing it with `--public-key` proves the package was signed by *your* deployment rather than by whoever produced the zip. You can also upload a package under **Evidence → Verify a package** — the server runs the same code.

## Signing key

Set `EVIDENCE_SIGNING_KEY` (urlsafe base64 of a 32-byte Ed25519 seed) in production; the API refuses to start without it. In development a deterministic key is derived and flagged as a development key everywhere it is shown. Rotating the key changes the public key: keep a record of previous public keys (key ids are shown on every export) so older packages remain verifiable.

## What verification does and does not mean

A `VERIFIED` package shows that the records are unchanged since they were captured and that your deployment signed them. It does not attest that the tests were sufficient, that the system is safe, or that it complies with any regulation.
