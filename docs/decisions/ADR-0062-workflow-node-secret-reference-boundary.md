# ADR-0062: Workflow node secret reference boundary

Status: Accepted

Related ADRs: [ADR-0045](ADR-0045-agent-builder-direct-edit-parameter-guidance.md), [ADR-0046](ADR-0046-agent-builder-graph-mutation-and-cas-save.md), [ADR-0057](ADR-0057-llm-credential-at-rest-encryption-and-rotation.md)

## Context

Slack Bot Token, Slack Incoming Webhook URL and GitHub API Token are node-owned
secrets. The product already exposes masked inputs for these values in Agent
Builder and Node Detail, but the previous save bridge placed plaintext in the
workflow graph. That made the value durable in the draft graph, deployment
snapshot, version history and any response or backup that copied the graph.

The direct input experience is intentional. It must not be replaced with a
credential picker, a Node Detail redirect or an instruction-only card. The
storage representation must change without removing that input path.

## Decision

1. Agent Builder keeps the masked Bot Token, Webhook URL and GitHub API Token
   inputs. An active task whose `defer_policy=allow_unresolved` keeps the
   explicit `나중에 설정` action. These controls are product requirements.
2. Plaintext exists only in the active input control and the authenticated
   secret-write request. It is never written to workflow graph data,
   Agent Builder messages/tasks/sessions, GraphMutation envelopes, audit,
   trace, log, version history or deployment snapshots.
3. Gateway encrypts a submitted value with a versioned keyring and creates an
   immutable workflow-node secret revision. The response returns only an
   opaque `workflow-node-secret://<uuid>` reference and configured state.
4. The graph field that previously held plaintext stores that opaque reference.
   Replacing a value creates a new revision. Existing deployments retain their
   previous immutable reference; using a replacement requires a new deployment.
5. A reference is scoped to its organization and workflow. Runtime resolves it
   immediately before provider I/O through a short database session, validates
   scope, status, node type and parameter key, copies plaintext into process
   memory, closes the session, then performs the external call.
6. Draft read, deployment read, export, audit and error responses never return
   ciphertext or plaintext. Safe failures use stable codes and do not reflect
   the submitted value.
7. Existing plaintext graph values are legacy data. A bounded migration path
   encrypts them and replaces them with references before the graph is returned
   or newly deployed. New writes may not recreate the legacy representation.
8. Secret clear/defer removes the graph reference but does not rewrite an
   immutable revision that may still be referenced by an older deployment.
   Unreferenced revision cleanup is a separate retention operation.
9. Draft save and deployment preflight bulk-load referenced revisions and
   validate active organization, workflow, node id, node type, parameter key
   and active status before accepting the graph. A validly formatted reference
   owned by another binding is rejected before runtime.
10. Legacy draft migration re-reads the Workflow under `FOR UPDATE` and
    transforms only that locked current graph. It never commits a graph that
    was read before a concurrent CAS save.
11. Node copy and paste remove Slack/GitHub workflow-node secret references
    from the copied node while preserving non-secret configuration. A copied
    node must obtain a new revision scoped to its new node id.
12. Until secret revisions carry a canonical nested container path, a graph
    containing the same secret-bearing node id/type/parameter identity more
    than once is rejected as ambiguous. This does not prohibit unrelated
    nested node ids; it prevents one revision from authorizing two locations.

This ADR supersedes only the plaintext graph persistence portions of ADR-0045
and ADR-0046. Their masked direct-input UX, `나중에 설정`, ParameterTask,
GraphMutation, CAS and acknowledgement contracts remain authoritative.

## Consequences

- Workflow graph hashes and CAS operate on opaque references, never plaintext.
- Agent Builder and Node Detail use the same secret-write API and storage
  service, preventing one editor path from restoring plaintext persistence.
- Deployment snapshots preserve deterministic secret revision semantics.
- Gateway and Workflow Engine require the same workflow-node secret keyring.
- PostgreSQL integration and authenticated browser verification remain required
  before this boundary can be marked complete.

## Non-Goals

- Replacing direct inputs with managed Slack/GitHub credential resources
- Displaying or hydrating an existing plaintext value
- Automatically rotating already deployed secret revisions
- Storing secret values in Agent Builder operation replay data
