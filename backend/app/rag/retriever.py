"""Hybrid retriever over policy chunks.

* BM25 (keyword) is always on: free, deterministic, and very strong on a small
  corpus full of exact terms (city names, "COD", "₹499", "Final Sale").
* Dense embeddings are optional: set EMBEDDING_MODEL (any provider, e.g.
  "mistralai:mistral-embed") and the two rankings are fused with Reciprocal Rank
  Fusion, which helps paraphrased questions ("can I send it back?" -> returns).
* A relevance floor turns "nothing matched" into an explicit NO_MATCH signal so
  the agent says it doesn't know instead of guessing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app import config
from app.rag.ingest import Chunk, load_chunks

log = logging.getLogger(__name__)

STOPWORDS = set("""
a an the is are was were be been am i me my we our you your it its of to in on for from by with
and or but if then so do does did can could would should will shall may might what which who whom
how when where why this that these those there here about as at into than too very just any
please tell know want need get give much many
""".split())

# Small domain synonym map: customers rarely use the policy's exact vocabulary.
SYNONYMS = {
    "cod": ["cash", "delivery"],
    "money": ["refund"],
    "back": ["return", "refund"],
    "send": ["return", "ship"],
    "exchange": ["exchanges"],
    "ship": ["shipping", "delivery"],
    "deliver": ["delivery"],
    "arrive": ["delivery"],
    "charge": ["charges", "fee"],
    "fee": ["charges"],
    "cost": ["charges", "fee"],
    "broken": ["damaged", "defective"],
    "faulty": ["defective"],
    "guarantee": ["warranty"],
    "address": ["address", "delivery"],
    "delete": ["deleting", "deletion"],
    "card": ["card"],
    "sale": ["final", "clearance"],
    "track": ["tracking"],
    "return": ["returns", "window"],
    "days": ["window"],
    "long": ["window", "timelines"],
    # product words -> policy categories
    **{w: ["clothing"] for w in ["jeans", "shirt", "tshirt", "kurta", "dress", "jacket", "saree",
                                "trousers", "top", "clothes", "apparel"]},
    **{w: ["footwear"] for w in ["shoes", "shoe", "sneakers", "sandals", "slippers", "boots"]},
    **{w: ["electronics", "accessories"] for w in ["phone", "mobile", "laptop", "earbuds",
                                                  "headphones", "smartwatch", "watch", "charger",
                                                  "tablet", "speaker", "tv", "camera"]},
    **{w: ["home", "kitchen"] for w in ["pan", "lamp", "mug", "bedsheet", "cookware", "utensil"]},
    **{w: ["personal", "care", "beauty"] for w in ["serum", "cream", "lipstick", "shampoo", "makeup"]},
    **{w: ["innerwear", "lingerie"] for w in ["underwear", "bra", "boxers", "briefs"]},
}


def _stem(tok: str) -> str:
    for suf in ("ing", "ies", "es", "ed", "s"):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)] + ("y" if suf == "ies" else "")
    return tok


def tokenize(text: str, expand: bool = False) -> list[str]:
    toks = re.findall(r"[a-z0-9₹]+", text.lower().replace("&", " and "))
    out: list[str] = []
    for t in toks:
        if t in STOPWORDS:
            continue
        out.append(_stem(t))
        if expand and t in SYNONYMS:
            out.extend(_stem(s) for s in SYNONYMS[t])
    return out


@dataclass
class Hit:
    chunk: Chunk
    score: float


class PolicyRetriever:
    def __init__(self, chunks: list[Chunk] | None = None):
        self.chunks = chunks if chunks is not None else load_chunks(config.POLICY_DIR)
        if not self.chunks:
            # Fail loudly here: an empty index would otherwise crash deep inside BM25
            # with "division by zero", or worse, answer every question with "I don't know".
            raise RuntimeError(
                f"No policy sections found in {config.POLICY_DIR}. "
                "Expected markdown files with '## ' headings (e.g. 01_shipping_policy.md). "
                "If this happens in a container, check that .dockerignore does not exclude *.md."
            )
        corpus = [tokenize(f"{c.doc_title} {c.section} {c.section} {c.text}") for c in self.chunks]
        self.bm25 = BM25Okapi(corpus)
        self.embedder = None
        self.doc_vectors: list[list[float]] = []
        if config.USE_EMBEDDINGS:
            self._init_embeddings()

    # ---------- optional dense retrieval ----------
    def _init_embeddings(self) -> None:
        """EMBEDDING_MODEL like 'google_genai:models/text-embedding-004' or 'mistralai:mistral-embed'.
        Vectors are cached on disk, so the documents are embedded only once."""
        try:
            from langchain.embeddings import init_embeddings
            self.embedder = init_embeddings(config.EMBEDDING_MODEL)
            config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
            cache_path = config.INDEX_DIR / "embeddings.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
            keys = [hashlib.sha1((config.EMBEDDING_MODEL + c.render()).encode()).hexdigest()
                    for c in self.chunks]
            missing = [i for i, k in enumerate(keys) if k not in cache]
            if missing:
                vecs = self.embedder.embed_documents([self.chunks[i].render() for i in missing])
                for i, v in zip(missing, vecs):
                    cache[keys[i]] = v
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
            self.doc_vectors = [cache[k] for k in keys]
        except Exception as exc:  # never block startup because of optional embeddings
            log.warning("Embeddings disabled (%s). Using BM25 only.", exc)
            self.embedder, self.doc_vectors = None, []

    def _dense_ranking(self, query: str) -> list[int]:
        q = self.embedder.embed_query(query)
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            return dot / (na * nb + 1e-9)
        sims = [cos(q, v) for v in self.doc_vectors]
        return sorted(range(len(sims)), key=lambda i: -sims[i])

    # ---------- main API ----------
    def search(self, query: str, k: int | None = None) -> list[Hit]:
        k = k or config.RETRIEVAL_TOP_K
        scores = self.bm25.get_scores(tokenize(query, expand=True))
        bm25_rank = sorted(range(len(scores)), key=lambda i: -scores[i])
        best = scores[bm25_rank[0]] if len(bm25_rank) else 0.0

        if self.embedder and self.doc_vectors:
            dense_rank = self._dense_ranking(query)
            rrf: dict[int, float] = {}
            for rank_list in (bm25_rank, dense_rank):
                for pos, idx in enumerate(rank_list):
                    rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + pos)
            order = sorted(rrf, key=lambda i: -rrf[i])
        else:
            order = bm25_rank

        if best < config.RETRIEVAL_MIN_SCORE and not self.embedder:
            return []  # nothing in the documents is about this
        hits = [Hit(self.chunks[i], float(scores[i])) for i in order[:k]]
        return [h for h in hits if h.score > 0 or self.embedder]

    def format_hits(self, hits: list[Hit]) -> str:
        if not hits:
            return ("NO_MATCH: No policy section is relevant to this query. The answer is not in the "
                    "ShopKart policy documents. Tell the customer you don't know; do not guess.")
        return "\n\n---\n\n".join(h.chunk.render() for h in hits)