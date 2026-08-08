import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Product
from app.services.mesh_gateway import MeshGateway
from app.services.vector_store import ChromaProductStore


class AgentState(TypedDict, total=False):
    user_id: int
    trace_id: str
    trigger: str
    profile: dict[str, Any]
    decision: str
    query_text: str
    candidates: list[dict[str, Any]]
    refinement_count: int
    retrieval_quality: float
    result: dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int
    node_trace: list[dict[str, Any]]


class RecommendationAgent:
    """Explicit LangGraph workflow with retrieval grading and one bounded refinement."""

    def __init__(
        self,
        db: Session,
        gateway: MeshGateway | None = None,
        vector_store: ChromaProductStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.gateway = gateway or MeshGateway(self.settings)
        self.vector_store = vector_store or ChromaProductStore(self.settings.chroma_path)
        self.graph = self._build_graph()

    @staticmethod
    def _trace(
        state: AgentState, node: str, started: float, **details: Any
    ) -> list[dict[str, Any]]:
        trace = list(state.get("node_trace", []))
        trace.append(
            {
                "node": node,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                **details,
            }
        )
        return trace

    def _analyze(self, state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        profile = state["profile"]
        decision = "retrieve" if profile["total_weight"] >= 2.0 else "insufficient_signal"
        return {
            "decision": decision,
            "query_text": profile["query_text"],
            "refinement_count": 0,
            "node_trace": self._trace(
                state,
                "analyze_behavior",
                started,
                event_count=profile["event_count"],
                weighted_intent=round(profile["total_weight"], 2),
                decision=decision,
            ),
        }

    @staticmethod
    def _route_after_analysis(state: AgentState) -> str:
        return "retrieve" if state["decision"] == "retrieve" else "end"

    def _retrieve(self, state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        vector = self.gateway.embed([state["query_text"]])[0]
        hits = self.vector_store.query(
            vector, limit=max(self.settings.recommendation_candidate_count * 2, 10)
        )
        hit_ids = [hit.product_id for hit in hits]
        products = {
            product.id: product
            for product in self.db.scalars(
                select(Product).where(Product.id.in_(hit_ids), Product.is_active.is_(True))
            )
        }
        top_categories = set(state["profile"]["top_categories"])
        target_role = state["profile"].get("target_role", "")
        viewed = set(state["profile"]["viewed_product_ids"])
        purchased = set(state["profile"].get("purchased_product_ids", []))
        cart = set(state["profile"].get("cart_product_ids", []))
        candidates: list[dict[str, Any]] = []
        for hit in hits:
            product = products.get(hit.product_id)
            if not product:
                continue
            if product.id in purchased:
                continue
            category_bonus = 0.12 if product.category in top_categories else 0.0
            role_bonus = (
                0.08
                if target_role
                and any(
                    word
                    in (product.title + " " + product.description + " " + product.tags_json).lower()
                    for word in target_role.lower().split()
                    if len(word) > 2
                )
                else 0.0
            )
            novelty_bonus = 0.05 if product.id not in viewed else 0.0
            cart_bonus = 0.08 if product.id in cart else 0.0
            score = min(
                1.0, hit.score * 0.83 + category_bonus + role_bonus + novelty_bonus + cart_bonus
            )
            candidates.append(
                {
                    "id": product.id,
                    "title": product.title,
                    "description": product.description,
                    "category": product.category,
                    "level": product.level,
                    "price": str(product.price),
                    "score": score,
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        candidates = candidates[: self.settings.recommendation_candidate_count]
        return {
            "candidates": candidates,
            "node_trace": self._trace(
                state,
                "semantic_retrieval",
                started,
                query=state["query_text"][:220],
                vector_hits=len(hits),
                active_candidates=len(candidates),
            ),
        }

    def _grade(self, state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        candidates = state.get("candidates", [])
        quality = sum(float(item["score"]) for item in candidates[:3]) / max(
            1, min(3, len(candidates))
        )
        return {
            "retrieval_quality": quality,
            "node_trace": self._trace(
                state,
                "grade_retrieval",
                started,
                quality=round(quality, 4),
                candidate_count=len(candidates),
            ),
        }

    @staticmethod
    def _route_after_grade(state: AgentState) -> str:
        if not state.get("candidates"):
            return "end"
        if state.get("retrieval_quality", 0) < 0.32 and state.get("refinement_count", 0) < 1:
            return "refine"
        return "generate"

    def _refine(self, state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        profile = state["profile"]
        focused_terms = profile["top_searches"][:3] + profile["top_categories"][:2]
        if not focused_terms:
            focused_terms = profile["top_topics"][:5]
        refined = "Find practical courses for: " + ", ".join(focused_terms)
        return {
            "query_text": refined,
            "refinement_count": state.get("refinement_count", 0) + 1,
            "node_trace": self._trace(state, "refine_query", started, refinement=refined[:220]),
        }

    def _generate(self, state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        generation = self.gateway.generate_recommendation(
            activity_summary=state["profile"]["summary"],
            candidates=state["candidates"],
            trace_id=state["trace_id"],
        )
        allowed = {item["id"] for item in state["candidates"]}
        score_by_id = {item["id"]: item["score"] for item in state["candidates"]}
        selected = []
        seen: set[str] = set()
        for item in generation.recommendation.recommendations:
            if item.product_id in allowed and item.product_id not in seen:
                selected.append(
                    {
                        "product_id": item.product_id,
                        "reason": item.reason,
                        "retrieval_score": score_by_id[item.product_id],
                    }
                )
                seen.add(item.product_id)
        if not selected:
            raise RuntimeError("Mesh did not select any retrieved catalog product")
        result = {
            "headline": generation.recommendation.headline,
            "narrative": generation.recommendation.narrative,
            "interest_summary": generation.recommendation.interest_summary,
            "items": selected,
        }
        return {
            "result": result,
            "model": generation.model,
            "prompt_tokens": generation.prompt_tokens,
            "completion_tokens": generation.completion_tokens,
            "node_trace": self._trace(
                state,
                "generate_persuasive_story",
                started,
                selected_count=len(selected),
                mesh_request_id=generation.request_id,
            ),
        }

    def _build_graph(self):  # type: ignore[no-untyped-def]
        workflow = StateGraph(AgentState)
        workflow.add_node("analyze", self._analyze)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("grade", self._grade)
        workflow.add_node("refine", self._refine)
        workflow.add_node("generate", self._generate)
        workflow.add_edge(START, "analyze")
        workflow.add_conditional_edges(
            "analyze", self._route_after_analysis, {"retrieve": "retrieve", "end": END}
        )
        workflow.add_edge("retrieve", "grade")
        workflow.add_conditional_edges(
            "grade",
            self._route_after_grade,
            {"refine": "refine", "generate": "generate", "end": END},
        )
        workflow.add_edge("refine", "retrieve")
        workflow.add_edge("generate", END)
        return workflow.compile()

    def run(
        self, *, user_id: int, trace_id: str, trigger: str, profile: dict[str, Any]
    ) -> AgentState:
        return self.graph.invoke(
            {
                "user_id": user_id,
                "trace_id": trace_id,
                "trigger": trigger,
                "profile": profile,
                "node_trace": [],
            }
        )
