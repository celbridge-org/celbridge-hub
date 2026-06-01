# Production Plan — Version 1

A step-by-step plan for standing up a new server that **exposes the
same API contract as the current Django `file_upload_API` project**
but is built on the architecture sketched in
`TDDs/production_plans/claude1.md`:

> Keycloak (identity) → thin API gateway → Verdaccio (storage and
> resolution engine) → S3 / MinIO (bytes) → Postgres (gateway state,
> audit) → Redis (scan queue) → Trivy (scanning) → Sigstore (signing).

The current Django implementation stays as the canonical reference for
behaviour. This document describes how to rebuild that behaviour on
the new stack without breaking existing clients.

---

## 1. The contract we must preserve

Every endpoint below must respond on the **same path**, the **same
HTTP method**, the **same status codes**, and (for JSON responses) the
**same body shape** as the current Django app. Bodies are bare JSON —
not the `{ data, meta, errors }` envelope from the claude1.md sketch.
Adopting the envelope is a v2 concern and is out of scope here.

```
GET    /api/packages                                  list (public)
POST   /api/packages                                  register {name,type} (auth)
GET    /api/packages/{name}                           detail (public)
DELETE /api/packages/{name}                           cascade-tombstone (auth)
GET    /api/packages/{name}/history                   text/markdown
GET    /api/packages/{name}/latest                    application/zip download
GET    /api/packages/{name}/versions                  list versions (public)
POST   /api/packages/{name}/versions                  publish (auth, multipart)
GET    /api/packages/{name}/versions/{n}              version detail (public)
DELETE /api/packages/{name}/versions/{n}              tombstone (auth)
GET    /api/packages/{name}/versions/{n}/download     application/zip; 410 if tombstoned
GET    /api/packages/{name}/versions/{n}/history      text/markdown as-of version n
GET    /api/packages/{name}/aliases                   list aliases (public)
PUT    /api/packages/{name}/aliases/{alias}           set alias {version} (auth)
DELETE /api/packages/{name}/aliases/{alias}           remove alias (auth)
```

Behavioural invariants that must carry over verbatim:

- **Integer-per-package versions.** Versions are `1, 2, 3, …`, not
  semver. The N+1 rule is enforced server-side on publish.
- **`latest` is reserved.** It tracks the highest non-tombstoned
  version automatically; clients cannot `PUT` or `DELETE` it.
- **Three package types** — `mod`, `project`, `page`. `page` packages
  additionally publish their unpacked contents to `/public/{name}/`
  on each new version and wipe that directory on tombstone.
- **Tombstones are soft deletes.** The version row, hash, and history
  entry remain; the zip bytes go. Download returns `410 Gone` with a
  JSON body describing the tombstone reason.
- **Forking.** Publish accepts `parent_version`, which may point at a
  version of a different package. The history endpoint walks the fork
  chain.
- **Reads are public; writes require auth.** Auth in the current app
  is DRF's session auth; the new stack swaps in OIDC/JWT (see §5).

---

## 2. The architectural shape

```
                       ┌──────────────────┐
                       │     Keycloak     │  OIDC, MFA, optional SAML
                       └────────┬─────────┘
                                │ JWKS
                                │
                       ┌────────▼─────────┐
   clients ──HTTPS──▶  │   API Gateway    │  FastAPI app
                       │  (preserves the  │
                       │   /api contract) │
                       └─┬──────┬────┬────┘
                         │      │    │
              ┌──────────┘      │    └────────────┐
              │                 │                 │
       ┌──────▼─────┐     ┌─────▼──────┐    ┌─────▼──────┐
       │  Postgres  │     │  Verdaccio │    │  Redis +   │
       │  (gateway  │     │  (registry │    │  Celery /  │
       │   state +  │     │   behind   │    │  RQ worker │
       │   audit)   │     │   auth)    │    └─────┬──────┘
       └────────────┘     └─────┬──────┘          │
                                │                 │ scan, sign
                          ┌─────▼──────┐    ┌─────▼──────┐
                          │  S3/MinIO  │    │  Trivy +   │
                          │  (tarballs)│    │  Sigstore  │
                          └────────────┘    └────────────┘
```

- **Gateway** is the only thing clients see. It owns the URL space,
  the response shapes, and all authorization.
- **Verdaccio** is an internal implementation detail. It is *never*
  reachable from outside the cluster. The gateway speaks npm protocol
  to it on a private network.
- **Postgres** stores everything that isn't a tarball: package rows,
  version rows, aliases, tombstones, fork links, audit log. Verdaccio
  is content storage; Postgres is the system of record for metadata.
- **MinIO/S3** is Verdaccio's tarball backend via its S3 storage
  plugin, *and* the destination for `page`-type unpacked content
  (separate bucket, served behind a CDN at `/public/{name}/`).

---

## 3. Pre-build decisions

These need answers before code is written; defaults given.

1. **Hosting target.** Default: Kubernetes (one namespace per env).
   Alternative: docker-compose on a single VM for staging.
2. **TLS termination.** Default: ingress (Traefik/nginx) does TLS;
   gateway speaks plain HTTP internally.
3. **Postgres flavour.** Default: managed (RDS / Cloud SQL / Supabase)
   in prod, container in dev.
4. **Object storage.** Default: MinIO in dev/staging, S3 (or R2) in
   prod. Same client API in both.
5. **Auth front door.** Default: Keycloak, self-hosted, one realm.
   Auth0/Cognito work too; OIDC discovery URL is the only thing that
   changes.
6. **CI publishing identity.** Default: scoped, hashed API keys issued
   by the gateway (claude1.md §"Auth model"). Format
   `kpf_live_<32-char-random>`. Users get short-lived JWTs.
7. **Data migration.** Default: write a one-shot importer that reads
   the current SQLite DB and the `media/` tree, recreates rows in the
   new Postgres, and re-uploads tarballs into MinIO/S3 via Verdaccio's
   publish path. Rerunnable, idempotent on `(name, version)`.
8. **Cutover model.** Default: dual-write window during which the new
   gateway is read-only behind a different hostname for soak testing;
   then DNS flip; old Django kept read-only for rollback for ~30 days.

---

## 4. Phased build

Each phase ends with something testable end-to-end.

### Phase 0 — Repo + skeleton (½ day)

- Create new repo, e.g. `package-platform-gateway`.
- FastAPI app with `/healthz`, `/readyz`, OpenTelemetry traces, JSON
  structured logging.
- `docker-compose.yml` for dev: gateway + Postgres + Redis + MinIO +
  Keycloak + Verdaccio. One command brings the stack up.
- Pre-commit: ruff, mypy strict, pytest scaffolding.

### Phase 1 — Identity (1–2 days)

- Deploy Keycloak. One realm `packages`. Two clients:
  - `gateway-api` — confidential, used by the gateway to verify JWTs.
  - `web-cli` — public, used by the web UI / CLI to do OIDC code flow.
- Enable email/password + TOTP. SAML/social can be added later.
- Gateway middleware: extract `Authorization: Bearer …`, verify via
  Keycloak JWKS (cached), check `exp`/`aud`/`iss`. On success, attach
  a `Principal(kind="user", id=…, scopes=[…])` to the request.
- API-key path: opaque key `kpf_live_<32-char>`, looked up by
  Argon2-hashed prefix in Postgres. Resolves to a `Principal(kind=
  "api_key", id=…, scopes=[…])`. Same downstream middleware.
- Tests: anonymous → 200 on reads, 401 on writes. Bearer → permitted.
  Revoked key → 401.

### Phase 2 — Data model (1 day)

Postgres schema, mirroring the current Django models:

- `package` (id, name UNIQUE, type ENUM('mod','project','page'),
  created_at)
- `package_version` (id, package_id, version INT, author_id,
  uploaded_at, summary, description, content_hash, storage_key,
  tombstoned_at NULL, tombstone_reason, forked_from_id NULL)
  UNIQUE `(package_id, version)`.
- `package_alias` (id, package_id, name, version_id) UNIQUE
  `(package_id, name)`. `name='latest'` is auto-managed.
- `api_key` (id, principal_id, prefix, hash, scopes JSONB, created_at,
  revoked_at).
- `audit_event` (id, ts, principal_id, action, target, request_id,
  ip, payload JSONB). Append-only — enforced by revoking UPDATE/
  DELETE on the role used by the app.

Alembic migrations from day one.

### Phase 3 — Verdaccio behind the gateway (1–2 days)

- Deploy Verdaccio with the `verdaccio-aws-s3-storage` plugin pointing
  at MinIO. Bind it to an internal-only address.
- Configure Verdaccio's auth as a static internal token: gateway sends
  `Authorization: Bearer <internal-svc-token>` on every call;
  Verdaccio trusts the gateway, full stop. **Verdaccio never sees
  end-user credentials.** All authz happens in the gateway.
- In the gateway, write a `RegistryClient` with three methods:
  `publish(tarball_bytes, name, version_npm, metadata)`,
  `fetch_tarball(name, version_npm) -> bytes`, and
  `tarball_exists(name, version_npm) -> bool`.
- Integer→semver mapping: package version `N` is stored in Verdaccio
  as `0.0.N`. We never expose this mapping to clients. Documented in
  one place: `registry_client.py::INT_TO_NPM_VERSION`.

### Phase 4 — Endpoint translation (3–5 days)

Implement the endpoint table in §1, in this order. Each one ships with
contract tests that replay request/response fixtures captured from the
current Django app.

| Endpoint | Backend calls |
|---|---|
| `GET  /api/packages` | Postgres list |
| `POST /api/packages` | Postgres insert, audit log |
| `GET  /api/packages/{name}` | Postgres join |
| `DELETE /api/packages/{name}` | Tombstone all versions in a txn, drop aliases, audit log, `wipe_public` if type=page |
| `GET  /api/packages/{name}/versions` | Postgres list |
| `POST /api/packages/{name}/versions` | (1) parse multipart, enforce `max_file_size_mb`; (2) parse the zip — reuse the current parser as a library; (3) compute N+1; (4) enqueue scan job; (5) on scan pass, publish tarball to Verdaccio at `0.0.N`; (6) insert row; (7) update `latest` alias; (8) if type=page, unpack to `public/` bucket; (9) audit log |
| `GET  /api/packages/{name}/versions/{n}` | Postgres |
| `DELETE /api/packages/{name}/versions/{n}` | Tombstone, re-point `latest`, audit log |
| `GET  /api/packages/{name}/versions/{n}/download` | If tombstoned → 410 JSON; else stream from Verdaccio (or hand back a presigned MinIO URL via a 302 — choose one mode, document it) |
| `GET  /api/packages/{name}/latest` | Resolve `latest` alias → delegate to `/versions/{n}/download` |
| `GET  /api/packages/{name}/history` | Render markdown — port `history.py::render_history` verbatim |
| `GET  /api/packages/{name}/versions/{n}/history` | Same renderer with `max_version=n` |
| `GET  /api/packages/{name}/aliases` | Postgres list |
| `PUT  /api/packages/{name}/aliases/{alias}` | Reject `latest`, validate name regex, upsert |
| `DELETE /api/packages/{name}/aliases/{alias}` | Reject `latest`, delete |

The zip parser, history renderer, and alias-name validator are pure
Python and should be **lifted out of the Django app into a small
shared library** (`packagelib/`) before this phase starts. Both the
old Django app and the new gateway depend on `packagelib`, eliminating
drift during the cutover window.

### Phase 5 — Scanning + signing (2 days)

- On publish, the gateway writes the tarball to a staging key in
  MinIO and enqueues a job on Redis (Celery or RQ).
- Worker: pull tarball, run Trivy filesystem scan, fail-loud on any
  HIGH/CRITICAL CVE (configurable threshold).
- On pass: sign tarball with Sigstore (cosign), attach signature as a
  sibling object in MinIO, and **only then** call
  `RegistryClient.publish(...)` to promote to Verdaccio.
- Failure path: row stays in `pending` state, audit-logged, publish
  endpoint returned 202 with a `version_id`. A `GET /versions/{n}`
  returns `status: "scanning"` or `"rejected"` accordingly.
  - **Note:** the current Django app is synchronous and returns 201
    immediately. Going async is a contract change. Default for v1:
    keep publish synchronous — scan inline, fail with 400 on
    detection, only return 201 once scan + sign + Verdaccio publish
    have all succeeded. Async upgrade is a v2 concern.

### Phase 6 — Importer (2 days)

- One-shot script that reads the old SQLite DB and `media/` tree.
- For each package: re-create row.
- For each version (oldest first to preserve N ordering): publish via
  the new gateway's publish flow, **with scanning disabled and
  tombstone state preserved**. Tombstoned versions are imported as
  rows with `tombstoned_at` set and no tarball.
- Rerunnable. Skip on `(name, version)` already present in Postgres.

### Phase 7 — Observability + ops (1 day)

- Prometheus metrics: request rate, latency p50/p95/p99 per endpoint,
  publish queue depth, scan failures, audit-write failures.
- Grafana dashboards for the above.
- Alert on: 5xx rate >0.5%, publish latency p95 > 30s, scan worker
  backlog > 100, Verdaccio unreachable.
- Backups: Postgres daily snapshot, MinIO bucket versioning enabled,
  Verdaccio storage backed up nightly.

### Phase 8 — Cutover (1 day staging + rollout)

- Stand up the new stack at `api2.<host>` behind a feature-flagged
  ingress, read-only.
- Run the importer; diff endpoint-by-endpoint against the old API for
  every package/version using a recorded fixture suite.
- Flip DNS / ingress for `/api/...` to the new gateway. Old Django
  goes read-only.
- Keep old Django available at `api-legacy.<host>` for 30 days.
- Decommission once metrics are stable and no clients have hit the
  legacy host for 14 days.

---

## 5. Auth: how it bridges to the current contract

The current Django app uses `IsAuthenticated`, which means *any*
authenticated session-or-token user passes write checks. There is no
ownership model yet. The new stack must not regress that — but it
should be ready for finer-grained scopes when v2 needs them.

- **Phase-1 scopes (parity with today):** every authenticated
  principal gets the implicit scope `packages:write:*`. Reads are
  anonymous. Aliases other than `latest` require
  `packages:write:{name}` (which everyone has, for now).
- **Phase-2 scopes (future, no v1 work):** narrow `packages:write:*`
  to `packages:write:{name}` based on namespace/ownership rows. The
  scope-check middleware is the only thing that has to change.

The token formats and validation flow are exactly as described in
claude1.md §"Auth model". The only deviation from that doc is that
the response envelope stays bare-JSON for contract compatibility.

---

## 6. Verdaccio fit-gap

Verdaccio expects semver. We have integer versions. Three concessions
need to be made up front so this doesn't bite later:

1. **Mapping is gateway-local.** `N ↔ 0.0.N` is mechanical and lives
   in one module. If we ever outgrow `0.0.65535`, switch to `N → 0.N.0`
   without touching anything else.
2. **Don't expose Verdaccio's dist-tags as our aliases.** Our aliases
   are a Postgres concept; Verdaccio's dist-tags are unused. This
   keeps semantics clean — our `latest` is determined by the highest
   non-tombstoned version, which is *not* equivalent to npm's default
   `latest` dist-tag.
3. **Don't rely on Verdaccio for tombstones.** npm "unpublish" has
   complicated semantics. We tombstone by deleting the tarball object
   from MinIO directly and marking the row in Postgres. Verdaccio's
   metadata for the version is left in place — clients never reach
   Verdaccio directly, so it doesn't matter.

If, after Phase 3, Verdaccio feels like it's adding more friction
than it's removing (semver fights, mostly), the fallback is to drop
it and have the gateway write tarballs straight to MinIO. The contract
to clients does not change. This decision can be made at the end of
Phase 3 without affecting any other phase.

---

## 7. The `page` type, specifically

The `page` package type is the only one with a side-effect outside
the API: each new version unpacks into `public/{name}/` and is served
over the public web at `/public/{name}/`. The current code does this
inline in `package_pipeline.process_upload` and clears the directory
on tombstone via `wipe_public`.

In the new stack:

- `public/` is a separate MinIO bucket fronted by a CDN.
- On publish of a `page` version: after Verdaccio publish succeeds,
  the gateway unpacks the zip to a temp dir, syncs it to
  `s3://public/{name}/` (delete-and-replace), and invalidates the CDN
  prefix.
- On tombstone of the latest `page` version: empty the prefix and
  invalidate. If a non-latest version is tombstoned, no public-side
  change is needed.
- This logic lives in `packagelib/page_publisher.py` so it can be
  unit-tested without S3.

---

## 8. Testing strategy

Three layers, in priority order:

1. **Contract replay.** Capture request/response pairs (incl. status
   code, headers, body) from the current Django app using a recorded
   fixture suite. Replay against the new gateway. Diff. Any drift is
   a bug. Tests live in `tests/contract/` and run on every PR.
2. **Behavioural integration.** Bring the docker-compose stack up,
   exercise full publish → fetch → tombstone → alias flows against a
   live Verdaccio + MinIO + Postgres. Slower; runs on main and nightly.
3. **Unit.** Pure-Python tests for `packagelib/` (parser, renderer,
   alias validator, page publisher). Run on every PR. Fast.

Contract replay is the load-bearing one. If it passes, clients
shouldn't notice the cutover.

---

## 9. Open risks

- **Page-publish atomicity.** Verdaccio publish, MinIO `public/`
  sync, and CDN invalidation are three systems. Failure between them
  can leave a mismatched state. Mitigation: write a reconciler job
  that compares Postgres `package_version.latest` for `page` types
  against the contents of `s3://public/{name}/` and re-syncs on
  mismatch. Runs every 5 minutes.
- **Synchronous scan latency.** Trivy on a multi-MB tarball can take
  seconds. The current Django app responds in ~100ms. If publish goes
  from 100ms → 5s, CI scripts may time out. Mitigation: keep scan
  inline for v1 but set the upload-side HTTP timeout to ≥60s in
  client docs; revisit async in v2.
- **Importer correctness.** The current SQLite DB is the only source
  of truth for some derived state (e.g., `latest` alias when no row
  is present). The importer needs to reconstruct that. Plan: dry-run
  mode that prints what it would do, diffed against a fresh export
  of the live API.
- **Verdaccio is GPL-2.0.** Fine for internal use; would be a problem
  if we ever wanted to redistribute the gateway as a closed product.
  Flagged for awareness, not blocking.

---

## 10. Estimated effort

Roughly **12–16 engineering days** for one engineer to reach a working
cutover candidate, not counting hardening, on-call rotation setup, or
formal security review. Phases 4 and 5 dominate the schedule. Phases
0–3 can be done in parallel by a second engineer if available
(Keycloak + Verdaccio + Postgres setup is independent from the
endpoint code, up until Phase 4 starts).

---

## 11. Estimated infrastructure cost

Sizing assumption for year one: **<50 users, <5000 packages, mostly
code (not large media files)**. Average code package 1–5 MB, so total
tarball storage is ~25 GB; with Postgres data, audit log, page-type
public assets, and Verdaccio metadata, plan for **~50 GB total**. At
this scale storage is a rounding error and compute dominates.

Object storage assumes **Impossible Cloud**: €7.99/TB/month
pay-as-you-go, **zero egress**, **zero API-call charges**, no
minimums. 50 GB ≈ €0.40/month. Even at 100× growth (5 TB) you'd be at
~€40/month, and the egress-free model removes the "download spike =
surprise bill" failure mode that bites on AWS/GCS.

### Tier 1 — Lean / single-VPS (recommended for v1)

Everything except object storage runs on one VPS.

| Item | Provider | Monthly | Yearly |
|---|---|---|---|
| Compute (8 GB / 4 vCPU) | Hetzner CCX23 / CX32 | €7–15 | €85–180 |
| Object storage (50 GB) | Impossible Cloud | €0.40 | €5 |
| Backup VPS (small, separate region) | Hetzner CX22 | €5 | €60 |
| Domain | Namecheap / Porkbun | €1 | €12 |
| CDN + DNS | Cloudflare free | €0 | €0 |
| TLS | Let's Encrypt | €0 | €0 |
| Identity (Keycloak self-hosted) | — | €0 | €0 |
| Postgres (on VPS, nightly pg_dump to S3) | — | €0 | €0 |
| Redis (on VPS) | — | €0 | €0 |
| Monitoring | Grafana Cloud free / self-hosted | €0 | €0 |
| Error tracking | Sentry free (5K events/mo) | €0 | €0 |
| Scanning / signing | Trivy + Sigstore (OSS) | €0 | €0 |
| **Total** | | **~€15–25/mo** | **~€180–300/yr** |

Trade-off: single point of failure; you eat the ops work; Postgres
backups are your responsibility (nightly `pg_dump` to Impossible Cloud
is fine at this size).

### Tier 2 — Managed where it matters (low-ops)

Same architecture, but offload Postgres and (optionally) identity so
3 AM pages don't happen.

| Item | Provider | Monthly | Yearly |
|---|---|---|---|
| Compute (gateway + Verdaccio + worker) | Hetzner CX32 or 2× DO droplets | €15–30 | €180–360 |
| Object storage (50 GB) | Impossible Cloud | €0.40 | €5 |
| Managed Postgres (8 GB, HA) | DigitalOcean / Supabase Pro | €15–25 | €180–300 |
| Managed Redis | Upstash free tier | €0 | €0 |
| Identity | Auth0 free (25K MAU) *or* Keycloak self-hosted | €0 | €0 |
| CDN + DNS | Cloudflare free | €0 | €0 |
| Monitoring | Grafana Cloud free | €0 | €0 |
| Error tracking | Sentry free | €0 | €0 |
| Domain | Namecheap | €1 | €12 |
| **Total** | | **~€35–60/mo** | **~€400–700/yr** |

The right tier for "v1 in production with a real user base."

### Tier 3 — Enterprise-shaped (overkill at this scale)

Kubernetes, paid observability, HA everywhere, paid Keycloak. Listed
for ceiling reference only — **don't pick this for <50 users.**

| Item | Provider | Monthly | Yearly |
|---|---|---|---|
| Managed K8s cluster (3 nodes) | DO / AWS EKS | €100–150 | €1.2–1.8K |
| Managed Postgres with HA | RDS Multi-AZ / Crunchy | €60–100 | €700–1.2K |
| Managed Redis | Redis Cloud Pro | €15 | €180 |
| Managed Keycloak | Cloud-IAM / Phase Two | €50 | €600 |
| Storage | Impossible Cloud | €1 | €12 |
| Observability | Datadog / Grafana Cloud Pro | €50–100 | €600–1.2K |
| **Total** | | **~€280–420/mo** | **~€3.3–5K/yr** |

### What the table omits

- **Engineer time dominates.** The 12–16 day v1 build at a typical
  loaded contractor rate (€500–800/day) is **€6K–€13K up front** — one
  full year of Tier 3 or 20+ years of Tier 1. Optimize for your time,
  not for €10/month savings.
- **Ongoing maintenance:** ~0.05–0.1 FTE for Tier 1/2 (patching,
  upgrades, on-call). Negligible in cash if you're the engineer;
  meaningful if outsourced.
- **Sigstore** uses free public infrastructure with rate limits. Fine
  for v1; if you outgrow it you self-host (compute only, no licensing).
- **Trivy CVE database** updates are free; bandwidth is included in
  the egress-free model.

### Recommendation

Start at **Tier 1 (~€20/month)** for staging and the first few months
of production. The architecture does not change between tiers — only
where Postgres and Keycloak run. Move to Tier 2 when either (a) a real
second engineer joins and shouldn't be paged for VPS reboots, or (b)
user count crosses ~100. Don't touch Tier 3 until paying customers ask
for an SLA in writing.
