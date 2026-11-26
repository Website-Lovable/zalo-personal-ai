import pytest

from common.models import AuditLog

pytestmark = pytest.mark.django_db


def test_profile_get_put_patch(client_a):
    assert client_a.get("/api/ai/profile/").status_code == 200
    response = client_a.put(
        "/api/ai/profile/",
        {
            "occupation": "Giám đốc",
            "expertise_areas": ["AI"],
            "topics_to_avoid": ["chính trị"],
        },
        format="json",
    )
    assert response.status_code == 200
    assert (
        client_a.patch("/api/ai/profile/", {"education": "MBA"}, format="json").json()["occupation"]
        == "Giám đốc"
    )
    assert AuditLog.objects.filter(action="ai_profile.updated").count() == 2


def test_style_validation(client_a):
    assert (
        client_a.patch("/api/ai/style/", {"formality_level": 11}, format="json").status_code == 400
    )
    response = client_a.patch(
        "/api/ai/style/",
        {"formality_level": 8, "emoji_usage": "never", "tone_by_relationship": {"family": "warm"}},
        format="json",
    )
    assert response.status_code == 200 and response.json()["emoji_usage"] == "never"
