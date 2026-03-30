import os

import psycopg


def get_conn():
    dsn = os.getenv("DATABASE_URL") or os.getenv("DB_DSN")
    return psycopg.connect(dsn)


def main():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH applied AS (
                  UPDATE presale_projects AS p
                  SET lon = o.lon,
                      lat = o.lat,
                      geom_center = o.geom_center,
                      center_source = o.center_source,
                      match_level = o.match_level,
                      updated_at = now()
                  FROM presale_manual_overrides AS o
                  WHERE o.presale_project_id = p.id
                  RETURNING p.id
                )
                SELECT count(*) FROM applied
                """
            )
            updated = cur.fetchone()[0]
        conn.commit()

    print(f"[DONE] updated={updated}")


if __name__ == "__main__":
    main()
