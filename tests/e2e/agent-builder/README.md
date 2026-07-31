# Agent Builder Direct-Edit E2E

These tests require a running Nodease stack and an explicitly provisioned demo
scope. They never store login credentials or resource IDs in the repository.
The suite covers direct GraphMutation save/acknowledgement, dedicated Knowledge
selection, canonical draft metadata, structured graph edits, and response-loss
recovery. Legacy Preview and apply/save routes are intentionally not exercised.

Required environment variables:

- `NODEASE_E2E_EMAIL`
- `NODEASE_E2E_PASSWORD`
- `NODEASE_E2E_ORGANIZATION_ID`
- `NODEASE_E2E_WORKFLOW_ID`
- `NODEASE_E2E_STRUCTURED_EDIT_WORKFLOW_ID`

Optional:

- `NODEASE_BASE_URL` (defaults to `http://localhost`)

Run sequentially:

```powershell
npm install
npm test
```

Generated authentication state, traces, screenshots, and reports must remain
untracked.
