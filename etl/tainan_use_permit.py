import os
import re
import hashlib
import zipfile
from io import BytesIO
from typing import List, Optional

import requests
import pandas as pd
from psycopg import connect


RE_RESOURCE_UUID = re.compile(r"/Resource/([0-9a-fA-F-]{36})")


def extract_all_resource_download_urls(dataset_url: str) -> List[str]:
    print(f"[ETL] 解析 DataSet 頁面取得所有 Resource UUID: {dataset_url}")

    r = requests.get(dataset_url, timeout=60)
    r.raise_for_status()

    uuids = RE_RESOURCE_UUID.findall(r.text)
    uuids = list(dict.fromkeys(uuids))

    download_urls = [f"https://data.tainan.gov.tw/File/DirectDownload/{rid}" for rid in uuids]
    print(f"[ETL] 找到 {len(download_urls)} 個資源")
    return download_urls


def resolve_real_download_url_from_html(html: bytes) -> str:
    m = RE_RESOURCE_UUID.search(html.decode("utf-8", errors="ignore"))
    if not m:
        raise SystemExit("❌ 下載到 HTML，但抓不到 /Resource/<uuid>，代表頁面結構可能改了或被擋。")

    rid = m.group(1)
    real = f"https://data.tainan.gov.tw/File/DirectDownload/{rid}"
    print(f"[ETL] ✅ 從 HTML 解析到 Resource UUID={rid}")
    print(f"[ETL] ✅ 改用真正下載連結: {real}")
    return real


RE_SPACES = re.compile(r"\s+")
RE_PUNCT = re.compile(r"[，,。．·•・/\\()（）]+")


def normalize_address(addr: str) -> str:
    if not addr:
        return ""
    s = str(addr).strip()
    s = RE_PUNCT.sub("", s)
    s = RE_SPACES.sub("", s)
    s = s.replace("臺南市", "台南市")
    s = s.replace("臺", "台")
    return s


def make_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "tn_house")
    user = os.getenv("POSTGRES_USER", "tn")
    pwd = os.getenv("POSTGRES_PASSWORD", "tnpass")
    return f"host={host} port={port} dbname={db} user={user} password={pwd}"


def make_row_hash(permit_no: str, address_norm: str, issue_date: str) -> str:
    raw = f"{permit_no}|{address_norm}|{issue_date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_dataframe_from_response(content: bytes, url: str, content_type: str) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(BytesIO(content)) as z:
            names = z.namelist()
            print("[ETL] ✅ 下載內容可當 ZIP 開啟")
            print("[ETL] ZIP 內檔案：", names)

            csv_files = [n for n in names if n.lower().endswith(".csv")]
            if not csv_files:
                raise RuntimeError("❌ ZIP 裡找不到 CSV 檔案")

            csv_name = csv_files[0]
            print(f"[ETL] 使用 CSV：{csv_name}")

            with z.open(csv_name) as f:
                return pd.read_csv(f, encoding="big5", encoding_errors="ignore")

    except zipfile.BadZipFile:
        pass

    ct = (content_type or "").lower()
    if "json" in ct or url.lower().endswith(".json"):
        print("[ETL] 下載內容判斷為 JSON")
        return pd.read_json(content)

    print("[ETL] 下載內容判斷為 CSV（非 ZIP）")
    return pd.read_csv(BytesIO(content), encoding="utf-8", encoding_errors="ignore")


def _to_py(v):
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    return v


def insert_rows_returning(rows: List[tuple]) -> int:
    if not rows:
        return 0

    col_arrays = list(zip(*rows))

    permit_no_arr = [_to_py(x) for x in col_arrays[0]]
    building_permit_no_arr = [_to_py(x) for x in col_arrays[1]]
    address_raw_arr = [_to_py(x) for x in col_arrays[2]]
    address_norm_arr = [_to_py(x) for x in col_arrays[3]]
    issue_date_arr = [_to_py(x) for x in col_arrays[4]]
    start_date_arr = [_to_py(x) for x in col_arrays[5]]
    floors_above_arr = [_to_py(x) for x in col_arrays[6]]
    floors_below_arr = [_to_py(x) for x in col_arrays[7]]
    height_m_arr = [_to_py(x) for x in col_arrays[8]]
    usage_arr = [_to_py(x) for x in col_arrays[9]]
    units_arr = [_to_py(x) for x in col_arrays[10]]
    hash_arr = [_to_py(x) for x in col_arrays[11]]

    insert_sql_returning = """
      INSERT INTO use_permits
        (permit_no, building_permit_no, address_raw, address_norm, issue_date, start_date,
         floors_above, floors_below, height_m, usage, units, source_row_hash)
      SELECT
        *
      FROM UNNEST(
        %s::text[],
        %s::text[],
        %s::text[],
        %s::text[],
        %s::date[],
        %s::date[],
        %s::int[],
        %s::int[],
        %s::double precision[],
        %s::text[],
        %s::int[],
        %s::text[]
      )
      ON CONFLICT (source_row_hash) DO UPDATE
      SET building_permit_no = COALESCE(use_permits.building_permit_no, EXCLUDED.building_permit_no)
      RETURNING 1
    """

    with connect(make_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                insert_sql_returning,
                (
                    permit_no_arr,
                    building_permit_no_arr,
                    address_raw_arr,
                    address_norm_arr,
                    issue_date_arr,
                    start_date_arr,
                    floors_above_arr,
                    floors_below_arr,
                    height_m_arr,
                    usage_arr,
                    units_arr,
                    hash_arr,
                ),
            )
            inserted_count = len(cur.fetchall())
        conn.commit()

    return inserted_count


def run_one_source(url: str) -> int:
    url = (url or "").strip()
    if not url:
        raise ValueError("url is empty")

    print(f"[ETL] 下載資料中: {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    content_type = r.headers.get("Content-Type") or ""
    print(f"[ETL] Content-Type = {content_type}")

    if "text/html" in content_type.lower() or r.content.lstrip().startswith(b"<!DOCTYPE html"):
        real = resolve_real_download_url_from_html(r.content)
        r = requests.get(real, timeout=120)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type") or ""
        print(f"[ETL] Content-Type = {content_type} (after resolve)")

    df = load_dataframe_from_response(
        r.content,
        url=str(r.url),
        content_type=content_type,
    )

    print(f"[ETL] 讀到 {len(df)} 筆")

    df.columns = [str(c).strip() for c in df.columns]

    def pick(*names):
        for n in names:
            if n in df.columns:
                return n
        return None

    col_addr = pick("建築地點", "建物地址", "地址", "建築地址")
    col_no = pick("使用執照號碼", "執照號碼", "使用執照字號", "執照字號")
    col_issue = pick("發照日期", "核發日期", "執照發照日期")
    col_start = pick("開工日期", "開工日")
    col_above = pick("地上層數", "地上樓層數", "地上樓層數(層)")
    col_below = pick("地下層數", "地下樓層數", "地下樓層數(層)")
    col_h = pick("建築物高度", "高度", "高度(公尺)")
    col_usage = pick("用途", "建築用途")
    col_units = pick("戶數", "總戶數")

    col_building_permit_no = pick("\u539f\u9818\u57f7\u7167\u5b57\u865f", "\u539f\u9818\u4f7f\u7528\u57f7\u7167\u5b57\u865f")

    if not col_addr:
        raise SystemExit(f"❌ 找不到地址欄位，資料欄位有：{list(df.columns)}")

    df["address_raw"] = df[col_addr].astype(str)
    df["address_norm"] = df["address_raw"].map(normalize_address)
    df["permit_no"] = df[col_no].astype(str) if col_no else ""
    df["building_permit_no"] = df[col_building_permit_no].astype(str) if col_building_permit_no else pd.NA
    df["issue_date"] = pd.to_datetime(df[col_issue], errors="coerce").dt.date if col_issue else pd.NaT
    df["start_date"] = pd.to_datetime(df[col_start], errors="coerce").dt.date if col_start else pd.NaT

    def to_int(series):
        return pd.to_numeric(series, errors="coerce").astype("Int64")

    df["floors_above"] = to_int(df[col_above]) if col_above else pd.Series([pd.NA] * len(df), dtype="Int64")
    df["floors_below"] = to_int(df[col_below]) if col_below else pd.Series([pd.NA] * len(df), dtype="Int64")
    df["height_m"] = pd.to_numeric(df[col_h], errors="coerce") if col_h else pd.NA
    df["usage"] = df[col_usage].astype(str) if col_usage else pd.NA
    df["units"] = to_int(df[col_units]) if col_units else pd.Series([pd.NA] * len(df), dtype="Int64")

    df["source_row_hash"] = [
        make_row_hash(
            str(df.at[i, "permit_no"]) if pd.notna(df.at[i, "permit_no"]) else "",
            df.at[i, "address_norm"],
            str(df.at[i, "issue_date"]) if pd.notna(df.at[i, "issue_date"]) else "",
        )
        for i in range(len(df))
    ]

    rows: List[tuple] = []
    for _, row in df.iterrows():
        rows.append(
            (
                None if pd.isna(row.get("permit_no")) else str(row.get("permit_no")),
                None if pd.isna(row.get("building_permit_no")) else str(row.get("building_permit_no")),
                str(row["address_raw"]),
                str(row["address_norm"]),
                None if pd.isna(row.get("issue_date")) else row.get("issue_date"),
                None if pd.isna(row.get("start_date")) else row.get("start_date"),
                None if pd.isna(row.get("floors_above")) else int(row.get("floors_above")),
                None if pd.isna(row.get("floors_below")) else int(row.get("floors_below")),
                None if pd.isna(row.get("height_m")) else float(row.get("height_m")),
                None if pd.isna(row.get("usage")) else str(row.get("usage")),
                None if pd.isna(row.get("units")) else int(row.get("units")),
                str(row["source_row_hash"]),
            )
        )

    print(f"[ETL] 準備寫入 {len(rows)} 筆（重複的會自動跳過）")

    inserted_count = insert_rows_returning(rows)

    with connect(make_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM use_permits")
            total = cur.fetchone()[0]

    print(f"[ETL] ✅ 本次實際新增 = {inserted_count} 筆")
    print(f"[ETL] ✅ 完成！use_permits 目前總筆數 = {total}")

    return inserted_count


def run() -> None:
    url = os.getenv("TAINAN_USE_PERMIT_URL", "").strip()
    if not url:
        raise SystemExit("❌ TAINAN_USE_PERMIT_URL 沒填，請去 .env 填下載連結")

    if "DataSet/Detail" in url:
        sources = extract_all_resource_download_urls(url)
    else:
        sources = [url]

    total_inserted = 0

    for i, src in enumerate(sources, start=1):
        print(f"\n===== [{i}/{len(sources)}] ETL source =====")
        print(src)
        inserted = run_one_source(src)
        total_inserted += inserted

    print(f"\n===== ALL DONE. Total inserted rows: {total_inserted} =====")


if __name__ == "__main__":
    run()