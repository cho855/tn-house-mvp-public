import re
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

# =========
# 1) 全形數字 → 半形數字
# =========
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")

# =========
# 2) 中文數字段 → 阿拉伯數字段（先處理「段」最常見）
# =========
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
    """
    將「二段」「三段」轉成「2段」「3段」
    """
    for k, v in CHINESE_NUM_MAP.items():
        s = s.replace(f"{k}段", f"{v}段")
    return s


# =========
# 3) Regex
# =========
RE_SPACES = re.compile(r"\s+")
RE_PUNCT = re.compile(r"[，,。．·•・/\\()（）]+")
RE_CITY = re.compile(r"^(台南市|臺南市)")
RE_DIST = re.compile(r"^(.+?[區鎮鄉市])")
RE_TO_HAO = re.compile(r"^(.+?號)")  # 只取到「號」
RE_DASH_SUBNO = re.compile(r"(\d+)[-－](\d+)號")  # 97-2號 → 97號之2

# lane/alley 抓取（數字+巷 / 數字+弄）
RE_LANE = re.compile(r"(?P<lane>\d+)巷")
RE_ALLEY = re.compile(r"(?P<alley>\d+)弄")


# =========
# 4) ParsedAddress
# =========
@dataclass
class ParsedAddress:
    raw: str
    canon: str

    city: Optional[str]
    district: Optional[str]

    road: Optional[str]     # 中華西路2段 / 三民路 / 中山路1段
    lane: Optional[str]     # 550（巷）
    alley: Optional[str]    # 30（弄）

    no: Optional[str]       # 416
    subno: Optional[str]    # 之2（字串 "2"）

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


# =========
# 5) normalize
# =========
def normalize(s: str) -> str:
    """
    normalize 就是把地址洗成比較一致的「canon」格式：
    - 臺 → 台
    - 全形９７ → 97
    - 二段 → 2段
    - 去空白、去標點
    - 97-2號 → 97號之2
    - 最後只取到「號」，避免樓層/室/備註干擾（例如：七樓、之5）
    """
    if not s:
        return ""

    s = s.replace("　", " ").strip()
    s = s.replace("臺", "台")

    # 中文段轉數字（很重要：中華西路二段 → 中華西路2段）
    s = normalize_chinese_section(s)

    # 全形數字轉半形
    s = s.translate(_FULLWIDTH)

    # 去空白、去標點
    s = RE_SPACES.sub("", s)
    s = RE_PUNCT.sub("", s)

    # 97-2號 → 97號之2
    s = RE_DASH_SUBNO.sub(r"\1號之\2", s)

    # 只取到「號」
    m = RE_TO_HAO.search(s)
    if m:
        s = m.group(1)

    return s


# =========
# 6) parse（normalize + parse + build_keys）
# =========
def parse(s: str) -> ParsedAddress:
    raw = s or ""
    canon = normalize(raw)

    city = None
    district = None

    # city
    m_city = RE_CITY.search(canon)
    if m_city:
        city = m_city.group(1)
        rest = canon[len(city):]
    else:
        rest = canon

    # district
    m_dist = RE_DIST.search(rest)
    if m_dist:
        district = m_dist.group(1)
        rest2 = rest[len(district):]
    else:
        rest2 = rest

    # lane / alley（先抓出來，給 matching 用）
    m_lane = RE_LANE.search(rest2)
    lane = m_lane.group("lane") if m_lane else None

    m_alley = RE_ALLEY.search(rest2)
    alley = m_alley.group("alley") if m_alley else None

    # road + no + subno
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


# =========
# 7) build_keys（比對用 key）
# =========
def build_keys(
    city: Optional[str],
    district: Optional[str],
    road: Optional[str],
    no: Optional[str],
    subno: Optional[str],
    canon: str,
) -> Tuple[str, str, str]:
    """
    key 是「比對用代號」，做 3 種：
    - key_core: 三民路97號
    - key_with_dist: 新營區三民路97號
    - key_full: 台南市新營區三民路97號
    """
    if not road or not no:
        key_core = canon
    else:
        key_core = f"{road}{no}號" + (f"之{subno}" if subno else "")

    key_with_dist = f"{district}{key_core}" if district else key_core
    key_full = f"{city}{key_with_dist}" if city else key_with_dist

    return key_full, key_with_dist, key_core


# =========
# 8) 向後相容（舊程式還在 import normalize_address）
# =========
def normalize_address(s: str) -> str:
    return normalize(s)