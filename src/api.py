from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
from pathlib import Path
from fastapi import APIRouter, Depends, FastAPI, UploadFile, HTTPException, status, Response
import logging
import shutil
from src.ingest import ingest
from src.job_queue import queue
from uuid import uuid4
from src.utils import is_valid_pdf, calculate_file_hash
from rq.job import Job
from rq.exceptions import NoSuchJobError
from src.job_queue import redis_conn
from src.schemas.query_input import QueryInput
from src.answer import answer
from src.schemas.query_output import Source, QueryOutput
from src.index_storage import IndexNotReadyError, PROJECT_ROOT
from src.auth import CurrentUser, get_current_user, router as auth_router
from src.oauth import router as oauth_router
from src.admin import router as admin_router
from src.observability import RequestMetricsMiddleware, prune_runtime_logs
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from filelock import Timeout
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError
from redis.exceptions import RedisError
from src.db import (
    init_db, get_document_by_hash, create_document, update_document_status,
    list_documents, get_document, get_document_by_job,
)
from src.schemas.document_output import DocumentOutput
import sqlite3

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await asyncio.to_thread(prune_runtime_logs)

    async def retain_logs():
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(prune_runtime_logs)

    retention_task = asyncio.create_task(retain_logs())
    try:
        yield
    finally:
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestMetricsMiddleware)
router = APIRouter(dependencies=[Depends(get_current_user)])
app.include_router(auth_router)
app.include_router(oauth_router)
app.include_router(admin_router)

UPLOAD_DIR = PROJECT_ROOT / "storage" / "upload"
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)

MAX_SIZE_BYTES = 1024 * 1024 * 10


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(file: UploadFile, response: Response, user: CurrentUser):

    file_path = UPLOAD_DIR / f"{uuid4()}.pdf"
    document = None
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")

        content = await file.read(MAX_SIZE_BYTES + 1)
        await file.seek(0)

        if len(content) > MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=413, detail="File is too large. Uploaded file shouldn't exceed 10 MB.")

        file_hash = calculate_file_hash(content)

        existing_document = get_document_by_hash(file_hash=file_hash, owner_id=user.id)

        if existing_document is not None:
            if existing_document['status'] == 'failed':
                response.status_code = status.HTTP_409_CONFLICT
            elif existing_document['status'] == 'ready':
                response.status_code = status.HTTP_200_OK

            return {
                "document_id": existing_document['id'],
                "job_id": existing_document['job_id'],
                "status": existing_document['status'],
                "filename": existing_document['original_filename']
            }

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if not is_valid_pdf(file_path):
            raise HTTPException(
                status_code=400,
                detail="Invalid PDF file"
            )

        job_id = str(uuid4())

        try:

            document = create_document(
                owner_id=user.id,
                file_hash=file_hash,
                original_filename=file.filename,
                file_path=str(file_path),
                job_id=job_id
            )
        except sqlite3.IntegrityError:
            file_path.unlink(missing_ok=True)

            existing_document = get_document_by_hash(file_hash=file_hash, owner_id=user.id)

            if existing_document is None:
                raise

            if existing_document['status'] == 'failed':
                response.status_code = status.HTTP_409_CONFLICT
            elif existing_document['status'] == 'ready':
                response.status_code = status.HTTP_200_OK

            return {
                "document_id": existing_document['id'],
                "job_id": existing_document['job_id'],
                "status": existing_document['status'],
                "filename": existing_document['original_filename']
            }

        job = queue.enqueue(
            ingest,
            filepath=str(file_path),
            filename=file.filename,
            owner_id=user.id,
            job_timeout=600,
            job_id=job_id,
            document_id=document['id']
        )

        return {
            "document_id": document['id'],
            "job_id": job.id,
            "status": "queued",
            "filename": file.filename
        }
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise
    except RedisError as exc:
        if document is not None:
            update_document_status(
                document_id=document['id'],
                owner_id=user.id,
                status='failed',
                error="Ingestion queue is unavailable. Try again shortly."
            )
        else:
            file_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=503,
            detail="Ingestion queue is unavailable. Try again shortly.",
        ) from exc
    except Exception as exc:
        if document is not None:
            update_document_status(
                document_id=document['id'], owner_id=user.id,
                status='failed', error="Unable to enqueue document ingestion.",
            )
        else:
            file_path.unlink(missing_ok=True)
        logger.exception("Document upload failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to upload document.",
        ) from exc
    finally:
        await file.close()


@router.get("/documents", response_model=list[DocumentOutput])
def get_documents(user: CurrentUser):
    return list_documents(owner_id=user.id)


@router.get("/documents/{document_id}", response_model=DocumentOutput)
def get_document_details(document_id: str, user: CurrentUser):
    document = get_document(document_id=document_id, owner_id=user.id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: str, user: CurrentUser):
    document = get_document(document_id=document_id, owner_id=user.id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(document["file_path"]).resolve()
    if not path.is_relative_to(UPLOAD_DIR.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(path, media_type="application/pdf",
                        filename=document["original_filename"],
                        content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: CurrentUser):
    document = get_document_by_job(job_id=job_id, owner_id=user.id)
    if document is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        return {
            "job_id": job.id,
            "status": job.get_status(refresh=True),
            "result": {
                "document_id": document["id"],
                "status": document["status"],
                "filename": document["original_filename"],
            } if job.is_finished else None,
            "error": "Document ingestion failed." if job.is_failed else None
        }
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Job status service is unavailable. Try again shortly.",
        ) from exc
    except Exception as exc:
        logger.exception("Job status lookup failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve job status.",
        ) from exc


@router.post("/ask", response_model=QueryOutput)
def ask(body: QueryInput, user: CurrentUser):

    try:
        response = answer(
            query=body.query, top_k=body.top_k, owner_id=user.id
        )

        sources = []

        for source in response.source_nodes:
            sources.append(Source(
                document_id=source.node.ref_doc_id,
                filename=source.node.metadata.get("file_name"),
                chunk_id=source.node.node_id,
                text=source.node.get_content(),
                reranking_score=float(source.score) if source.score else 0.0,
            ))

        result = QueryOutput(
            answer=str(response),
            sources=sources
        )

        return result

    except HTTPException:
        raise
    except Timeout as exc:
        raise HTTPException(
            status_code=503,
            detail="Index is busy being updated. Try again shortly.",
        ) from exc
    except IndexNotReadyError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Answer provider timed out. Try again shortly.",
        ) from exc
    except (APIConnectionError, RateLimitError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Answer provider is temporarily unavailable. Try again shortly.",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="Answer provider request failed.",
        ) from exc
    except Exception as exc:
        logger.exception("Answer generation failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to generate an answer.",
        ) from exc


app.include_router(router)
app.include_router(auth_router, prefix="/api")
app.include_router(oauth_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(router, prefix="/api")

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
