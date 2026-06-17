from __future__ import annotations

import argparse
from dataclasses import dataclass


BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CorpusShape:
    documents: int
    chunks_per_document: int
    chunk_chars: int
    embedding_dim: int

    @property
    def chunks(self) -> int:
        return self.documents * self.chunks_per_document


@dataclass(frozen=True, slots=True)
class Estimate:
    label: str
    index_size_bytes: int
    ram_floor_bytes: int
    ram_comfort_bytes: int
    notes: tuple[str, ...]


def estimate_mariadb_keyword(shape: CorpusShape) -> Estimate:
    text_bytes = shape.chunks * shape.chunk_chars
    metadata_bytes = shape.chunks * 900
    fulltext_overhead = int(text_bytes * 0.45)
    total = text_bytes + metadata_bytes + fulltext_overhead
    return Estimate(
        label="MariaDB FULLTEXT",
        index_size_bytes=total,
        ram_floor_bytes=512 * BYTES_PER_MIB,
        ram_comfort_bytes=1 * BYTES_PER_GIB,
        notes=(
            "Reuses the existing SQL service and backup path.",
            "Latency depends on buffer pool sizing and FULLTEXT term selectivity.",
        ),
    )


def estimate_qdrant_vectors(shape: CorpusShape) -> Estimate:
    vector_bytes = shape.chunks * shape.embedding_dim * 4
    payload_bytes = shape.chunks * 450
    graph_overhead = int(vector_bytes * 0.65)
    total = vector_bytes + payload_bytes + graph_overhead
    ram_floor = max(512 * BYTES_PER_MIB, int(vector_bytes * 0.20))
    return Estimate(
        label="Qdrant vectors",
        index_size_bytes=total,
        ram_floor_bytes=ram_floor,
        ram_comfort_bytes=max(1 * BYTES_PER_GIB, int(vector_bytes * 0.60)),
        notes=(
            "Stores vectors and metadata pointers only; MariaDB remains source of truth.",
            "Embedding model latency is outside this storage estimate.",
        ),
    )


def estimate_opensearch_single_node(shape: CorpusShape) -> Estimate:
    text_bytes = shape.chunks * shape.chunk_chars
    metadata_bytes = shape.chunks * 1_200
    vector_bytes = shape.chunks * shape.embedding_dim * 4
    bm25_overhead = int(text_bytes * 0.70)
    hnsw_overhead = int(vector_bytes * 1.00)
    total = text_bytes + metadata_bytes + vector_bytes + bm25_overhead + hnsw_overhead
    ram_floor = max(2 * BYTES_PER_GIB, int(vector_bytes * 0.35))
    return Estimate(
        label="OpenSearch single node",
        index_size_bytes=total,
        ram_floor_bytes=ram_floor,
        ram_comfort_bytes=max(4 * BYTES_PER_GIB, int(vector_bytes * 0.90)),
        notes=(
            "Needs JVM heap plus filesystem cache; do not size only by index bytes.",
            "Combines lexical and vector retrieval but adds a separate snapshot/restore path.",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate Current-Info retrieval storage and RAM sizing.")
    parser.add_argument("--documents", type=_positive_int, default=5_000)
    parser.add_argument("--chunks-per-document", type=_positive_int, default=12)
    parser.add_argument("--chunk-chars", type=_positive_int, default=1_200)
    parser.add_argument("--embedding-dim", type=_positive_int, default=768)
    args = parser.parse_args()

    shape = CorpusShape(
        documents=args.documents,
        chunks_per_document=args.chunks_per_document,
        chunk_chars=args.chunk_chars,
        embedding_dim=args.embedding_dim,
    )
    estimates = (
        estimate_mariadb_keyword(shape),
        estimate_qdrant_vectors(shape),
        estimate_opensearch_single_node(shape),
    )
    print(f"Corpus: {shape.documents:,} documents, {shape.chunks:,} chunks")
    print(f"Chunk chars: {shape.chunk_chars:,}; embedding dim: {shape.embedding_dim:,}")
    print()
    for estimate in estimates:
        print(estimate.label)
        print(f"  estimated index size: {_format_bytes(estimate.index_size_bytes)}")
        print(f"  RAM floor:            {_format_bytes(estimate.ram_floor_bytes)}")
        print(f"  RAM comfort:          {_format_bytes(estimate.ram_comfort_bytes)}")
        for note in estimate.notes:
            print(f"  - {note}")
        print()
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _format_bytes(value: int) -> str:
    if value >= BYTES_PER_GIB:
        return f"{value / BYTES_PER_GIB:.2f} GiB"
    return f"{value / BYTES_PER_MIB:.1f} MiB"


if __name__ == "__main__":
    raise SystemExit(main())
