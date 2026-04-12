"""ChromaDB vector store for the N184 Memory Palace.

Manages the seven hall collections with verbatim document storage,
semantic similarity search, and metadata filtering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from n184_memory_palace.config import CHROMADB_PATH, HALLS


class ChromaDBStore:
    """Manages ChromaDB collections (the Seven Halls)."""

    def __init__(self, persist_dir: Path | str | None = None) -> None:
        self.persist_dir = Path(persist_dir) if persist_dir else CHROMADB_PATH
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client: chromadb.ClientAPI | None = None
        self._collections: dict[str, chromadb.Collection] = {}

    @property
    def client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        return self._client

    def initialize(self) -> None:
        """Create or get all seven hall collections."""
        for hall_name, info in HALLS.items():
            collection = self.client.get_or_create_collection(
                name=info["collection"],
                metadata={"description": info["description"], "hall": hall_name},
            )
            self._collections[hall_name] = collection

    def get_collection(self, hall_name: str) -> chromadb.Collection:
        """Get a hall collection by logical name."""
        if hall_name not in self._collections:
            if hall_name not in HALLS:
                raise ValueError(
                    f"Unknown hall '{hall_name}'. "
                    f"Valid halls: {', '.join(HALLS.keys())}"
                )
            self._collections[hall_name] = self.client.get_or_create_collection(
                name=HALLS[hall_name]["collection"],
                metadata={
                    "description": HALLS[hall_name]["description"],
                    "hall": hall_name,
                },
            )
        return self._collections[hall_name]

    def add(
        self,
        hall_name: str,
        doc_id: str,
        document: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a document to a hall collection."""
        collection = self.get_collection(hall_name)
        # ChromaDB metadata values must be str, int, float, or bool.
        # Serialize lists/dicts in metadata to JSON strings.
        clean_meta = _clean_metadata(metadata) if metadata else {}
        collection.add(
            ids=[doc_id],
            documents=[document],
            metadatas=[clean_meta],
        )

    def query(
        self,
        hall_name: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Query a hall collection by semantic similarity.

        Returns dict with keys: ids, documents, metadatas, distances.
        Each value is a list of lists (ChromaDB batch format).
        """
        collection = self.get_collection(hall_name)

        kwargs: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": min(n_results, collection.count() or 1),
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document

        # Avoid querying empty collections
        if collection.count() == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        return collection.query(**kwargs)

    def multi_hall_query(
        self,
        hall_names: list[str],
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Query multiple halls and return results keyed by hall name."""
        results = {}
        for hall_name in hall_names:
            results[hall_name] = self.query(
                hall_name=hall_name,
                query_text=query_text,
                n_results=n_results,
                where=where,
            )
        return results

    def update(
        self,
        hall_name: str,
        doc_id: str,
        document: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update an existing document in a hall collection."""
        collection = self.get_collection(hall_name)
        kwargs: dict[str, Any] = {"ids": [doc_id]}
        if document is not None:
            kwargs["documents"] = [document]
        if metadata is not None:
            kwargs["metadatas"] = [_clean_metadata(metadata)]
        collection.update(**kwargs)

    def delete(self, hall_name: str, doc_id: str) -> None:
        """Delete a document from a hall collection."""
        collection = self.get_collection(hall_name)
        collection.delete(ids=[doc_id])

    def get(
        self, hall_name: str, doc_id: str
    ) -> dict[str, Any] | None:
        """Get a specific document by ID."""
        collection = self.get_collection(hall_name)
        result = collection.get(ids=[doc_id])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "document": result["documents"][0] if result["documents"] else None,
            "metadata": result["metadatas"][0] if result["metadatas"] else None,
        }

    def count(self, hall_name: str) -> int:
        """Return the number of documents in a hall."""
        return self.get_collection(hall_name).count()

    def counts(self) -> dict[str, int]:
        """Return document counts for all halls."""
        return {name: self.count(name) for name in HALLS}

    def list_all(self, hall_name: str) -> dict[str, Any]:
        """List all documents in a hall (use sparingly on large collections)."""
        collection = self.get_collection(hall_name)
        return collection.get()


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Ensure metadata values are ChromaDB-compatible (str, int, float, bool).

    Lists and dicts are JSON-serialized. None values are dropped.
    """
    import json

    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (list, dict)):
            cleaned[key] = json.dumps(value)
        else:
            cleaned[key] = str(value)
    return cleaned
