from pydantic import BaseModel

class Source(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    text: str
    reranking_score: float

class QueryOutput(BaseModel):
    answer: str
    sources: list[Source]
