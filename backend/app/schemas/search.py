from typing import List

from pydantic import BaseModel

from app.schemas.common import SearchResultOut


class SearchResponse(BaseModel):
    items: List[SearchResultOut]

