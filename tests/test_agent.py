from decimal import Decimal

from app.config import Settings
from app.models import Product
from app.schemas import MeshRecommendation, MeshRecommendationItem
from app.services.agent import RecommendationAgent
from app.services.mesh_gateway import MeshGeneration
from app.services.vector_store import VectorHit


class FakeGateway:
    def embed(self, texts):
        return [[0.2, 0.8] for _ in texts]

    def generate_recommendation(self, *, activity_summary, candidates, trace_id):
        assert "Agentic AI" in activity_summary
        assert trace_id == "trace-agent-test"
        return MeshGeneration(
            recommendation=MeshRecommendation(
                headline="Make agent reliability your next practical advantage",
                narrative=(
                    "Your attention to planning and evaluation is a strong foundation for "
                    "building agent systems that remain useful outside a prototype."
                ),
                interest_summary="Your strongest signal connects agents with dependable delivery.",
                recommendations=[
                    MeshRecommendationItem(
                        product_id=candidates[0]["id"],
                        reason=(
                            "This course connects the planning patterns you explored with "
                            "evaluation and production safeguards."
                        ),
                    )
                ],
            ),
            model="mesh/fake-boundary",
            prompt_tokens=120,
            completion_tokens=60,
            request_id="mesh-fake-request",
        )


class FakeVectorStore:
    def __init__(self, product_id):
        self.product_id = product_id
        self.calls = 0

    def query(self, embedding, limit=8):
        self.calls += 1
        return [
            VectorHit(
                product_id=self.product_id,
                score=0.92,
                document="Agent systems with planning and evaluation",
                metadata={"category": "Agentic AI"},
            )
        ]


def test_langgraph_retrieves_grades_and_generates_grounded_result(db):
    product = Product(
        title="Agentic Systems",
        slug="agentic-systems",
        description="Build dependable agents with planning, tools, and evaluation.",
        category="Agentic AI",
        level="Advanced",
        price=Decimal("129.00"),
        duration_minutes=480,
        tags_json='["agents", "evaluation"]',
    )
    db.add(product)
    db.commit()
    store = FakeVectorStore(product.id)
    profile = {
        "event_count": 6,
        "total_weight": 14.0,
        "query_text": "Learning goals: agent planning and evaluation",
        "summary": "Strongest course categories: Agentic AI. Repeated topic: evaluation.",
        "top_categories": ["Agentic AI"],
        "top_searches": ["agent planning"],
        "top_topics": ["agents", "evaluation"],
        "viewed_product_ids": [],
    }

    state = RecommendationAgent(
        db,
        gateway=FakeGateway(),
        vector_store=store,
        settings=Settings(mesh_api_key="rsk_test"),
    ).run(
        user_id=1,
        trace_id="trace-agent-test",
        trigger="test",
        profile=profile,
    )

    assert state["decision"] == "retrieve"
    assert state["result"]["items"][0]["product_id"] == product.id
    assert [entry["node"] for entry in state["node_trace"]] == [
        "analyze_behavior",
        "semantic_retrieval",
        "grade_retrieval",
        "generate_persuasive_story",
    ]
    assert store.calls == 1
