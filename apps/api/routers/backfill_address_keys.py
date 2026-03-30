from psycopg import connect
from address import parse
import os

DB_DSN = os.environ["DB_DSN"]

def main():
    with connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, address_norm
                FROM address_points
                WHERE addr_core IS NULL
            """)
            rows = cur.fetchall()

            print("Total rows:", len(rows))

            for _id, addr in rows:
                p = parse(addr)

                cur.execute("""
                    UPDATE address_points
                    SET road = %s,
                        no = %s,
                        subno = %s,
                        addr_core = %s,
                        addr_with_dist = %s
                    WHERE id = %s
                """, (
                    p.road,
                    p.no,
                    p.subno,
                    p.key_core,
                    p.key_with_dist,
                    _id
                ))

        conn.commit()

if __name__ == "__main__":
    main()