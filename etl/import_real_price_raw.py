import os, glob, json, hashlib
import pandas as pd
from psycopg import connect
from psycopg.rows import dict_row

DB_DSN = os.environ.get("DB_DSN")  # 你也可以沿用你現有的連線方式
PRICE_DIR = os.environ.get("PRICE_DIR", "/data/price")  # 你把檔案放進容器的路徑

def md5_row(d: dict) -> str:
    s = json.dumps(d, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def main():
    files = sorted(glob.glob(os.path.join(PRICE_DIR, "*.csv")))
    if not files:
        raise SystemExit(f"No CSV files found under {PRICE_DIR}")

    with connect(DB_DSN) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS real_price_raw (
          id BIGSERIAL PRIMARY KEY,
          source_file TEXT NOT NULL,
          row_no BIGINT,
          payload JSONB NOT NULL,
          row_hash TEXT UNIQUE
        );
        """)
        conn.commit()

        for fp in files:
            # utf-8-sig：很常見於政府 CSV（避免 BOM 造成欄位名怪掉）
            df = pd.read_csv(fp, encoding="utf-8-sig", dtype=str).fillna("")
            rows = df.to_dict(orient="records")

            data = []
            for i, r in enumerate(rows, start=1):
                h = md5_row(r)
                data.append((os.path.basename(fp), i, json.dumps(r, ensure_ascii=False), h))

            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO real_price_raw (source_file, row_no, payload, row_hash)
                    VALUES (%s, %s, %s::jsonb, %s)
                    ON CONFLICT (row_hash) DO NOTHING
                    """,
                    data
                )
            conn.commit()
            print(f"Imported raw: {os.path.basename(fp)} rows={len(rows)}")

if __name__ == "__main__":
    main()
