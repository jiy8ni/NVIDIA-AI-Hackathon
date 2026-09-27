# HandoffOS implementation rules

- Preserve apps/web/openapi.yaml: 12 operations, /v1, camelCase, no response envelope.
- Evidence/RetrievalResponse v1 are frozen in packages/contracts. Do not require snapshotId, evidenceId, locator or extra upstream fields.
- Python owns read-only bounded synthesis. Node owns authentication, job state, checklists and progress.
- Never infer task assignment from author/owner, or dates from retrieval/update timestamps.
- Preserve opposing evidence. Unknowns remain null or confirmation questions.
- No external SaaS writes. HTTP-backed cached content requires current ACL validation; missing ACL fails closed.
- Native/NAT runtime and offline/Nemotron model mode are independent. Do not present fixtures as NVIDIA inference.
- Test with pytest, node --test services/api/access.test.mjs, and the web build. Optional NAT tests need the supported Python environment.
- Keep .env, .runtime, generated PDFs/screenshots, virtualenvs and node_modules out of Git.
