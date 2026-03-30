import sys
import unittest
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import address
from routers import nearby_by_address, nearby_txn_by_address  # noqa: E402


class ExecuteConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        road = params[0]
        row = self.rows.get(road)
        return ExecuteResult(row)


class ExecuteResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class CursorConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        road = params[0]
        self.current = self.rows.get(road)

    def fetchone(self):
        return self.current


class AddressAliasApiTests(unittest.TestCase):
    def test_nearby_by_address_road_candidates_supports_street_to_road(self):
        self.assertEqual(
            address.road_candidates("文成五街"),
            ["文成五街", "文成五路"],
        )

    def test_nearby_by_address_center_uses_alias_match(self):
        conn = ExecuteConn(
            {
                "文成五路": {"lon": 120.20499479893336, "lat": 23.01890904436833},
            }
        )
        parsed = {"road": "文成五街", "lane": None, "alley": None, "no": "66"}

        center = nearby_by_address.find_center_by_address_points_base(
            conn,
            parsed,
            "台南市北區文成五街66號",
        )

        self.assertIsNotNone(center)
        center_info, match_level = center
        self.assertEqual(center_info.source, "ADDRESS_POINT")
        self.assertEqual(match_level, "BASE_ROAD_NO_ALIAS")
        self.assertEqual(center_info.match_level, "BASE_ROAD_NO_ALIAS")

    def test_nearby_txn_by_address_road_candidates_supports_street_to_road(self):
        self.assertEqual(
            address.road_candidates("文成五街"),
            ["文成五街", "文成五路"],
        )

    def test_nearby_txn_by_address_center_uses_alias_match(self):
        conn = CursorConn(
            {
                "文成五路": {
                    "lon": 120.20499479893336,
                    "lat": 23.01890904436833,
                    "match_level": "BASE_ROAD_NO",
                }
            }
        )

        center = nearby_txn_by_address._center_from_address_points_base(
            conn,
            road="文成五街",
            lane=None,
            alley=None,
            number_clean="66",
        )

        self.assertIsNotNone(center)
        self.assertEqual(center["match_level"], "BASE_ROAD_NO_ALIAS")
        self.assertEqual(center["lon"], 120.20499479893336)
        self.assertEqual(center["lat"], 23.01890904436833)


if __name__ == "__main__":
    unittest.main()
