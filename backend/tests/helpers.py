ABILITIES = {
    "strength": 10,
    "dexterity": 10,
    "constitution": 10,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 10,
}


def create_hero(client, session_id: str, *, name: str = "Hero") -> dict:
    response = client.post(
        f"/sessions/{session_id}/characters",
        json={
            "expected_revision": 0,
            "name": name,
            "concept": "Adventurer",
            "max_hp": 10,
            "stats": ABILITIES,
            "starting_item": "",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["characters"][0]
