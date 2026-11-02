from pydantic import BaseModel


class DocumentOutput(BaseModel):
    id: str
    original_filename: str
    status: str
    job_id: str | None
    created_at: str
