import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.config import Settings, get_settings
from app.schemas import MeshRecommendation


class MeshNotConfiguredError(RuntimeError):
    pass


class MeshResponseError(RuntimeError):
    pass


@dataclass(slots=True)
class MeshGeneration:
    recommendation: MeshRecommendation
    model: str
    prompt_tokens: int
    completion_tokens: int
    request_id: str | None


class MeshGateway:
    """The only AI boundary in the application.

    Both generative and embedding calls use the sponsor-mandated Mesh base URL.
    Keeping this boundary explicit makes compliance straightforward to inspect.
    """

    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def configured(self) -> bool:
        return self.settings.mesh_configured or self._client is not None

    @property
    def client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        if not self.settings.mesh_configured:
            raise MeshNotConfiguredError("MESH_API_KEY is not configured")
        self._client = OpenAI(
            api_key=self.settings.mesh_api_key,
            base_url=self.settings.mesh_base_url,
            timeout=self.settings.mesh_timeout_seconds,
            max_retries=2,
        )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(
            model=self.settings.mesh_embedding_model,
            input=texts,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [item.embedding for item in ordered]
        if len(vectors) != len(texts):
            raise MeshResponseError("Mesh returned an unexpected number of embeddings")
        return vectors

    def generate_recommendation(
        self,
        *,
        activity_summary: str,
        candidates: list[dict[str, Any]],
        trace_id: str,
    ) -> MeshGeneration:
        candidate_payload = [
            {
                "product_id": item["id"],
                "title": item["title"],
                "category": item["category"],
                "level": item["level"],
                "price": item["price"],
                "description": item["description"],
                "retrieval_score": round(float(item["score"]), 4),
            }
            for item in candidates
        ]
        response = self.client.chat.completions.create(
            model=self.settings.mesh_chat_model,
            temperature=0.35,
            max_tokens=2400,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are LumaLearn's recommendation strategist. Create helpful, "
                        "persuasive learning guidance grounded exclusively in the supplied "
                        "catalog candidates. Reflect the learner's demonstrated interests "
                        "without mentioning tracking, surveillance, hidden data, or inventing "
                        "facts. Select 2-4 distinct candidate product IDs. Explain concrete fit "
                        "and progression; avoid hype and pressure. Keep the headline under 100 "
                        "characters, narrative under 350 characters, interest_summary under 180 "
                        "characters, each reason under 180 characters, and use exactly 2 "
                        "recommendations when possible. Return "
                        "ONLY valid JSON with the requested fields; do not add commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Behavioral learning brief:\n{activity_summary}\n\n"
                        "Retrieved catalog candidates (the only allowed products):\n"
                        f"{json.dumps(candidate_payload, ensure_ascii=False)}\n\n"
                        f"Trace: {trace_id}. Return the requested JSON schema."
                    ),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise MeshResponseError("Mesh returned an empty recommendation")
        try:
            # Some Mesh-hosted models wrap otherwise-valid JSON in markdown fences.
            normalized = content.strip()
            if normalized.startswith("```"):
                normalized = normalized.split("\n", 1)[1]
                normalized = normalized.rsplit("```", 1)[0].strip()
            parsed = MeshRecommendation.model_validate_json(normalized)
        except Exception as exc:
            raise MeshResponseError(
                f"Mesh returned invalid structured output: {str(exc)[:500]}"
            ) from exc

        usage = response.usage
        return MeshGeneration(
            recommendation=parsed,
            model=response.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            request_id=getattr(response, "_request_id", None),
        )
