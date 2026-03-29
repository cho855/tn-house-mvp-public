import re
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")

CHINESE_NUM_MAP = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def normalize_chinese_section(s: str) -> str:
    for k, v in CHINESE_NUM_MAP.items():
        s = s.replace(f"{k}段", f"{v}段")
    return s


RE_SPACES = re.compile(r"\s+")
RE_PUNCT = re.compile(r"[，,。．·•・/\\()（）]+")
RE_CITY = re.compile(r"^(台南市|臺南市)")
RE_DIST = re.compile(r"^(.+?[區鎮鄉市])")
RE_TO_HAO = re.compile(r"^(.+?號)")
RE_DASH_SUBNO = re.compile(r"(\d+)[-－](\d+)號")
RE_LANE = re.compile(r"(?P<lane>\d+)巷")
RE_ALLEY = re.compile(r"(?P<alley>\d+)弄")


@dataclass
class ParsedAddress:
    raw: str
    canon: str
    city: Optional[str]
    district: Optional[str]
    road: Optional[str]
    lane: Optional[str]
    alley: Optional[str]
    no: Optional[str]
    subno: Optional[str]
    key_full: str
    key_with_dist: str
    key_core: str

    def as_dict(self) -> Dict:
        return {
            "raw": self.raw,
            "canon": self.canon,
            "city": self.city,
            "district": self.district,
            "road": self.road,
            "lane": self.lane,
            "alley": self.alley,
            "no": self.no,
            "subno": self.subno,
            "key_full": self.key_full,
            "key_with_dist": self.key_with_dist,
            "key_core": self.key_core,
        }


def normalize(s: str) -> str:
    if not s:
        return ""

    s = s.replace("　", " ").strip()
    s = s.replace("臺", "台")
    s = normalize_chinese_section(s)
    s = s.translate(_FULLWIDTH)
    s = RE_SPACES.sub("", s)
    s = RE_PUNCT.sub("", s)
    s = RE_DASH_SUBNO.sub(r"\1號之\2", s)

    m = RE_TO_HAO.search(s)
    if m:
        s = m.group(1)

    return s


def parse(s: str) -> ParsedAddress:
    raw = s or ""
    canon = normalize(raw)

    city = None
    district = None

    m_city = RE_CITY.search(canon)
    if m_city:
        city = m_city.group(1)
        rest = canon[len(city):]
    else:
        rest = canon

    m_dist = RE_DIST.search(rest)
    if m_dist:
        district = m_dist.group(1)
        rest2 = rest[len(district):]
    else:
        rest2 = rest

    m_lane = RE_LANE.search(rest2)
    lane = m_lane.group("lane") if m_lane else None

    m_alley = RE_ALLEY.search(rest2)
    alley = m_alley.group("alley") if m_alley else None

    road = None
    no = None
    subno = None

    m_main = re.search(
        r"^(?P<road>.+?)(?P<no>\d+)號(?:之(?P<subno>\d+))?$",
        rest2
    )
    if m_main:
        road = m_main.group("road")
        no = m_main.group("no")
        subno = m_main.group("subno")

    key_full, key_with_dist, key_core = build_keys(
        city=city,
        district=district,
        road=road,
        no=no,
        subno=subno,
        canon=canon,
    )

    return ParsedAddress(
        raw=raw,
        canon=canon,
        city=city,
        district=district,
        road=road,
        lane=lane,
        alley=alley,
        no=no,
        subno=subno,
        key_full=key_full,
        key_with_dist=key_with_dist,
        key_core=key_core,
    )


def build_keys(
    city: Optional[str],
    district: Optional[str],
    road: Optional[str],
    no: Optional[str],
    subno: Optional[str],
    canon: str,
) -> Tuple[str, str, str]:
    if not road or not no:
        key_core = canon
    else:
        key_core = f"{road}{no}號" + (f"之{subno}" if subno else "")

    key_with_dist = f"{district}{key_core}" if district else key_core
    key_full = f"{city}{key_with_dist}" if city else key_with_dist

    return key_full, key_with_dist, key_core


def road_candidates(road: Optional[str]) -> list[str]:
    if not road:
        return []

    road = road.strip()
    if not road:
        return []

    candidates = [road]
    if road.endswith("街"):
        candidates.append(f"{road[:-1]}路")

    return list(dict.fromkeys(candidates))


def normalize_address(s: str) -> str:
    return normalize(s)