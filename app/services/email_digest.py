import smtplib
from email.message import EmailMessage
from html import escape

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import DigestDelivery, Recommendation, User
from app.services.recommendations import RecommendationService, latest_recommendation


class EmailDeliveryService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.smtp_host and self.settings.smtp_from_email)

    def send(self, user: User, recommendation: Recommendation) -> str:
        if not self.configured:
            raise RuntimeError("SMTP delivery is not configured")
        product_lines = "".join(
            f"<li><strong>{escape(item.product.title)}</strong> — {escape(item.reason)}</li>"
            for item in recommendation.items
        )
        message = EmailMessage()
        message["Subject"] = f"LumaLearn: {recommendation.headline}"
        message["From"] = self.settings.smtp_from_email
        message["To"] = user.email
        message.set_content(
            f"{recommendation.headline}\n\n{recommendation.narrative}\n\n"
            + "\n".join(f"• {item.product.title}: {item.reason}" for item in recommendation.items)
        )
        message.add_alternative(
            f"""
            <div style="font-family:Inter,Arial,sans-serif; max-width:640px;
                        margin:auto; color:#191825">
              <p style="color:#6d5dfc;font-weight:700">LumaLearn · your learning signal</p>
              <h1>{escape(recommendation.headline)}</h1>
              <p style="font-size:17px;line-height:1.65">{escape(recommendation.narrative)}</p>
              <ol style="line-height:1.65">{product_lines}</ol>
            </div>
            """,
            subtype="html",
        )
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
            if self.settings.smtp_use_tls:
                smtp.starttls()
            if self.settings.smtp_username:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password or "")
            response = smtp.send_message(message)
        return "accepted" if not response else f"partial:{len(response)}"


class DigestService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.email = EmailDeliveryService(self.settings)

    def run_daily(self) -> dict[str, int]:
        stats = {"eligible": 0, "sent": 0, "skipped": 0, "failed": 0}
        with SessionLocal() as db:
            user_ids = list(
                db.scalars(
                    select(User.id).where(User.is_active.is_(True), User.digest_opt_in.is_(True))
                )
            )
        for user_id in user_ids:
            stats["eligible"] += 1
            RecommendationService(self.settings).maybe_refresh(user_id, trigger="daily_digest")
            with SessionLocal() as db:
                user = db.get(User, user_id)
                recommendation = latest_recommendation(db, user_id)
                if not user or not recommendation:
                    stats["skipped"] += 1
                    continue
                already_sent = db.scalar(
                    select(DigestDelivery).where(
                        DigestDelivery.user_id == user_id,
                        DigestDelivery.recommendation_id == recommendation.id,
                    )
                )
                if already_sent:
                    stats["skipped"] += 1
                    continue
                delivery = DigestDelivery(
                    user_id=user_id,
                    recommendation_id=recommendation.id,
                    status="sending",
                )
                db.add(delivery)
                db.commit()
                try:
                    delivery.provider_message_id = self.email.send(user, recommendation)
                    delivery.status = "sent"
                    stats["sent"] += 1
                except Exception as exc:
                    delivery.status = "skipped" if not self.email.configured else "failed"
                    delivery.error = str(exc)[:900]
                    stats[delivery.status] += 1
                db.commit()
        return stats
