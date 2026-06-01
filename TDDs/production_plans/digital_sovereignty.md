# EU Digital Sovereignty & Cloud Storage Options

A guide to choosing S3-compatible object storage (and broader cloud services) for an EU-based project where digital sovereignty matters.

## What "digital sovereignty" actually means

There's a spectrum, and "EU data residency" is the weakest end:

### Tier 1 — EU data residency

Data physically stored in EU data centers. AWS, Azure, GCP all offer this via their EU regions. **Weakest form of sovereignty.**

### Tier 2 — EU jurisdiction

Provider is an EU legal entity, not subject to US CLOUD Act, FISA 702, etc. The US CLOUD Act allows U.S. authorities to access data held by American companies regardless of where it is stored globally, which undermines GDPR protections. **This rules out AWS/Azure/GCP even when using their EU regions.**

### Tier 3 — EU-operated infrastructure

Data centers owned and operated by EU entities, EU staff, EU supply chain. The strictest interpretation, often required for government/defence/healthcare. SecNumCloud (France) is the reference qualification.

### Which tier applies?

Depends on what your data actually contains:

- **GDPR for personal data** → tier 1 often sufficient with proper DPAs
- **Schrems II caution** → tier 2 minimum (rules out US-parented providers)
- **Sector regulation** (healthcare, finance, defence, public sector) → tier 3
- **Strategic / geopolitical concerns** → tier 2 minimum, ideally tier 3

---

## True EU-sovereign providers (tier 2+)

### Scaleway (France)

The most "AWS-like" of the EU-native providers.

- S3-compatible Object Storage
- Regions in Paris, Amsterdam, Warsaw
- Full cloud platform: compute, managed Postgres, Kubernetes, load balancers
- SecNumCloud-qualified (French government sovereignty standard)
- Easiest migration path if you're used to AWS patterns

### OVHcloud (France)

Largest EU cloud provider.

- S3-compatible Object Storage
- Locations: Gravelines, Strasbourg, Frankfurt, London, Warsaw
- SecNumCloud-qualified regions available
- Wide service catalog, similar to AWS in scope
- Mixed reputation on reliability (2021 Strasbourg fire) but heavy investment since

### Hetzner (Germany)

Known for excellent price/performance.

- S3-compatible Object Storage (relatively recent launch)
- Locations: Falkenstein, Nuremberg, Helsinki
- Less feature-rich than Scaleway/OVH but very cheap and reliable
- Good for compute + storage with straightforward needs
- Founded 1997 — long operational track record

### IONOS (Germany)

S3 Object Storage in German data centres, designed for GDPR compliance. Enterprise-oriented, German-owned. More expensive than Hetzner but stronger compliance posture.

### Exoscale (Switzerland)

Owned by A1 Telekom Austria.

- S3-compatible Object Storage
- Locations: Vienna, Frankfurt, Geneva, Zurich, Sofia
- Swiss data protection is even stricter than GDPR in some respects
- Good developer experience

### Impossible Cloud (Germany)

Headquartered in Hamburg, operates exclusively in EU data centers.

- S3-compatible API
- Not subject to US CLOUD Act or FISA 702
- Zero egress fees, no API request charges
- Marketed explicitly as "sovereign by design"
- **Caveats**: newer entrant (founded ~2022), narrower product (object storage only — no compute/Postgres/K8s), uses partner data center model rather than fully owned facilities

### Cubbit / WIIT European Cloud Vault (Italy)

Geo-distributed S3 (DS3) that distributes encrypted data across 19 data centers in 7 European regions, so data remains accessible even if an entire region fails. Zero-knowledge architecture — your data is shredded, erasure-coded, keys held by you.

---

## Apparent options that don't pass sovereignty requirements

### Storj

Geofences data to European regions and stores exclusively within EEA, **but** Storj Inc. is US-incorporated. Despite geofenced data, the company is under US jurisdiction. Probably fails strict sovereignty requirements.

### AWS S3, Azure Blob, Google Cloud Storage (EU regions)

Even in EU regions, the parent is US-jurisdictional. US companies hold ~69% of Europe's cloud market, exposing EU data to non-EU legislation.

### AWS European Sovereign Cloud (Brandenburg, Germany)

AWS's attempt to address this with a separately governed EU entity. Worth watching but legal independence is debated. Conservative EU regulators (especially France, Germany) are skeptical.

### Microsoft Cloud for Sovereignty / Google Sovereign Cloud (partnerships with T-Systems, Thales)

Better than vanilla US cloud, but not the same as a fully EU-owned provider.

### Cloudflare R2, Backblaze B2

US companies, despite EU points of presence.

---

## Egress fees

Egress = charges for data **leaving** the cloud provider's network (downloads to the public internet, transfers to other providers).

### Why they exist

Hyperscalers (AWS, Azure, GCP) charge for outbound bandwidth at rates wildly disconnected from actual cost. AWS charges roughly $0.05–0.09 per GB egress from EU regions; actual wholesale bandwidth cost is around $0.001–0.005 per GB — a 10–100x markup.

The reason is strategic: egress fees are a **lock-in mechanism**. Storing data is cheap and easy; getting it out is expensive. Once you have terabytes in AWS, the bill to move it elsewhere is itself a barrier to leaving.

The **EU Data Act** (in force September 2025) targets this directly — mandates data portability and limits "switching charges." Several US providers have responded by waiving egress fees for customers actively migrating *off* their platform, but normal-operation egress fees remain.

### Why it matters for large-media use cases

Run the math for a project distributing media:

- 100 client projects × 50 GB downloads/month = 5 TB egress/month
- AWS S3 EU egress @ ~$0.09/GB → **~$450/month just for bandwidth**
- Scale to 500 projects → **~$2,250/month**
- Add CDN distribution or international downloads → multiply by 2–3x

For small npm tarballs the impact is smaller, but still adds up at scale.

### Provider egress comparison (EU regions, normal operation)

| Provider | Egress to internet | Notes |
|---|---|---|
| AWS S3 | ~$0.09/GB | First 100 GB/month free; tiered discounts at high volume |
| Azure Blob | ~$0.087/GB | Similar to AWS |
| Google Cloud Storage | ~$0.12/GB | Often the highest |
| Cloudflare R2 | **$0** | Zero egress — their main differentiator (but US co.) |
| Backblaze B2 | $0.01/GB | 3× free egress relative to storage (but US co.) |
| **Scaleway** | ~€0.01/GB after free tier | 75 GB/month free egress per project |
| **OVHcloud** | Free on Standard Object Storage | Limited bandwidth quotas; "High Performance" tier charges |
| **Hetzner** | Free up to 1 TB/month per storage box | Then ~€1/TB, very cheap |
| **IONOS** | Generally included in storage price | Check current terms |
| **Exoscale** | Free up to a quota, then ~€0.02/GB | |
| **Impossible Cloud** | **$0** | No egress, no API call fees |
| **Cubbit / WIIT** | Generally included | DS3 model bills differently |
| **MinIO self-hosted** | You pay hosting provider's bandwidth | On Hetzner dedicated: ~free up to 20 TB/month |

EU-native providers and S3 challengers have largely abandoned the hyperscaler egress-fee model. It's one of their main competitive levers.

### Hidden costs beyond headline egress

- **API request fees** — AWS charges per-request (GET, PUT, LIST). Trivial individually, painful at scale. Most EU providers don't charge.
- **Cross-region transfer** — moving between two regions of the same provider often costs egress rates.
- **Inter-AZ transfer** — AWS charges within one region; most others don't.
- **NAT gateway / load balancer egress** — separate from S3 egress, often forgotten.
- **CDN origin pulls** — when CloudFront/Cloudflare pulls from your origin, you pay egress unless the provider has free-transfer arrangements (Cloudflare → R2 free, AWS → CloudFront free, AWS → Cloudflare CDN *not* free).

---

## Architectural implications

### Pre-signed URLs go direct from S3 to client

The architecture we sketched (presigned URLs for uploads/downloads) means bandwidth bills are whatever the storage provider charges — it doesn't double-bill through your API server.

### Consider an EU-sovereign CDN

For repeated downloads of category 3 (large media), a CDN in front reduces origin egress and improves delivery latency:

- **Bunny.net** (Slovenian, EU-sovereign) — reasonable pricing, integrates cleanly with S3 origins
- **Gcore** (Luxembourg-HQ) — EU-native CDN

### Budget egress separately

Storage is usually the small line item; egress and requests are the variable ones that scale with usage. Model them as a separate cost driver.

### Watch for "free egress up to X" patterns

Hetzner's free tier on storage boxes is generous; their cloud object storage has different terms. Always read the specific product's pricing, not the company's overall reputation.

### Client-side encryption is your safety net

Even with a sovereign provider, encrypt client-side before upload. Then sovereignty is about *who can compel the bits*, not *who can read them*. You can use any provider while keeping the keys yourself — this is what Cubbit's zero-knowledge model formalises, but you can do it manually on any S3-compatible backend.

### Whole-stack consistency

Storage isn't enough on its own. Your **Keycloak hosting, FastAPI gateway, Postgres for audit logs** — the whole stack should sit in the same sovereignty tier, otherwise you've moved the storage but the access path still touches non-sovereign infrastructure.

---

## Recommendations for the project stack

Given the requirements:

- S3-compatible (matches the architecture sketch)
- EU digital sovereignty (tier 2 minimum)
- Predictable egress (especially for category 3 — large client project media)
- Likely to need broader services as project grows

### Primary recommendation: Scaleway or OVHcloud

Both are mature, S3-compatible, with full cloud platforms beyond storage. When you later need Postgres, compute, Kubernetes, you stay in the same provider.

- **Scaleway**: better developer ergonomics
- **OVHcloud**: broader scale, more compliance certifications

### Budget alternative: Hetzner

Object Storage + cloud compute is excellent value. Less polished tooling than Scaleway, but if needs are straightforward this is hard to beat on cost. Location exclusively EU. Long operational history.

### Storage-only specialist: Impossible Cloud

German, sovereign, zero egress, very competitive pricing. Best if object storage is *all* you need and you're happy multi-providering for compute/databases.

### Maximum sovereignty: MinIO self-hosted on EU bare metal

Run MinIO yourself on Hetzner/OVH/Scaleway dedicated servers. You control everything. Worth it for category 3 if data is extremely sensitive and you want client-controlled encryption with no provider in the trust chain.

**Caveat**: MinIO's licensing changed recently — for commercial use beyond a certain scale you need their enterprise license. Check current terms before committing.

### Trap to avoid

Don't optimise for zero-egress (Cloudflare R2, Backblaze B2) at the cost of sovereignty if sovereignty is genuinely a requirement. The cheapest egress is worth nothing if the data shouldn't be there in the first place.