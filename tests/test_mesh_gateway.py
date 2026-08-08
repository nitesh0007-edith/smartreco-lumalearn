import json
from types import SimpleNamespace

from app.config import Settings
from app.services.mesh_gateway import MeshGateway


class FakeEmbeddings:
    def create(self, **kwargs):
        assert kwargs["model"] == "openai/text-embedding-3-small"
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index), 0.5])
                for index, _ in enumerate(kwargs["input"])
            ]
        )


class FakeCompletions:
    def create(self, **kwargs):
        candidate = json.loads(kwargs["messages"][1]["content"].split("\n")[4])[0]
        content = json.dumps(
            {
                "headline": "Turn your agent interest into a production skill",
                "narrative": (
                    "Your repeated attention to agent workflows points to a practical next "
                    "step that connects planning with dependable delivery."
                ),
                "interest_summary": (
                    "You are consistently exploring agent planning and production reliability."
                ),
                "recommendations": [
                    {
                        "product_id": candidate["product_id"],
                        "reason": (
                            "It develops the planning and evaluation skills reflected in your "
                            "recent exploration."
                        ),
                    }
                ],
            }
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            _request_id="mesh-test-request",
        )


class FakeClient:
    def __init__(self):
        self.embeddings = FakeEmbeddings()
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_mesh_gateway_embeddings_and_strict_generation():
    settings = Settings(mesh_api_key="rsk_test")
    gateway = MeshGateway(settings, client=FakeClient())
    vectors = gateway.embed(["first", "second"])
    generation = gateway.generate_recommendation(
        activity_summary="Strong interests in agent planning and evaluation over six events.",
        candidates=[
            {
                "id": "product-1",
                "title": "Agent Systems",
                "category": "Agentic AI",
                "level": "Advanced",
                "price": "129.00",
                "description": "Build dependable systems.",
                "score": 0.91,
            }
        ],
        trace_id="trace-1",
    )

    assert vectors == [[0.0, 0.5], [1.0, 0.5]]
    assert generation.recommendation.recommendations[0].product_id == "product-1"
    assert generation.prompt_tokens == 100
