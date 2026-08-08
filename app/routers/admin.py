from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import admin_user
from app.models import (
    ActivityEvent,
    AgentRun,
    CartItem,
    Product,
    Purchase,
    Recommendation,
    User,
    VectorSyncJob,
)
from app.render import render
from app.schemas import ProductForm
from app.security import add_flash, validate_csrf
from app.services.behavior import build_behavior_profile
from app.services.catalog import CatalogService, VectorSyncService
from app.services.recommendations import latest_recommendation

router = APIRouter(prefix="/admin", tags=["administration"])


def process_sync_job(job_id: str) -> None:
    VectorSyncService().process_job(job_id)


def _product_payload(form) -> ProductForm:  # type: ignore[no-untyped-def]
    return ProductForm(
        title=str(form.get("title", "")),
        description=str(form.get("description", "")),
        category=str(form.get("category", "")),
        level=str(form.get("level", "")),
        price=Decimal(str(form.get("price", "0"))),
        duration_minutes=int(str(form.get("duration_minutes", "0"))),
        tags=[tag.strip() for tag in str(form.get("tags", "")).split(",") if tag.strip()],
        accent=str(form.get("accent", "violet")),
    )


@router.get("")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    stats = {
        "products": db.scalar(select(func.count(Product.id)).where(Product.is_active.is_(True)))
        or 0,
        "users": db.scalar(select(func.count(User.id)).where(User.role == "user")) or 0,
        "events": db.scalar(select(func.count(ActivityEvent.id))) or 0,
        "recommendations": db.scalar(select(func.count(Recommendation.id))) or 0,
    }
    products = list(db.scalars(select(Product).order_by(Product.updated_at.desc()).limit(20)))
    jobs = list(
        db.scalars(select(VectorSyncJob).order_by(VectorSyncJob.created_at.desc()).limit(10))
    )
    runs = list(db.scalars(select(AgentRun).order_by(AgentRun.started_at.desc()).limit(10)))
    learners = list(
        db.scalars(
            select(User)
            .where(User.role == "user")
            .order_by(User.last_login_at.desc().nullslast(), User.created_at.desc())
            .limit(50)
        )
    )
    learner_stats = {
        learner.id: {
            "events": db.scalar(
                select(func.count(ActivityEvent.id)).where(ActivityEvent.user_id == learner.id)
            )
            or 0,
            "recommendations": db.scalar(
                select(func.count(Recommendation.id)).where(Recommendation.user_id == learner.id)
            )
            or 0,
        }
        for learner in learners
    }
    return render(
        request,
        "admin/dashboard.html",
        user=user,
        page_title="Admin overview",
        stats=stats,
        products=products,
        jobs=jobs,
        runs=runs,
        learners=learners,
        learner_stats=learner_stats,
    )


@router.get("/learners/{user_id}")
def learner_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    learner = db.scalar(select(User).where(User.id == user_id, User.role == "user"))
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")
    profile = build_behavior_profile(db, learner.id)
    events = list(
        db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.user_id == learner.id)
            .options(selectinload(ActivityEvent.product))
            .order_by(ActivityEvent.id.desc())
            .limit(20)
        )
    )
    cart_items = list(
        db.scalars(
            select(CartItem)
            .where(CartItem.user_id == learner.id)
            .options(selectinload(CartItem.product))
        )
    )
    purchases = list(
        db.scalars(
            select(Purchase)
            .where(Purchase.user_id == learner.id)
            .options(selectinload(Purchase.product))
            .order_by(Purchase.purchased_at.desc())
        )
    )
    runs = list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.user_id == learner.id)
            .order_by(AgentRun.started_at.desc())
            .limit(10)
        )
    )
    recommendation = latest_recommendation(db, learner.id)
    return render(
        request,
        "admin/learner_detail.html",
        user=user,
        page_title=f"Learner · {learner.name}",
        learner=learner,
        profile=profile,
        events=events,
        cart_items=cart_items,
        purchases=purchases,
        runs=runs,
        recommendation=recommendation,
    )


@router.get("/products/new")
def new_product_page(request: Request, user: User = Depends(admin_user)):
    return render(
        request,
        "admin/product_form.html",
        user=user,
        page_title="Add course",
        product=None,
    )


@router.post("/products/new")
async def create_product(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        payload = _product_payload(form)
    except (ValidationError, ValueError) as exc:
        return render(
            request,
            "admin/product_form.html",
            user=user,
            page_title="Add course",
            product=None,
            values=dict(form),
            error=str(exc),
            status_code=422,
        )
    product, job = CatalogService(db).create(payload)
    background.add_task(process_sync_job, job.id)
    add_flash(request, f"{product.title} saved. Vector sync is queued.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.get("/products/{product_id}/edit")
def edit_product_page(
    product_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return render(
        request,
        "admin/product_form.html",
        user=user,
        page_title=f"Edit {product.title}",
        product=product,
    )


@router.post("/products/{product_id}/edit")
async def edit_product(
    product_id: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        payload = _product_payload(form)
    except (ValidationError, ValueError) as exc:
        return render(
            request,
            "admin/product_form.html",
            user=user,
            page_title=f"Edit {product.title}",
            product=product,
            values=dict(form),
            error=str(exc),
            status_code=422,
        )
    job = CatalogService(db).update(product, payload)
    background.add_task(process_sync_job, job.id)
    add_flash(request, f"{product.title} updated and re-indexing queued.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/products/{product_id}/delete")
async def delete_product(
    product_id: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    job = CatalogService(db).delete(product)
    background.add_task(process_sync_job, job.id)
    add_flash(request, f"{product.title} removed; vector deletion is queued.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/vector-sync")
async def reconcile_vectors(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(admin_user),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    background.add_task(VectorSyncService().process_pending, 100)
    add_flash(request, "Vector reconciliation started in the background.", "info")
    return RedirectResponse("/admin", status_code=303)
