import os
from functools import lru_cache
from threading import Lock
from llama_index.core import StorageContext, load_index_from_storage
from llama_index.core.callbacks import CallbackManager
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.llms.openrouter import OpenRouter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core.postprocessor import SentenceTransformerRerank, MetadataReplacementPostProcessor
from src.index_storage import INDEX_DIR, INDEX_LOCK, IndexNotReadyError
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
from src.query_metrics import QueryMetrics


GENERATION_MODEL = "openai/gpt-3.5-turbo-16k"
_reranker_lock = Lock()


@lru_cache(maxsize=1)
def _get_embed_model():
    return HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")


@lru_cache(maxsize=1)
def _get_reranker():
    return SentenceTransformerRerank(model="cross-encoder/ms-marco-MiniLM-L6-v2")


@lru_cache(maxsize=1)
def _load_index(signature):
    storage_context = StorageContext.from_defaults(persist_dir=str(INDEX_DIR))
    return load_index_from_storage(
        storage_context, embed_model=_get_embed_model(), callback_manager=CallbackManager()
    )


def _get_index():
    try:
        with INDEX_LOCK:
            for name in ("docstore.json", "index_store.json", "default__vector_store.json"):
                if not (INDEX_DIR / name).is_file():
                    raise FileNotFoundError(name)
            signature = []
            for path in sorted(INDEX_DIR.glob("*.json")):
                info = path.stat()
                signature.append((path.name, info.st_ino, info.st_size, info.st_mtime_ns))
            return _load_index(tuple(signature))
    except FileNotFoundError as exc:
        _load_index.cache_clear()
        raise IndexNotReadyError(
            "Saved index missing or incomplete. Upload a document first."
        ) from exc


def get_query_engine(owner_id: str, top_k: int = 5, metrics: QueryMetrics | None = None):
    if not owner_id:
        raise ValueError("An owner ID is required to query documents")

    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    index = _get_index()
    callback_manager = CallbackManager([metrics or QueryMetrics()])
    embed_model = _get_embed_model().model_copy(update={"callback_manager": CallbackManager()})
    with _reranker_lock:
        reranker = _get_reranker().model_copy(update={
            "top_n": top_k, "callback_manager": callback_manager,
        })

    metadata_processor = MetadataReplacementPostProcessor(
        target_metadata_key="window")

    llm = OpenRouter(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=GENERATION_MODEL,
        context_window=16_385,
        max_tokens=512,
        temperature=0,
        callback_manager=callback_manager,
        system_prompt=(
            "Answer only using the provided context. "
            "If the context does not support an answer, say so. "
            "Answer each part of the question independently. Provide information supported by the context, and explicitly identify which parts cannot be answered."
        ),
    )

    retriever = VectorIndexRetriever(
        index=index,
        similarity_top_k=20,
        node_ids=list(index.index_struct.nodes_dict.values()),
        embed_model=embed_model,
        callback_manager=callback_manager,
        filters=MetadataFilters(filters=[
            MetadataFilter(key="owner_id", value=owner_id),
        ]),
    )
    query_engine = RetrieverQueryEngine(
        retriever=retriever,
        callback_manager=callback_manager,
        response_synthesizer=get_response_synthesizer(
            llm=llm, response_mode="compact", callback_manager=callback_manager
        ),
        node_postprocessors=[reranker, metadata_processor],
    )

    return query_engine
