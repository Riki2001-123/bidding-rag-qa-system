from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import User
from app.schemas.common import SearchResultOut
from app.schemas.search import SearchResponse
from app.services.retrieval import get_attachments, search_domain


router = APIRouter()
SEARCH_DOMAINS = ("policy", "tender", "enterprise")


def _to_response(items, db: Session, user: User):
    results = []
    for item in items:
        results.append(
            SearchResultOut(
                domain=item.domain,
                record_id=item.record_id,
                score=item.score,
                title=item.title,
                summary=item.summary,
                publish_date=item.publish_date,
                key_fields=item.key_fields,
                attachments=get_attachments(db, item.domain, item.record_id, user),
            )
        )
    return SearchResponse(items=results)


def _sorted_items(items):
    return sorted(items, key=lambda item: (-float(item.score), item.domain, item.record_id))


# 路由装饰器，定义GET请求路径为"/policy"，返回模型为SearchResponse
@router.get("/policy", response_model=SearchResponse)
def search_policy(
    q: Optional[str] = None,          # 可选查询参数，用于搜索关键词
    region: Optional[str] = None,     # 可选参数，用于指定地区
    project_id: Optional[int] = None, # 可选参数，用于指定项目ID
    top_k: int = Query(default=10, ge=1, le=50),  # 整数参数，默认值为10，用于指定返回结果数量
    db: Session = Depends(get_db),     # 数据库会话依赖，通过get_db函数获取
    current_user: User = Depends(get_current_user),  # 当前用户依赖，通过get_current_user函数获取
):
    # 调用search_domain函数进行搜索，传入数据库会话、用户信息和搜索参数
    items = search_domain(db, "policy", current_user, q=q, region=region, project_id=project_id, top_k=top_k)
    # 将搜索结果转换为响应格式并返回
    return _to_response(items, db, current_user)


@router.get("/tender", response_model=SearchResponse)
def search_tender(
    q: Optional[str] = None,
    stage: Optional[str] = None,
    tenderer: Optional[str] = None,
    region: Optional[str] = None,
    project_id: Optional[int] = None,
    top_k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = search_domain(
        db,
        "tender",
        current_user,
        q=q,
        stage=stage,
        tenderer=tenderer,
        region=region,
        project_id=project_id,
        top_k=top_k,
    )
    return _to_response(items, db, current_user)


@router.get("/enterprise", response_model=SearchResponse)
def search_enterprise(
    q: Optional[str] = None,
    region: Optional[str] = None,
    industry: Optional[str] = None,
    project_id: Optional[int] = None,
    top_k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = search_domain(
        db,
        "enterprise",
        current_user,
        q=q,
        region=region,
        industry=industry,
        project_id=project_id,
        top_k=top_k,
    )
    return _to_response(items, db, current_user)


@router.get("/all", response_model=SearchResponse)
def search_all(
    q: Optional[str] = None,
    project_id: Optional[int] = None,
    top_k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = []
    for domain in SEARCH_DOMAINS:
        items.extend(
            search_domain(
                db=db,
                domain=domain,
                user=current_user,
                q=q,
                project_id=project_id,
                top_k=top_k,
            )
        )
    return _to_response(_sorted_items(items), db, current_user)
