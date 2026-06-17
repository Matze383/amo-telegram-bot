# Spike 077: OpenSearch Evaluation After MariaDB + Vector Retrieval

## Verdict: PARTIAL

Question: Should Current-Info evaluate OpenSearch before the existing MariaDB keyword/FULLTEXT cache and optional Qdrant vector retrieval are proven?

Evidence: Current code already has a MariaDB/MySQL FULLTEXT path for `current_info_document_chunks`, Qdrant vector indexing with MariaDB as source of truth, and hybrid reciprocal-rank fusion. The next measurable baseline is therefore MariaDB keyword/FULLTEXT plus Qdrant vectors, not OpenSearch.

Recommendation: Do not introduce OpenSearch yet. Run a MariaDB + vector baseline first. OpenSearch becomes a Go only if it beats that baseline on retrieval quality or latency at realistic Current-Info sizes while staying within RAM, backup, and operations budgets.

## Existing Current-Info Baseline

- `src/amo_bot/current_info/cache.py` stores fetched public documents and retrieval chunks in SQLAlchemy tables, prunes by TTL/retention, hashes private query text in query-run metrics, and uses MySQL/MariaDB `MATCH(title, text_excerpt) AGAINST (...)` when the backend supports it.
- `src/amo_bot/db/models.py` defines `current_info_documents` and `current_info_document_chunks`; chunks have `ix_current_info_document_chunks_search_text` as a MySQL FULLTEXT index on `title, text_excerpt`.
- `src/amo_bot/current_info/vector.py` indexes chunk pointers into Qdrant. The vector payload deliberately omits full text and resolves vector hits back through MariaDB chunk rows.
- `src/amo_bot/current_info/hybrid.py` fuses keyword and vector chunks with reciprocal-rank fusion, source-type boosts, recency boosts, weak-source penalties, host diversity, and metadata filters.
- `src/amo_bot/current_info/eval.py` provides deterministic answer-quality evals from fixtures, but it is currently answer-quality focused rather than storage/latency focused.

## Benchmark Methodology

Run this only after MariaDB + vector retrieval is functional in a local or staging instance.

1. Build a fixed Current-Info corpus from public data only:
   - Small: 5,000 documents, 60,000 chunks. This matches the default `AMO_CURRENT_INFO_CACHE_MAX_DOCUMENTS=5000` and `AMO_CURRENT_INFO_CACHE_MAX_CHUNKS_PER_DOCUMENT=12`.
   - Medium: 25,000 documents, 300,000 chunks. This represents several retained Current-Info windows or larger production use.
   - Large: 100,000 documents, 1,200,000 chunks. This is a stress target, not an initial production requirement.
2. Use identical chunk text, metadata, and query sets for both candidates.
3. Candidate A: MariaDB/MySQL FULLTEXT for keyword retrieval plus Qdrant for semantic retrieval, fused through the existing hybrid provider.
4. Candidate B: single-node OpenSearch with BM25 text fields and k-NN vectors in one index. Keep MariaDB as source of truth unless a later migration explicitly changes the data model.
5. Measure warm-cache and cold-after-restart behavior separately.
6. Run at least 100 representative queries per domain bucket: news/current, docs/official, local/region, broad web, and unknown/general.

## Measurements

Record these for every corpus size and candidate:

- RAM: idle RSS after load, RSS during 100 concurrent-ish sequential queries, and peak during index build.
- Index size: MariaDB table/index bytes, Qdrant collection bytes, OpenSearch data path bytes, and backup archive bytes.
- Query latency: p50, p95, p99, timeout count, and result count for keyword-only, vector-only, and fused/hybrid queries.
- Quality: reuse Current-Info eval fixtures and add retrieval-specific checks for expected source URL, required terms, source freshness, and source diversity.
- Operational overhead: services to run, ports/secrets, health checks, snapshot/backup steps, restore drill time, upgrade steps, and failure modes.
- Backup/restore: dump MariaDB, snapshot Qdrant, snapshot OpenSearch, then restore into an empty instance and re-run the same retrieval checks.

## Go / No-Go Criteria

OpenSearch is a Go only if all of these are true:

- It improves p95 retrieval latency by at least 30% at the medium corpus size, or improves retrieval quality enough to pass eval cases that MariaDB + vector fails.
- It stays below a 2 GB RAM budget for the small corpus and below an agreed production RAM budget for the medium corpus (see Source-Backed Operating Facts: OpenSearch JVM heap should be ~50% of server RAM but no more than ~30–32 GB).
- Its on-disk index plus snapshots are not materially larger than MariaDB + Qdrant for the same chunks and embeddings (see Source-Backed Operating Facts: OpenSearch snapshots are incremental and share data; Qdrant vectors default to RAM with optional disk offload).
- Backup and restore are documented, repeatable, and no more complex than the MariaDB + Qdrant two-store baseline (see Source-Backed Operating Facts: OpenSearch snapshots require API usage for deletion; Qdrant supports collection and full storage snapshots with configurable paths).
- The migration path preserves MariaDB as source of truth or has an explicit rollback path.
- Failure of OpenSearch does not break Current-Info fallback behavior.

No-Go if any of these are true:

- Benefit is only architectural consolidation without measured latency or quality gain.
- Single-node OpenSearch needs materially more RAM than the host can spare.
- Operations require new production backup/restore tooling before Current-Info itself is proven.
- Relevance is equivalent to MariaDB + vector at the small/default corpus size.

## Hardware Needs

Use the estimator in this directory before installing anything:

```bash
python spikes/077-opensearch-evaluation/estimate_resources.py --documents 5000 --chunks-per-document 12 --embedding-dim 768
```

Initial hardware expectation:

- MariaDB FULLTEXT remains the lowest operational cost because it reuses the existing SQL database and backup path.
- MariaDB + Qdrant adds one vector service, but Current-Info already has code-level fallback to keyword retrieval when vector retrieval fails.
- OpenSearch single-node likely needs a dedicated memory budget even for modest corpora because JVM heap (should be ~50% of RAM, ≤ 32 GB), filesystem cache, BM25 structures, stored fields, and vector graph memory all compete on the same host.

## Migration Path If OpenSearch Becomes A Go

1. Keep MariaDB tables and cache pruning as the source of truth.
2. Add an indexer sidecar/job that projects `current_info_document_chunks` into an OpenSearch index.
3. Add a retrieval provider behind the existing `CurrentInfoRetrievalProvider` protocol.
4. Run shadow reads: execute MariaDB + vector and OpenSearch for the same query, but return the existing baseline result.
5. Compare telemetry for latency, hit overlap, eval pass rate, and timeout/error rate.
6. Enable OpenSearch for a narrow domain only after shadow-read results pass the Go criteria.
7. Keep MariaDB + vector fallback until at least one restore drill and one OpenSearch upgrade drill pass.

## Source-Backed Operating Facts

These facts are drawn from official documentation and inform the sizing, operational constraints, and Go/No-Go criteria below.

### OpenSearch

- **Snapshots:** Incremental by default; they capture primary shards as they existed when initiated, so in-flight documents/updates are generally excluded. Snapshots share underlying data; deleting them should use the API to avoid orphaned blocks. Source: https://docs.opensearch.org/latest/tuning-your-cluster/availability-and-recovery/snapshots/snapshot-restore/
- **JVM Heap:** Set `Xms` and `Xmx` equal. Heap should be about 50% of server RAM but no more than ~30–32 GB; the remaining RAM is needed for OS/Lucene page cache. Fielddata on text fields can cause heap pressure. Source: https://opensearch.org/blog/error-logs/error-log-outofmemoryerror-java-heap-space-the-node-crash/
- **AWS shard-to-heap guidance:** Around 25 shards per GiB of heap is a common rule of thumb, and AWS allocates about half of physical memory (up to 32 GB) to JVM/OpenSearch. Source: https://docs.aws.amazon.com/wellarchitected/latest/amazon-opensearch-service-lens/aosperf01-bp03.html

### MariaDB FULLTEXT

- **Storage engines:** Supported on MyISAM, Aria, InnoDB, and Mroonga; only CHAR/VARCHAR/TEXT columns; partitioned tables cannot contain FULLTEXT indexes.
- **Index behavior:** For large datasets it is faster to load data first and create the FULLTEXT index afterward. Searches use `MATCH ... AGAINST`. Partial words are excluded; default minimum word length is 4 chars for MyISAM and 3 chars for InnoDB (configurable). Stopwords are excluded. Natural-language searches are sorted by relevance. Source: https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/optimization-and-indexes/full-text-indexes/full-text-index-overview

### Qdrant

- **Memory:** Critical resource. By default Qdrant stores vectors in RAM for maximum search performance; it can offload data to disk, and frequently accessed vectors stay cached. HNSW on disk can require significant I/O; putting vectors+HNSW on disk should be done only when RAM is severely constrained, ideally with fast NVMe. Source: https://qdrant.tech/documentation/overview/
- **On-disk configuration:** Vector data can be patched to `on_disk=true`; HNSW, quantization, and disk configs can be updated without recreating a collection, with segments rebuilding in the background. Source: https://qdrant.tech/documentation/manage-data/collections/
- **Payload indexes:** Speed filtering but use additional memory/disk; only index fields used in filters. Text payload indexes support full-text filtering. Source: https://qdrant.tech/documentation/manage-data/indexing/
- **Snapshots:** Supports collection snapshots and full storage snapshots; restore can recover from URL/local file/upload. Default snapshot path is `./snapshots` or `/qdrant/snapshots` in Docker; configurable via `storage.snapshots_path` or env var. Source: https://qdrant.tech/documentation/snapshots/

## Estimator Notes (Heuristic)

`estimate_resources.py` provides sizing gates for deciding whether a benchmark is worth running. Key assumptions:
- **From repo sizing:** Chunk counts, text bytes per chunk, and embedding dimensions are based on Current-Info defaults and observed data shapes.
- **From source-backed ops constraints:** RAM comfort values incorporate the OpenSearch JVM heap guidance (heap ≤ 50% RAM, ≤ 32 GB) and Qdrant’s RAM-first design (fast search requires vectors in memory). Disk sizing assumes BM25 and HNSW overheads typical for text+vector combined indexes.
- The estimator does not account for concurrent query load, replication, or snapshot I/O bursts; those require runtime measurement.

## Risks And Assumptions

- This spike does not install or benchmark OpenSearch. That is intentional because the issue asks to evaluate it only after MariaDB + vector retrieval works.
- Resource estimates are heuristic. Treat them as sizing gates for whether a real benchmark is worth running, not as production capacity planning.
- OpenSearch could still win at larger corpora or more complex lexical ranking needs, but that should be proven against the existing hybrid provider rather than assumed.
