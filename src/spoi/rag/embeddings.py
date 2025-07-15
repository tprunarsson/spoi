"""
rag/embeddings.py

Handles loading and caching of embedding models for RAG matching.
"""

from sentence_transformers import SentenceTransformer

_EMBED_MODEL = None
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

def get_embed_model():
    """
    Returns a singleton embedding model for use in RAG searches.
    """
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        _EMBED_MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _EMBED_MODEL
