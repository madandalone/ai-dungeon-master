import httpx
from typing import Dict, Any, Optional

DND_API_BASE_URL = "https://www.dnd5eapi.co/api/2014"


class DnD5eClient:
    """Клиент для взаимодействия с D&D 5e SRD REST API."""

    @staticmethod
    def get_monster(monster_index: str) -> Optional[Dict[str, Any]]:
        """Получить канонические статы монстра (HP, AC, атаки)."""
        url = f"{DND_API_BASE_URL}/monsters/{monster_index.lower()}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json"
        }
        try:
            # Включаем follow_redirects=True, чтобы не спотыкаться на 301
            with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                response = client.get(url)
                if response.status_code == 200:
                    data = response.json()

                    # Извлекаем показатель защиты (Armor Class)
                    ac = 10
                    ac_data = data.get("armor_class")
                    if isinstance(ac_data, list) and len(ac_data) > 0:
                        first = ac_data[0]
                        ac = first.get("value", 10) if isinstance(first, dict) else first
                    elif isinstance(ac_data, int):
                        ac = ac_data

                    return {
                        "name": data.get("name"),
                        "hit_points": data.get("hit_points"),
                        "armor_class": ac,
                        "challenge_rating": data.get("challenge_rating"),
                        "actions": [a.get("name") for a in data.get("actions", [])][:3]
                    }
                else:
                    print(f"[D&D API] Ошибка сервера: HTTP {response.status_code}")
        except Exception as e:
            print(f"[D&D API] Ошибка соединения: {e}")
        return None