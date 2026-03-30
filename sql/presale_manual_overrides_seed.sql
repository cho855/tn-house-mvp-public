BEGIN;

INSERT INTO presale_manual_overrides (
  presale_project_id,
  source_id,
  district,
  project_name,
  road,
  address_note,
  lon,
  lat,
  note
)
VALUES
(
  3055,
  '六甲區|風光合2-日光寓|自由街/進化街口|(111)南工造字第00720號',
  '六甲區',
  '風光合2-日光寓',
  '自由街/進化街口',
  '請填你確認的交會點或代表點說明',
  120.000000,
  23.000000,
  'manual override'
),
(
  3744,
  '永康區|時代至上|永大二路VS永吉路口|南工造字第03877-01號',
  '永康區',
  '時代至上',
  '永大二路VS永吉路口',
  '請填你確認的交會點或代表點說明',
  120.000000,
  23.000000,
  'manual override'
)
ON CONFLICT (presale_project_id) DO UPDATE
SET source_id = EXCLUDED.source_id,
    district = EXCLUDED.district,
    project_name = EXCLUDED.project_name,
    road = EXCLUDED.road,
    address_note = EXCLUDED.address_note,
    lon = EXCLUDED.lon,
    lat = EXCLUDED.lat,
    note = EXCLUDED.note,
    updated_at = now();

COMMIT;
