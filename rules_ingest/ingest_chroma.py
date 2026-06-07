"""
ingest_chroma.py
Embeds all parsed rules into ChromaDB for semantic retrieval.
Used by the chatbot when structured lookups aren't enough.

Usage:
    python -m rules_ingest.ingest_chroma --md path/to/Duel_Masters_rules.md \
                            --chroma-path ./dm_chroma_db \
                            --openai-key sk-...

    # or without OpenAI — uses ChromaDB's built-in local embeddings:
    python -m rules_ingest.ingest_chroma --md path/to/Duel_Masters_rules.md \
                            --chroma-path ./dm_chroma_db
"""

import argparse

from rules_ingest.parser import parse_rules_md, Rule

COLLECTION_NAME = "dm_rules"
BATCH_SIZE      = 100          # ChromaDB prefers batches ≤ 166


# ── Build a rich document string for each rule ───────────────────────────────

def rule_to_document(rule: Rule) -> str:
    """
    Combine rule number + text into a single string.
    The LLM retrieves this whole string as context.
    """
    parts = [f"[Rule {rule.rule_number}]"]

    # add breadcrumb so the LLM knows the context
    cat = rule.rule_category.replace("_", " ").title()
    parts.append(f"Category: {cat}.")

    if rule.applies_in_phase and rule.applies_in_phase != ["any"]:
        phases = ", ".join(rule.applies_in_phase)
        parts.append(f"Applies during: {phases}.")

    if rule.applies_in_zone and rule.applies_in_zone != ["any"]:
        zones = ", ".join(rule.applies_in_zone)
        parts.append(f"Zone: {zones}.")

    parts.append(rule.text)
    return " ".join(parts)


def rule_to_metadata(rule: Rule) -> dict:
    """
    Flat metadata dict for ChromaDB filtering.
    ChromaDB only accepts str / int / float / bool values.
    """
    return {
        "rule_number":    rule.rule_number,
        "chapter":        rule.chapter_number,
        "section":        rule.section_number,
        "depth":          rule.depth,
        "rule_category":  rule.rule_category,
        "is_state_based": rule.is_state_based,
        "is_turn_based":  rule.is_turn_based,
        "is_keyword_rule":rule.is_keyword_rule,
        "priority":       rule.priority,
        # store lists as comma-separated strings (ChromaDB limitation)
        "applies_in_phase": ",".join(rule.applies_in_phase or ["any"]),
        "applies_in_zone":  ",".join(rule.applies_in_zone  or ["any"]),
        "has_parent":     rule.parent_rule is not None,
        "parent_rule":    rule.parent_rule or "",
    }


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _get_embedding_function(openai_key: str | None):
    from chromadb.utils import embedding_functions

    """
    Return the best available embedding function.
    Priority: OpenAI → sentence-transformers → ChromaDB default
    Falls back gracefully if a dependency is missing.
    """
    if openai_key:
        ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=openai_key,
            model_name="text-embedding-3-small",
        )
        print("  Using OpenAI  text-embedding-3-small")
        return ef

    # Try sentence-transformers (works fully offline after first download)
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        print("  Using sentence-transformers  all-MiniLM-L6-v2  (local)")
        return ef
    except Exception:
        pass

    # Final fallback — ChromaDB's own ONNX model
    ef = embedding_functions.DefaultEmbeddingFunction()
    print("  Using ChromaDB default embedding function")
    return ef


def ingest_to_chroma(
    rules:        list[Rule],
    chroma_path:  str,
    openai_key:   str | None = None,
):
    import chromadb

    # ── embedding function ───────────────────────────────────────────────────
    ef = _get_embedding_function(openai_key)

    # ── client + collection ──────────────────────────────────────────────────
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    existing_ids = set(collection.get(include=[])["ids"])
    print(f"  Existing docs in collection : {len(existing_ids)}")

    # ── batch upsert ─────────────────────────────────────────────────────────
    ids, docs, metas = [], [], []
    added   = 0
    updated = 0

    for rule in rules:
        doc  = rule_to_document(rule)
        meta = rule_to_metadata(rule)

        ids.append(rule.rule_number)
        docs.append(doc)
        metas.append(meta)

        if rule.rule_number in existing_ids:
            updated += 1
        else:
            added += 1

        if len(ids) >= BATCH_SIZE:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)
            ids, docs, metas = [], [], []

    if ids:
        collection.upsert(ids=ids, documents=docs, metadatas=metas)

    print(f"  ✔ Added   : {added}")
    print(f"  ✔ Updated : {updated}")
    print(f"  ✔ Total in collection: {collection.count()}")
    return collection


# ── Query helpers (used by the chatbot) ──────────────────────────────────────

class DMRulesRetriever:
    """
    Thin wrapper around the ChromaDB collection.
    Import this in your chatbot/LangGraph nodes.

    Usage:
        retriever = DMRulesRetriever("./dm_chroma_db")

        # Semantic search
        rules = retriever.search("can a creature attack the turn it's summoned?")

        # Filter by phase
        rules = retriever.search(
            "what happens when a shield is broken",
            phase="direct_attack",
            n=5
        )

        # Exact rule lookup
        rule = retriever.get_rule("509.5a")
    """

    def __init__(self, chroma_path: str, openai_key: str | None = None):
        import chromadb

        ef = _get_embedding_function(openai_key)

        client = chromadb.PersistentClient(path=chroma_path)
        self.collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )

    # ── core search ──────────────────────────────────────────────────────────

    def search(
        self,
        query:    str,
        n:        int              = 5,
        phase:    str | None       = None,
        zone:     str | None       = None,
        category: str | None       = None,
        chapter:  int | None       = None,
        state_based_only: bool     = False,
    ) -> list[dict]:
        """
        Semantic search with optional metadata filters.

        Returns list of dicts:
            {"rule_number": str, "text": str, "score": float, "metadata": dict}
        """
        where: dict = {}

        if state_based_only:
            where["is_state_based"] = True
        if phase:
            where["applies_in_phase"] = {"$contains": phase}
        if zone:
            where["applies_in_zone"] = {"$contains": zone}
        if category:
            where["rule_category"] = category
        if chapter is not None:
            where["chapter"] = chapter

        kwargs: dict = {
            "query_texts": [query],
            "n_results":   n,
            "include":     ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        output = []
        for i in range(len(results["ids"][0])):
            output.append({
                "rule_number": results["ids"][0][i],
                "text":        results["documents"][0][i],
                "score":       1 - results["distances"][0][i],  # cosine → similarity
                "metadata":    results["metadatas"][0][i],
            })
        return output

    # ── exact lookup ─────────────────────────────────────────────────────────

    def get_rule(self, rule_number: str) -> dict | None:
        """Fetch a single rule by its exact rule number."""
        result = self.collection.get(
            ids=[rule_number],
            include=["documents", "metadatas"],
        )
        if not result["ids"]:
            return None
        return {
            "rule_number": result["ids"][0],
            "text":        result["documents"][0],
            "metadata":    result["metadatas"][0],
        }

    # ── convenience methods for common game-engine queries ──────────────────

    def get_state_based_actions(self) -> list[dict]:
        """Return all state-based action rules ordered by priority."""
        results = self.collection.get(
            where={"is_state_based": True},
            include=["documents", "metadatas"],
        )
        rows = [
            {"rule_number": rid, "text": doc, "metadata": meta}
            for rid, doc, meta in zip(
                results["ids"],
                results["documents"],
                results["metadatas"],
            )
        ]
        rows.sort(key=lambda r: r["metadata"].get("priority", 100))
        return rows

    def get_phase_rules(self, phase: str) -> list[dict]:
        """Return all rules that apply during a given game phase."""
        return self.search(
            query=f"rules during {phase.replace('_', ' ')} step",
            n=20,
            phase=phase,
        )

    def get_keyword_rules(self) -> list[dict]:
        """Return all keyword definition rules (section 701)."""
        results = self.collection.get(
            where={"is_keyword_rule": True},
            include=["documents", "metadatas"],
        )
        return [
            {"rule_number": rid, "text": doc, "metadata": meta}
            for rid, doc, meta in zip(
                results["ids"],
                results["documents"],
                results["metadatas"],
            )
        ]

    def build_context_for_event(
        self,
        event_description: str,
        current_phase:     str,
        n:                 int = 8,
    ) -> str:
        """
        Build a rules-context block to inject into an LLM prompt.

        Example:
            ctx = retriever.build_context_for_event(
                "player wants to use Ninja Strike after opponent attacks",
                current_phase="attack_declare",
            )
            prompt = f"Rules context:\n{ctx}\n\nQuestion: {event_description}"
        """
        rules = self.search(
            query=event_description,
            n=n,
            phase=current_phase,
        )
        lines = [f"[{r['rule_number']}] {r['text']}" for r in rules]
        return "\n\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ingest DM rules into ChromaDB")
    ap.add_argument("--md",          required=True,  help="Path to Duel_Masters_rules.md")
    ap.add_argument("--chroma-path", required=True,  help="Directory for ChromaDB persistence")
    ap.add_argument("--openai-key",  default=None,   help="OpenAI API key (optional)")
    args = ap.parse_args()

    print("\n[1/2] Parsing markdown …")
    _, _, rules = parse_rules_md(args.md)
    print(f"      {len(rules)} rules parsed")

    print("\n[2/2] Upserting into ChromaDB …")
    ingest_to_chroma(rules, args.chroma_path, args.openai_key)

    # ── quick smoke-test ─────────────────────────────────────────────────────
    print("\n── Smoke test ───────────────────────────────────────────────────")
    retriever = DMRulesRetriever(args.chroma_path, args.openai_key)

    q = "can a creature attack the turn it is summoned"
    results = retriever.search(q, n=3)
    print(f"\nQuery: '{q}'")
    for r in results:
        print(f"  [{r['rule_number']}] score={r['score']:.3f}  {r['text'][:100]}…")

    print("\n✅  ChromaDB ingestion complete.\n")


if __name__ == "__main__":
    main()
