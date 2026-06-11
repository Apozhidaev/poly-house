from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request
from pymongo.asynchronous.database import AsyncDatabase


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db

class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


Db = Annotated[AsyncDatabase, Depends(get_db)]
Page = Annotated[Pagination, Depends()]
