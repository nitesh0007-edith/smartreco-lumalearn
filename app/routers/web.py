import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import current_user, optional_user
from app.models import ActivityEvent, AgentRun, CartItem, Product, Purchase, User
from app.render import render
from app.security import add_flash, validate_csrf
from app.services.behavior import build_behavior_profile
from app.services.recommendations import RecommendationService, latest_recommendation

router = APIRouter(tags=["platform"])


@router.get("/")
def catalog(
    request: Request,
    q: str = Query(default="", max_length=120),
    category: str = Query(default="", max_length=80),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    statement = select(Product).where(Product.is_active.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Product.title.ilike(pattern),
                Product.description.ilike(pattern),
                Product.tags_json.ilike(pattern),
            )
        )
    if category:
        statement = statement.where(Product.category == category)
    products = list(db.scalars(statement.order_by(Product.created_at.desc())))
    categories = list(
        db.scalars(
            select(Product.category)
            .where(Product.is_active.is_(True))
            .distinct()
            .order_by(Product.category)
        )
    )
    recommendation = latest_recommendation(db, user.id) if user else None
    return render(
        request,
        "home.html",
        user=user,
        page_title="Courses that follow your curiosity",
        products=products,
        categories=categories,
        query=q,
        selected_category=category,
        recommendation=recommendation,
    )


@router.get("/products/{slug}")
def product_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    product = db.scalar(select(Product).where(Product.slug == slug, Product.is_active.is_(True)))
    if not product:
        raise HTTPException(status_code=404, detail="Course not found")
    related = list(
        db.scalars(
            select(Product)
            .where(
                Product.category == product.category,
                Product.id != product.id,
                Product.is_active.is_(True),
            )
            .limit(3)
        )
    )
    return render(
        request,
        "product.html",
        user=user,
        page_title=product.title,
        product=product,
        related=related,
    )


@router.get("/for-you")
def for_you(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    recommendation = latest_recommendation(db, user.id)
    event_count = (
        db.scalar(select(func.count(ActivityEvent.id)).where(ActivityEvent.user_id == user.id)) or 0
    )
    return render(
        request,
        "for_you.html",
        user=user,
        page_title="For you",
        recommendation=recommendation,
        event_count=event_count,
    )


@router.get("/your-signal")
def your_signal(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    profile = build_behavior_profile(db, user.id)
    runs = list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.user_id == user.id)
            .order_by(AgentRun.started_at.desc())
            .limit(8)
        )
    )
    return render(
        request,
        "signal.html",
        user=user,
        page_title="Your learning signal",
        profile=profile,
        runs=runs,
    )


@router.get("/learning")
def learning_dashboard(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    purchases = list(
        db.scalars(
            select(Purchase)
            .where(Purchase.user_id == user.id)
            .options(selectinload(Purchase.product))
            .order_by(Purchase.purchased_at.desc())
        )
    )
    cart_items = list(
        db.scalars(
            select(CartItem)
            .where(CartItem.user_id == user.id)
            .options(selectinload(CartItem.product))
            .order_by(CartItem.added_at.desc())
        )
    )
    events = (
        db.scalar(select(func.count(ActivityEvent.id)).where(ActivityEvent.user_id == user.id)) or 0
    )
    return render(
        request,
        "learning.html",
        user=user,
        page_title="My learning",
        purchases=purchases,
        cart_items=cart_items,
        event_count=events,
    )


@router.get("/profile")
def profile_page(request: Request, user: User = Depends(current_user)):
    return render(request, "profile.html", user=user, page_title="Your profile")


@router.post("/profile")
async def update_profile(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf_token", "")))
    name = " ".join(str(form.get("name", "")).split())
    target_role = " ".join(str(form.get("target_role", "")).split()) or None
    if len(name) < 2 or len(name) > 100:
        add_flash(request, "Please enter a name between 2 and 100 characters.", "error")
    else:
        user.name = name
        user.target_role = target_role
        db.commit()
        background.add_task(
            RecommendationService().maybe_refresh,
            user.id,
            trigger="profile_update",
            force=True,
        )
        add_flash(request, "Profile updated.", "success")
    return RedirectResponse("/profile", status_code=303)


@router.get("/how-it-works")
def how_it_works(request: Request, user: User | None = Depends(optional_user)):
    return render(request, "how_it_works.html", user=user, page_title="How LumaLearn works")


@router.get("/cart")
def cart(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    items = list(
        db.scalars(
            select(CartItem).where(CartItem.user_id == user.id).order_by(CartItem.added_at.desc())
        )
    )
    return render(request, "cart.html", user=user, page_title="Your cart", items=items)


@router.post("/cart/add/{product_id}")
async def add_to_cart(
    product_id: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf_token", "")))
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.is_active.is_(True))
    )
    if not product:
        raise HTTPException(status_code=404, detail="Course not found")
    already_owned = db.scalar(
        select(Purchase.id).where(Purchase.user_id == user.id, Purchase.product_id == product.id)
    )
    if already_owned:
        add_flash(request, "You already own this course.", "info")
    elif not db.scalar(
        select(CartItem.id).where(CartItem.user_id == user.id, CartItem.product_id == product.id)
    ):
        db.add(CartItem(user_id=user.id, product_id=product.id))
        db.add(
            ActivityEvent(
                user_id=user.id,
                product_id=product.id,
                client_event_id=str(uuid.uuid4()),
                event_type="cart_add",
                path=f"/products/{product.slug}",
                created_at=datetime.now(UTC),
                metadata_json='{"source":"cart"}',
            )
        )
        db.commit()
        background.add_task(RecommendationService().maybe_refresh, user.id, trigger="cart_add")
        add_flash(
            request, "Added to your cart. Your next recommendation will account for it.", "success"
        )
    return RedirectResponse(request.headers.get("referer") or "/cart", status_code=303)


@router.post("/cart/remove/{product_id}")
async def remove_from_cart(
    product_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf_token", "")))
    item = db.scalar(
        select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product_id)
    )
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse("/cart", status_code=303)


@router.post("/checkout")
async def checkout(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf_token", "")))
    code = str(form.get("discount_code", "")).strip().upper()
    items = list(db.scalars(select(CartItem).where(CartItem.user_id == user.id)))
    if not items:
        add_flash(request, "Your cart is empty.", "info")
        return RedirectResponse("/cart", status_code=303)
    for item in items:
        amount = Decimal("0") if code == "MESH_FREE" else Decimal(str(item.product.price))
        db.add(
            Purchase(
                user_id=user.id,
                product_id=item.product_id,
                amount_paid=amount,
                discount_code=code or None,
            )
        )
        db.add(
            ActivityEvent(
                user_id=user.id,
                product_id=item.product_id,
                client_event_id=str(uuid.uuid4()),
                event_type="purchase",
                path="/checkout",
                created_at=datetime.now(UTC),
                metadata_json='{"source":"checkout"}',
            )
        )
        db.delete(item)
    db.commit()
    background.add_task(
        RecommendationService().maybe_refresh, user.id, trigger="purchase", force=True
    )
    add_flash(
        request,
        "Purchase complete. Your learning signal now prioritizes complementary courses.",
        "success",
    )
    return RedirectResponse("/for-you", status_code=303)
