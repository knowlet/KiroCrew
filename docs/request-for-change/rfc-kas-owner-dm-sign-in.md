---
title: Owner-only KAS device sign-in from Telegram and Slack
status: draft
author: knowlet
created: 2026-11-03
last-audited: 2026-11-03
audited-at: 8b9aeceeef98a03a491d3547f2b71632d06c1765
doc-pr: null
implementation-prs: []
tracking-issues: [26042]
supersedes: []
superseded-by: []
---

# RFC: Owner-only KAS device sign-in from private messages

> **Draft; nothing proposed here is implemented or approved.**
> This is a design-only response to [#26042][issue] and the maintainer's
> [request-for-change guidance][invitation]. The issue remains `tier:T3` and
> `parked`; neither this document nor its pull request claims implementation
> ownership, changes that priority, or closes the issue. All behavior described
> under Design is proposed. No live Telegram, Slack, or provider approval flow
> was run for this RFC.

## Summary

An installation owner operating a remote gateway from a phone should be able to
start KAS account sign-in without a terminal on the gateway. Propose a narrow,
explicit control command in an authenticated, owner-bound, one-to-one Telegram
or Slack DM. After a fresh confirmation, the gateway starts the existing device
flow, sends its short-lived verification link and user code to that same DM,
and owns polling, cancellation, expiry, and persistence through
`KasLoginService` and `TokenStore`. Human approval remains on the issuer's page.

Recommend direct device-code delivery for the headless use case, subject to
maintainer acceptance of the owner binding and chat-retention boundary. A link
to the existing authenticated sign-in UI is the alternative, not a new way to
mint dashboard access. Neither approach is a wrapper around shell commands or
an agent tool. The first implementation must include shared login coordination;
a chat-only lock would not cover the existing dashboard and CLI writers.

The initial scope is one operator, explicit opt-in, supported device-flow
providers, and a running gateway whose messaging transport remains available
while KAS is signed out. Account switching, owner enrollment through chat,
external-IdP loopback flows, and enterprise boot-policy changes are excluded.
The decisions in Open questions are prerequisites, not delegated implementation
choices.

## Motivation and current code

### Evidence snapshot

The source references below are pinned to upstream
`8b9aeceeef98a03a491d3547f2b71632d06c1765`. Code-search results were used only to
find files; the cited source was read at this snapshot. The PR description
records fork/branch provenance separately from this technical design.

| Existing integration point | Observed behavior and consequence for this design |
|---|---|
| [Telegram command catalog][tg-commands], `parse_command` | The inspected command catalog does not register `/login`. Bare `/cancel` is an alias for stopping a turn; this proposal uses `/login cancel` instead. |
| [Telegram dispatcher][tg-dispatch], `TelegramDispatcher` | The gateway owns the dispatcher, bot identity, transport, and conversation services. The credential control must be intercepted before conversation routing, queuing, and model work, not implemented as a prompt. |
| [Telegram transport][tg-transport], `TelegramInboundMessage` | Ordinary admission uses allowed user IDs and can admit configured forum topics. Its normalized message has a default `chat_type` of `private`; a missing raw chat type must not inherit that default as proof for sign-in. |
| [Telegram session resume][tg-owner], `TelegramSessionResume.is_owner` | A stricter precedent requires exactly one configured operator, an explicit private chat, and `chat_id == user_id == owner_id`. This is useful input to an owner binding, not proof that an arbitrary allowlist entry owns the installation. |
| [Slack events][slack-events], `init_socket_mode` | The gateway installs the configured owner ID and performs its existing workspace/enterprise admission before Socket Mode dispatch. Messages, interactive events, and slash commands have distinct entry paths. |
| [Slack access][slack-owner], `is_owner` | The existing predicate accepts the owner ID and a W/U-prefix cross-match. A credential-changing command must use a platform-verified canonical ID in the bound workspace, not infer identity equivalence from that string substitution. |
| [Native Slack pre-turn dispatch][slack-dispatch] and [transport dispatch][slack-transport] | Linked-thread routing and hooks can precede ordinary command handling. A late addition to the bang-command router alone does not establish that auth controls never reach a transcript or a model. Both paths need the same early interceptor. |
| [Messaging admission][messaging-identity], `channel_inbound_permitted` | Channel governance is independent of the KAS credential being recovered. Retain channel admission; do not require an already authenticated model turn to run the login control. |
| [Shared Crew owner][shared-owner], `is_owner_principal` | Local bootstrap and tailnet owner principals are not a verified Telegram/Slack account link. Do not manufacture a local-owner principal from a chat-supplied ID. |
| [Dashboard KAS handlers][dashboard-auth], `_service`, `_require_owner` | Owner-gated mutations use a lazily created `app["kas_login_service"]`. The proposed gateway-owned factory must remain lazy and work without a dashboard request; it must not fabricate an HTTP owner session. |
| [KAS service: begin and poll][service-begin] | `begin_device` supports social, Builder ID, and IdC device flows and returns `login_id`, `user_code`, `verification_uri_complete`, and expiry. It does not bind those attempts to a chat actor or expose a polling interval. |
| [KAS service: persistence][service-save] and [cancel/logout][service-cancel] | A per-service lock orders cancellation against save; a store-wide sign-out generation guards against another process signing out. Sign-in generation is bumped before saving. These are valuable existing invariants, not a full-duration, cross-process sign-in lease. |
| [OIDC device helper][oidc] | The authorization contains `interval_secs`, but `poll_token_once` maps both `authorization_pending` and `slow_down` to `None`. A new polling owner needs that distinction preserved through the service, rather than guessing the cadence. |
| [Token store][store] | KAS account credentials use the dedicated encrypted vault rooted under the data home's `kas` directory. Reuse its ownership, link refusal, locking, and write behavior; add no second credential store. |
| [Existing sign-in component][login-ui] | `KasLoginGate` already distinguishes browser-on-loopback from device flow and has a fixed device poll interval. Its UI and owner-authenticated backend are reuse points, not evidence that a phone deep link or new handoff capability already exists. |
| [KAS route resolver][kas-route] | The current bundle/relay decision includes configured choices, CLI availability, overrides, and sign-in state. Do not copy the older assumption that `agent.kas_bundle` alone decides whether a vault sign-in can serve the selected route. |

[PR #23657][remote-pr] is merged and supplies CLI/cloud remote vault sign-in;
it does not supply this owner-DM command. Its recorded live check exercised
Builder ID registration and authorization start, not human approval. The
[KAS auth specification][auth-spec] separately reports a social-device proof of
concept; that is not a Telegram/Slack integration test.
[PR #25608][generation-pr] is merged and supplies the sign-in-generation
fingerprint behavior that this design preserves. Work on remote **MCP server**
sign-in retention, such as [#28630][mcp-pr], concerns a different credential
lifecycle and is not evidence that KAS **account** sign-in from a DM exists.

## Goals

Enable terminal-free account sign-in and recovery from a verified owner DM;
keep consent at the issuer; reuse the existing authentication and vault owners;
and make denial, concurrency, expiry, cancellation, and persistence outcomes
unambiguous. Every successful credential write must be attributable to one
still-authorized owner request, without a token entering chat or model context.

## Non-goals

No implementation in this PR; no change to `parked`; no `/claim`. No passwords,
API keys, refresh tokens, or callback URLs accepted in chat. No arbitrary shell,
LLM/MCP login tool, new auth provider, Discord support, group or channel login,
new dashboard bearer link, automatic owner pairing, or chat-triggered logout.
No account switching while a usable identity is present, multi-operator vault
management, or change to enterprise startup, harness selection, or governance.

## Design

### 1. Explicit commands and early dispatch

Proposed grammar, with `/login` on Telegram and `!login` on Slack:

```text
/login                         # help; no issuer request
/login start <provider>        # fresh, short-lived confirmation challenge
/login confirm <nonce>         # consume challenge and begin device authorization
/login status [attempt]        # read sanitized local state; never poll the issuer
/login cancel <attempt>        # cancel exactly this attempt; never log out
```

Provider names come from the existing device-flow service. For IdC, use an
owner-configured organization profile; do not accept an arbitrary start URL,
issuer, region override, or redirect target from chat. An unsupported provider
gets a local refusal, not a loopback fallback. A configured Slack slash-command
subcommand may share the controller only when it proves the same one-to-one
DM and sender; otherwise leave it unsupported in v1. Ephemeral delivery in a
shared channel is still a shared-channel invocation and is refused.

Install a narrow control interceptor after authenticated platform admission and
before hooks, linked-session forwarding, queues, transcript writes, or any
agent/readiness path. It consumes accepted, denied, and malformed login
commands, including when the feature is disabled. Neither an unknown subcommand
nor a pasted secret becomes an ordinary model prompt. Preserve existing bot
mention parsing and unrelated command semantics; do not normalize arbitrary
quoted text into commands. Auth replies use a dedicated typed renderer and the
verified destination, never a conversation's unified or later-rebound route.

The feature is off until the installation owner explicitly enables it through
a trusted owner-management surface. Channel policy still applies. A gateway
that cannot run messaging while signed out cannot be recovered by this design;
do not bypass an enterprise boot gate to make it work.

### 2. Owner private-DM authorization

Define an immutable verified actor from transport-owned metadata, not command
arguments, display names, forwarded authors, model output, or session keys.
Before **every** start, confirmation, status, cancel, and sensitive reply,
require an enabled feature, a readable current owner binding, an admitted
installation, and an exact private-peer match.

For Telegram, require the current authenticated bot identity; an explicit raw
`chat.type == private`; a human `from.id`; and
`chat.id == from.id == bound_owner_user_id`. Reject widget-origin, edited,
forwarded, anonymous, bot-origin, group, supergroup, and channel invocations.
The existing single-operator resume predicate can supply the configured
candidate. Zero/multiple candidates or absent raw fields fail closed; an
ordinary allowed-user match is not enough.

For Slack, bind the bot/app installation and workspace (and enterprise ID where
applicable), the exact canonical owner user ID, and the one-to-one DM peer.
Verify the conversation with the installation's authenticated platform client;
require `is_im`, the expected human peer, and no multi-person or shared-channel
context. Missing or contradictory data refuses before starting auth. A channel
ID prefix, `channel_type` alone, an allowlisted colleague, a trusted bot, or a
W/U-prefix guess is insufficient. Canonicalization that needs Slack lookup must
succeed under the same installation; no new broad OAuth scope is assumed.

The owner-management surface must confirm which existing Telegram/Slack account
is allowed to mutate this installation's KAS account. It must not infer that two
platform accounts belong to one person. A versioned binding is revoked on owner,
bot, workspace, or relevant policy changes; unreadable/degraded authorization
configuration is not replaced by permissive defaults or a stale boot copy.
No first-message enrollment is offered. Maintainers must choose the authoritative
binding location and writer before implementation (Q1).

### 3. Shared service and immutable attempt binding

A lazily initialized gateway login coordinator supplies the same
`KasLoginService` instance to the dashboard and both messaging adapters.
Separate-process callers use the same store coordination described below.
Construct it only after admission, not on the gateway socket-readiness path.
Gateway shutdown awaits coordinator cleanup and service `close`; a messaging-only
launch has the same lifecycle. Do not call private dashboard handlers from chat,
create an artificial owner HTTP request, or launch `kirocrew kas login` in a shell.

Bind each attempt to:

```text
installation/vault identity + process incarnation + owner-binding revision
platform + bot/app installation + workspace/enterprise where applicable
human user ID + exact DM ID + provider/organization profile
source event ID + random attempt ID + service login_id + expiry + state
```

Store device codes, client secrets, and pending authorization only in service
memory. The chat-visible attempt label is not an authorization credential;
knowing any ID alone never authorizes status or cancellation. Conversation
resets, slot reuse, unified DMs, or a second platform must not adopt an attempt.
Status reports only the caller's bound attempt, or an undetailed busy result.
After restart, old confirmations and attempts are unusable; require a fresh
request and new device authorization, not secret-bearing resume records.

### 4. Concurrency and the commit boundary

Permit one interactive account sign-in per vault, across Telegram, Slack,
dashboard device/loopback login, and CLI/cloud callers. A duplicate of the same
request returns its sanitized state; another start gets `login_busy` without
superseding or disclosing the active attempt. No implicit replace or takeover.

Extend the existing authentication/store owner with a small, non-secret,
store-wide attempt lease: a random fencing generation, owner-binding revision,
process incarnation, and bounded deadline. Reserve under the existing store
lock **before** issuer work, then release the lock during network waits.
Admission and all interactive credential commits must participate. The existing
sign-out lock/generation remains in place; it does not by itself serialize two
sign-ins whose approval arrives in different processes.

At persistence, under a defined lock order, revalidate the live lease,
uncancelled state, unexpired deadline, sign-out generation, current trusted
owner-binding revision, and expected preexisting vault identity. This comparison
and the vault mutation form one ordered commit. Owner-binding revocation writers
must participate in the same ordering; a check merely before an awaited poll is
not a sufficient revocation guarantee. Retain sign-in-generation bump and vault
persistence in the existing service path. Never save credentials in an adapter.

A crashed or expired holder can be replaced only with a new fencing generation;
a late response from the earlier holder cannot commit or release its successor's
lease. All supported process entry points must be upgraded together. Mixed old
writers or an unprovable coordination state are outside the supported opt-in
configuration, not silently declared safe. A hostile same-OS-user writer is
outside this boundary. Exact lease placement and cross-process lock ordering
are design-review decisions, with deterministic race tests required before ship.

A usable existing sign-in yields `already_signed_in`, with no provider request.
Recovery of a lapsed/refused identity requires explicit confirmation of its
provider and machine-wide effect. Keep the previous credential until the new
one commits; reject ambiguous multi-slot state or a changed vault generation
instead of choosing or deleting an account. Do not expose raw `replaces` in chat.
Same provider is **not** proof of the same account; the human must select the
intended account on the issuer page, and the confirmation must say this clearly.

### 5. Replay defense and confirmation

Platform authentication proves the sender, not freshness or exactly-once delivery.
Preserve Telegram update/message identifiers and Slack event/envelope identifiers
at ingress. Deduplicate on installation, platform, sender, DM, and source ID;
require bounded event age and reject edited/forwarded variants. A normalized
message lacking the needed provenance cannot start a login.

`start` issues a cryptographically random, single-use, 128-bit confirmation nonce
bound to the actor tuple, provider, originating message, and process incarnation.
The prompt names the installation and machine-wide sign-in effect. Proposed
confirmation lifetime: 60 seconds. Consume it atomically before the first issuer
request; retries cannot consume it twice. A replayed start after restart can at
most produce a new confirmation prompt, not a grant; the old confirmation has
no live nonce and fails. Text confirmation is sufficient for v1. Any later button
must carry a separate action-bound nonce and revalidate the actual clicker and
origin message, not trust callback text.

Keep bounded deduplication and terminal tombstones for at least the accepted
source-event age window plus maximum attempt lifetime. Capacity exhaustion
refuses new work rather than dropping a still-live replay guard. Rate-limit
confirmation/start requests per owner and installation; proposed initial bound
is three new device authorizations per ten minutes. These values are proposed
policy defaults, not claims about current code.

### 6. Polling, timeout, cancellation, and errors

One coordinator task owns polling. `status` reads state only, and repeated starts
or UI refreshes cannot spawn additional pollers. Preserve the provider's interval,
`slow_down`, and applicable retry-after advice through the service API; the current
OIDC single-poll return shape needs an additive change. Follow the device-flow
cadence in [RFC 8628 section 3.5][device-standard], including the increased
interval after `slow_down`. Do not copy the UI's fixed five-second timer or build
a second token-exchange implementation in the messaging adapter.

Set a monotonic local deadline from the smaller of the issuer's remaining TTL
and a proposed ten-minute local cap. Revalidate expiry before commit, including
a response arriving after the deadline. Network retries are bounded by that
same deadline and existing service transport-failure policy. Clock uncertainty
or contradictory timing fails closed. Expiry never starts a replacement flow.

```text
awaiting_confirmation -> starting -> pending -> committing -> authorized
          |                 |          |
          +-----------------+----------+-> cancelled | expired | failed
```

Cancel awaits the service/coordinator terminal transition, not just
`Task.cancel()`. If cancellation wins before persistence, no late poll may save.
If commit wins, report completion; do not falsely report cancellation or delete
the resulting credential. Cancellation and worker-thread persistence must remain
ordered even if the caller task is cancelled. Unknown/old attempt cancellation
is idempotent for the verified owner and cannot touch another attempt. Local
cancellation stops redemption and persistence; it does not promise revocation
of an already approved provider authorization.

Use stable, non-secret outcomes: `owner_only`, `private_dm_required`,
`owner_binding_unavailable`, `unsupported_provider`, `login_busy`,
`confirmation_expired`, `rate_limited`, `expired`, `cancelled`,
`auth_service_unreachable`, `authorization_failed`, `token_store_failed`, and
`signed_out_during_login`. These are proposed controller outcomes, not a claim
that each already exists. Map only allowlisted service fields; never echo an
exception, issuer body, token, account email, or raw command arguments.

A failed/uncertain delivery of the verification message cancels the pending
attempt and stops polling; it does not resend through a different DM, public
channel, or dashboard. A successful vault commit followed by notification
failure remains committed and discoverable through sanitized status. A storage
error is never an authorized result. Provider denial, expiry, and transient
transport failure remain distinct internally even where user wording is brief.

### 7. Secrets, histories, and audit

Access/refresh tokens, device codes, dynamic client secrets, PKCE material, and
vault keys never leave the auth subsystem for chat, logs, traces, model context,
or a durable messaging spool. The service's opaque `login_id` remains internal.
The user code and complete verification link are deliberately disclosed once
to the verified owner DM; they are still sensitive short-lived approval
material, not public data and not a KAS access token.

Accept only issuer-generated HTTPS verification URLs validated against the
selected provider's expected origins; reject userinfo, unexpected ports/origins,
and substituted callback targets. Show the authentic domain and expiry, disable
link unfurling/preview where supported, and never shorten the link. Revalidate
the actor/destination immediately before sending; never reuse a generic
conversation reply route or serialize the entire begin response.

Exclude both control messages and their replies from hooks, conversation logs,
queued turns, dashboard mirrors, memory/learning, LLM context, history backfill,
and diagnostic payload capture. The exclusion must survive a later history
fetch and gateway restart: retain non-secret control-message identifiers for
the history-reader retention window, or prove an equivalent conservative
classification at every reader. An in-memory flag on today's message is not
sufficient. Normal messages keep their existing history policy.

The reused auth path currently has logging/error sites that include issuer
response or exception text. Reuse is not evidence of secret-safe logging.
The implementation prerequisite is a scoped sweep of those begin/poll/persist
paths, replacing raw payload logging with allowlisted error codes and testing
canary secrets at every sink. A renderer-only redaction layer cannot establish
this invariant. This is a release requirement for the new surface, not a claim
of an independently reproduced vulnerability in an existing deployment.

SEL audit records only operation, pseudonymous/canonical actor reference,
installation, non-secret attempt correlation, state, and allowlisted outcome.
No URLs, codes, credentials, or full incoming payloads. Admission requires an
audit acceptance record; audit failure refuses a new remote login. If audit
fails after a credential is already committed, do not roll back or announce
failure of persistence: retain the terminal outcome, expose an audit-delivery
problem locally, and block new remote starts until the sink recovers. Status
and cancellation must remain available. Existing non-chat login audit behavior
is not silently changed by this proposal.

Telegram/Slack can retain messages in notifications, synchronized devices,
exports, or administrator systems. Best-effort removal/editing after completion
or expiry reduces exposure but is not a security guarantee. A compromised owner
chat account can initiate the same controls as the owner. Provider consent,
clear installation naming, and the fresh confirmation reduce confusion but do
not cryptographically prove that the person approving the issuer page is the
same person who sent the DM. This residual is part of Q2's decision.

## Direct delivery versus existing UI

| Option | Benefit | Boundary and cost |
|---|---|---|
| Direct device link/code in owner DM (recommended) | Works when the phone cannot reach a dashboard; the gateway needs no inbound callback port. Closest to the original problem. | Approval material enters the messaging platform. Requires the owner proof, history exclusions, polling ownership, and shared commit coordination above. |
| Non-secret locator to the existing sign-in UI | Reuses `KasLoginGate`, its provider chooser, and authenticated owner API; no device code need be posted in chat. | The phone must reach the configured HTTPS dashboard and independently authenticate as its owner. A missing browser session remains a prerequisite, not permission to mint a broad dashboard token in the reply. Mobile uses device flow, not the gateway's loopback callback. |
| New one-use UI handoff capability | Could reduce browser-login friction with narrower authority than a dashboard token. | Adds a new capability issuance/redemption protocol, audience and owner binding, anti-replay, and leakage controls. Not existing functionality; defer to a separate accepted design. |

The existing Telegram dashboard-link command mints a dashboard access token
([`_handle_dashboard`][tg-dashboard]); it is not account sign-in. Do not call it
implicitly from `/login`. A UI locator must use an owner-configured trusted
origin, not an arbitrary URL from chat. A stable sign-in deep link and mobile
navigation require explicit UI verification; this RFC does not claim them done.
Shell execution, token paste, automatic first-DM ownership, and treating a
channel allowlist as ownership are rejected alternatives.

## Security considerations

The protected asset is the **machine-wide KAS account credential**, not merely
one chat session. Trust the gateway's existing protected auth/vault boundary,
owner-management authority, authenticated platform transport, and issuer TLS.
Do not trust chat text, session metadata, bot output, stale configuration,
platform retries, link previews, or knowledge of an attempt ID as authority.

The proposed controls address unauthorized/non-private invocation, confused
routing, replay, concurrent account replacement, stale commits after revocation,
and accidental secret capture. They do not protect against a compromised gateway
OS account, malicious issuer, compromised messaging account/device, or a human
approving the wrong device/account. Account-subject equality is not inferred
from a shared profile ARN or provider name. No change widens sandbox visibility,
reads another credential store, bypasses provider consent, or enables a channel
forbidden by the existing governance policy.

## Migration plan

All phases below are **unstarted**. Phase 0 must finish before implementation.

| Phase | PR-sized work | Exit condition |
|---|---|---|
| 0: design decision | This standalone RFC and discussion on #26042. Resolve Q1-Q4, including whether direct delivery is acceptable. | A maintainer records the accepted scope and owner-proof source through the normal RFC process. `parked` is not that decision. |
| 1: shared auth prerequisites | Lazy service ownership; store-wide lease/fencing and revocation ordering; safe polling metadata; scoped error/log sanitization. Preserve existing dashboard/CLI behavior outside the coordinated sign-in contract. | Deterministic cross-process and cancellation tests pass; no participating credential writer bypasses the commit guard; auth spec updated in the same implementation commit. |
| 2: owner-DM adapters | Explicit opt-in/binding management, early Telegram and both Slack-path interception, nonce confirmation, typed delivery, history exclusions, status/cancel. Slash support only if the same proof is available. | Channel-negative, restart/replay, signed-out-gateway, no-dashboard, and secret-canary tests pass; messaging/Slack specs updated. |
| 3: controlled verification | Owner-approved live sign-in for each enabled provider and both transports, including cancellation and phone navigation. | Evidence names exact build, provider, transport, actual approval and vault result; unresolved providers remain disabled. No credentials or approval codes in evidence. |

Disabling the feature refuses new starts and cancels pending remote attempts
through the same fencing boundary. It does not sign out, delete existing vault
entries, or change other commands. Rollback must drain/invalidate active remote
attempts before removing their coordinator; do not let an old process redeem a
still-live attempt after downgrade. Non-secret lease/receipt metadata must not
make an older supported vault reader lose credentials.

## Backward compatibility

This PR changes documentation only. The proposed command is additive and opt-in;
existing `/cancel`, `/status`, dashboard access, and non-KAS routes retain their
meanings. Successful sign-in retains the existing fingerprint/session-retirement
contract. Shared interactive single-flight is an intentional future behavior
change: a second dashboard/CLI start can receive `login_busy` instead of racing.
That change and its full caller sweep require acceptance in Phase 0, not an
unannounced side effect of a messaging patch.

## Tests and acceptance conditions

The following are **required future tests**, not results of this RFC. Use isolated
homes/vaults, fake issuers and platform clients, injected clocks, bounded waits,
and barriers at race boundaries; no sleeps as synchronization or real credentials.

| Area | Required witness |
|---|---|
| Telegram ownership | Exact private owner passes; second allowlisted user, multiple candidates, wrong bot/chat, missing raw type, forwarded/edited/widget/bot message all deny before any issuer call. |
| Slack ownership | Exact verified workspace/owner/IM peer passes; W/U guess, other workspace, unknown conversation, MPIM, channel, shared context, bot and lookup failure deny. |
| Dispatch | Native and transport Slack paths, Telegram linked/resumed routes, and any slash entry consume auth commands before hooks/history/model work, even when disabled or KAS is signed out. |
| Binding | Wrong DM/platform/provider/owner revision and a leaked attempt ID cannot query or cancel another attempt; slot resets and unified DMs change nothing. |
| Replay | Duplicate event/confirmation, late retry, edited message, old callback and restart replay cause at most one issuer begin; consumed/expired nonces never revive. |
| Contention | Simultaneous Telegram, Slack, dashboard and CLI begins reserve one vault lease; duplicate requests do not create another poller; busy discloses no foreign details. |
| Commit races | Stage cancel, expiry, owner revocation, sign-out and newer sign-in against an approved response in both orders; losing attempts write nothing and cannot release the winner's lease. Include caller cancellation during worker-thread persistence. |
| Crash recovery | Expired/crashed lease is replaced with a new fence; a late old process cannot commit. Restart does not restore device secrets or confirmation authority. |
| Cadence | Issuer interval, repeated `slow_down`, backoff and deadline are honored; status and message retries do not increase polling. |
| Vault outcomes | Full disk, refused linked path, key/write failure and generation conflict are errors, never success; failed recovery preserves the previous identity; ambiguous slots require the UI. |
| Delivery | Begin reply failure cancels; notification failure after commit leaves a correct status; no fallthrough to public/cross-DM/unified-session delivery. |
| Secret canaries | Tokens, client/device secrets, approval link/code and confirmation nonce never reach logs, traces, SEL payloads, transcript/memory or later history backfill, including exception and restart paths. Only the specified approval material reaches the authorized DM. |
| Lifecycle/policy | Lazy construction, messaging-only gateway, shutdown drain, changed owner binding, disabled feature, audit outage and denied channel policy have defined, fail-closed outcomes. |
| Regression | Existing dashboard, CLI/cloud login, cancellation, sign-out generation, sign-in fingerprint, and harness-route tests still pass without a second token store. |

Release acceptance additionally requires a real phone-driven run through each
enabled platform/provider: owner command, fresh confirmation, issuer approval,
verified vault persistence, sanitized completion, and the correct next KAS
spawn. Record negative-owner, timeout/cancel, and concurrent-entry evidence.
A provider-start response, mocked token, passing document check, or merged
related PR does not establish this end-to-end result.

For this RFC, verification is source inspection and document checks only.
The complete repository test/doc gate and live authentication validation are
not represented as passed; the PR description records exactly which checks
were actually run and any environment limitation.

## Alternatives considered

UI-only handoff is the preferred fallback when maintainers do not accept storing
short-lived approval material on messaging platforms and when dashboard access
is already available. A new restricted UI capability may be considered later,
not hidden inside the command. A separate chat credential store, a subprocess
wrapper, natural-language/agent-triggered login, or per-channel-only mutex is
rejected because it duplicates authority or leaves existing writers unordered.
A broader multi-user account-management feature would need its own design.

## Open questions

1. **Owner proof (blocking):** Which existing trusted owner-management surface
   confirms platform identities, and where is its authoritative versioned binding
   stored? Is the single-operator Telegram configuration sufficient after explicit
   owner opt-in, or is separate pairing required? Which canonical Slack identity
   proof is supported without new broad scopes? No binding means no remote login.
2. **Entry point and disclosure (blocking):** Accept direct device link/code
   delivery with the stated platform-retention and account-confusion residuals,
   or require the independently authenticated UI? Are text confirmation and the
   proposed command names appropriate? Slash entry can remain deferred.
3. **Shared coordination (blocking):** Accept one active interactive login per
   vault across UI/CLI/chat, and the fenced commit/revocation protocol, including
   its `login_busy` compatibility cost? Confirm all participating writer entry
   points and the supported mixed-version/downgrade policy before implementation.
4. **Initial coverage (blocking):** Which existing device providers and running
   gateway shapes ship first? Recommend one-operator installations only, no boot
   policy changes, and provider enablement only after a live approval test.
5. **Operational policy:** Confirm timeout/rate limits, non-secret history-receipt
   retention, audit-failure semantics, and best-effort message removal. These must
   not weaken commit fencing or substitute for excluding secrets at their source.

[issue]: https://github.com/kirodotdev/KiroCrew/issues/26042
[invitation]: https://github.com/kirodotdev/KiroCrew/issues/26042#issuecomment-6514102354
[remote-pr]: https://github.com/kirodotdev/KiroCrew/pull/23657
[generation-pr]: https://github.com/kirodotdev/KiroCrew/pull/25608
[mcp-pr]: https://github.com/kirodotdev/KiroCrew/pull/28630
[device-standard]: https://www.rfc-editor.org/rfc/rfc8628.html#section-3.5
[tg-commands]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/telegram/commands.py
[tg-dispatch]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/telegram/transport_dispatch.py#L332-L480
[tg-transport]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/telegram/transport.py#L1-L260
[tg-owner]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/telegram/session_resume.py#L185-L300
[tg-dashboard]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/telegram/dispatch/commands.py#L1-L150
[slack-events]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/slack/events.py#L1-L270
[slack-owner]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/slack/handler_runtime/access.py#L1-L210
[slack-dispatch]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/slack/handler_runtime/dispatch.py#L80-L260
[slack-transport]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/slack/transport_dispatch.py#L1-L250
[messaging-identity]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/messaging/identity.py#L1-L230
[shared-owner]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/shared_crew/owner.py
[dashboard-auth]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/dashboard/handlers/kas_login.py#L1-L270
[service-begin]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/auth/service.py#L270-L510
[service-save]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/auth/service.py#L510-L790
[service-cancel]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/auth/service.py#L879-L973
[oidc]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/auth/login/builder_id.py#L1-L270
[store]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/auth/store.py#L1-L120
[login-ui]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/website/src/components/KasLoginGate.tsx#L1-L170
[kas-route]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/src/kiro_crew/acp/kas_route.py#L1-L230
[auth-spec]: https://github.com/kirodotdev/KiroCrew/blob/8b9aeceeef98a03a491d3547f2b71632d06c1765/docs/system-specs/modules/kas-auth.md#L1-L150
