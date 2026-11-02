from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from rq import get_current_job
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.node_parser import SentenceWindowNodeParser
from llama_index.readers.file import PDFReader
from llama_index.core import StorageContext, load_index_from_storage
from src.text_splitter import split_text_preserving_lists
from src.index_storage import INDEX_DIR, INDEX_LOCK
from src.db import get_document, update_document_status
from src.observability import finish_ingestion, start_ingestion


def ingest(filepath: str, document_id: str, filename: str, owner_id: str):
    record = get_document(document_id=document_id, owner_id=owner_id)
    if record is None:
        raise LookupError("Document not found for this owner")
    filepath = record["file_path"]
    filename = record["original_filename"]
    started = perf_counter()
    job = get_current_job()
    queued_at = job.enqueued_at if job else None
    if queued_at is None and record.get("created_at"):
        try:
            queued_at = datetime.fromisoformat(record["created_at"])
        except ValueError:
            queued_at = None
    if queued_at is not None and queued_at.tzinfo is None:
        queued_at = queued_at.replace(tzinfo=timezone.utc)
    queue_ms = max(0, (datetime.now(timezone.utc) - queued_at).total_seconds() * 1000) if queued_at else None
    try:
        file_bytes = Path(filepath).stat().st_size
    except OSError:
        file_bytes = None
    event_id = start_ingestion(
        owner_id, document_id, job.id if job else record.get("job_id"), queue_ms, file_bytes
    )
    chunk_count = None

    try:
        update_document_status(
            document_id=document_id,
            owner_id=owner_id,
            status='processing',
        )

        reader = PDFReader(return_full_document=True)

        documents = reader.load_data(filepath)

        for document in documents:
            document.id_ = document_id
            document.metadata["file_name"] = filename
            document.metadata["owner_id"] = owner_id
            document.excluded_embed_metadata_keys.append("owner_id")
            document.excluded_llm_metadata_keys.append("owner_id")

        text_splitter = SentenceWindowNodeParser(
            sentence_splitter=split_text_preserving_lists,
            window_size=2,
            include_metadata=True,
            include_prev_next_rel=True,
        )

        Settings.text_splitter = text_splitter

        Settings.embed_model = HuggingFaceEmbedding(
            model_name="BAAI/bge-small-en-v1.5"
        )

        with INDEX_LOCK:
            if INDEX_DIR.exists() and any(INDEX_DIR.iterdir()):
                storage_context = StorageContext.from_defaults(
                    persist_dir=str(INDEX_DIR)
                )
                index = load_index_from_storage(storage_context)

                for document in documents:
                    index.insert(document)
            else:
                index = VectorStoreIndex.from_documents(documents)

            index.storage_context.persist(persist_dir=str(INDEX_DIR))
            doc_info = index.docstore.get_ref_doc_info(document_id)
            if doc_info is not None:
                chunk_count = len(doc_info.node_ids)

        update_document_status(
            document_id=document_id,
            owner_id=owner_id,
            status='ready',
        )

        finish_ingestion(
            event_id, outcome="success", duration_ms=(perf_counter() - started) * 1000,
            chunk_count=chunk_count,
        )
        return {"status": "completed", "document_id": document_id, "filename": filename}
    except Exception as e:
        finish_ingestion(
            event_id, outcome="error", duration_ms=(perf_counter() - started) * 1000,
            chunk_count=chunk_count, error_type=type(e).__name__,
        )
        update_document_status(
            document_id=document_id,
            owner_id=owner_id,
            status='failed',
            error=type(e).__name__
        )

        raise
