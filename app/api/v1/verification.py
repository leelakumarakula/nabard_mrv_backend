"""Independent sample verification API (separate from internal review tasks)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, build_current_user, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Programme, User, VerificationFinding, VerificationSample, VerificationSampleItem
from app.schemas.common import Page, PageParams, paginate
from app.schemas.community import (
    DrawIn,
    SampleCompleteIn,
    SampleDetail,
    SampleIn,
    SampleItemOut,
    SampleItemUpdate,
    SampleOut,
)
from app.schemas.mrv import FindingIn, FindingOut
from app.services import crud
from app.services import verification as service

router = APIRouter(prefix="/verification-samples", tags=["independent verification"])


def _counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    out: dict[uuid.UUID, dict[str, int]] = {}
    for sample_id, result, n in db.execute(select(VerificationSampleItem.sample_id, VerificationSampleItem.result, func.count())
                                           .where(VerificationSampleItem.sample_id.in_(ids or [uuid.UUID(int=0)]))
                                           .group_by(VerificationSampleItem.sample_id, VerificationSampleItem.result)):
        out.setdefault(sample_id, {})[result] = n
    return out


def _detail(db: Session, sample: VerificationSample) -> SampleDetail:
    items = list(db.scalars(select(VerificationSampleItem).where(VerificationSampleItem.sample_id == sample.id)
                            .order_by(VerificationSampleItem.entity_label)))
    findings = list(db.scalars(select(VerificationFinding).where(VerificationFinding.verification_sample_id == sample.id)
                               .order_by(VerificationFinding.created_at)))
    base = SampleOut.model_validate(sample).model_copy(update={"result_counts": _counts(db, [sample.id]).get(sample.id, {})})
    return SampleDetail(**base.model_dump(), items=[SampleItemOut.model_validate(i) for i in items],
                        findings=[FindingOut.model_validate(f).model_dump(mode="json") for f in findings])


@router.get("", response_model=Page[SampleOut])
def list_samples(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, status: str | None = None,
                 current: CurrentUser = Depends(require("verification.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(VerificationSample).order_by(VerificationSample.created_at.desc())
    if programme_id:
        stmt = stmt.where(VerificationSample.programme_id == programme_id)
    if status:
        stmt = stmt.where(VerificationSample.status == status)
    items, total = paginate(db, stmt, params, unique=False)
    counts = _counts(db, [s.id for s in items])
    return {"items": [SampleOut.model_validate(s).model_copy(update={"result_counts": counts.get(s.id, {})}) for s in items],
            "total": total, "page": params.page, "page_size": params.page_size}


@router.post("", response_model=SampleDetail, status_code=201)
def create_sample(body: SampleIn, current: CurrentUser = Depends(require("verification.sample")),
                  db: Session = Depends(get_db)) -> SampleDetail:
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    if body.verifier_id:
        verifier = crud.get_or_404(db, User, body.verifier_id, "Verifier")
        if not build_current_user(db, verifier).has("verification.sample"):
            raise AppError(422, "invalid_verifier", "The assigned user is not an independent verifier")
    data = body.model_dump(mode="json")
    data["programme_id"], data["reporting_period_id"] = body.programme_id, body.reporting_period_id
    data["verifier_id"] = body.verifier_id or current.id
    sample = crud.create(db, current, VerificationSample, data, "verification_sample", summary=body.title)
    service.refresh_frame_size(db, sample)
    db.commit()
    return _detail(db, sample)


@router.get("/{sample_id}", response_model=SampleDetail)
def get_sample(sample_id: uuid.UUID, current: CurrentUser = Depends(require("verification.read")),
               db: Session = Depends(get_db)) -> SampleDetail:
    return _detail(db, crud.get_or_404(db, VerificationSample, sample_id, "Verification sample"))


@router.get("/{sample_id}/frame")
def sample_frame(sample_id: uuid.UUID, params: PageParams = Depends(), current: CurrentUser = Depends(require("verification.sample")),
                 db: Session = Depends(get_db)) -> dict:
    """Records eligible for the sample – used to pick records for purposive selection."""
    sample = crud.get_or_404(db, VerificationSample, sample_id, "Verification sample")
    items, total = paginate(db, service.frame_query(db, sample), params)
    return {"items": [{"id": e.id, "label": service.label_for(e)} for e in items], "total": total, "page": params.page,
            "page_size": params.page_size}


@router.post("/{sample_id}/draw", response_model=SampleDetail)
def draw_sample(sample_id: uuid.UUID, body: DrawIn, current: CurrentUser = Depends(require("verification.sample")),
                db: Session = Depends(get_db)) -> SampleDetail:
    sample = crud.get_or_404(db, VerificationSample, sample_id, "Verification sample")
    service.draw(db, current, sample, body.entity_ids)
    db.commit()
    return _detail(db, sample)


@router.patch("/items/{item_id}", response_model=SampleItemOut)
def record_item(item_id: uuid.UUID, body: SampleItemUpdate, current: CurrentUser = Depends(require("verification.sample")),
                db: Session = Depends(get_db)) -> VerificationSampleItem:
    item = crud.get_or_404(db, VerificationSampleItem, item_id, "Sample item")
    sample = db.get(VerificationSample, item.sample_id)
    service.record_result(db, current, item, sample, body.model_dump())
    db.commit()
    return item


@router.post("/{sample_id}/findings", response_model=FindingOut, status_code=201)
def add_finding(sample_id: uuid.UUID, body: FindingIn, current: CurrentUser = Depends(require("verification.sample")),
                db: Session = Depends(get_db)) -> VerificationFinding:
    sample = crud.get_or_404(db, VerificationSample, sample_id, "Verification sample")
    finding = crud.create(db, current, VerificationFinding, {**body.model_dump(), "verification_sample_id": sample.id,
                                                             "is_demo": sample.is_demo}, "verification_finding")
    db.commit()
    return finding


@router.post("/{sample_id}/complete", response_model=SampleDetail)
def complete_sample(sample_id: uuid.UUID, body: SampleCompleteIn, current: CurrentUser = Depends(require("verification.sample")),
                    db: Session = Depends(get_db)) -> SampleDetail:
    sample = crud.get_or_404(db, VerificationSample, sample_id, "Verification sample")
    service.complete(db, current, sample, body.conclusion, body.conclusion_note)
    db.commit()
    return _detail(db, sample)
