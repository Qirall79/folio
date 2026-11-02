from typing import Annotated
from pydantic import BaseModel, Field, StringConstraints


class QueryInput(BaseModel):
    query: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1)]
    top_k: int = Field(default=5, gt=0, le=20)
