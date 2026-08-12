"""
One-shot script: mark verified false-positive arbs as 'closed' in arbitrage_opportunities.
Run once. Each ID has a documented reason.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()
import psycopg2

FALSE_POSITIVES = [
    ("fb0c289d-ecc3-4713-93c6-8997cc229266", "KXVPRESNOMR-28: VP nominee market — not exhaustive (write-ins / field possible)"),
    ("447bfcf2-e319-472c-b553-7789bd73bf23", "KXFIRSTHURRICANE: partial coverage — our 9 legs vs Kalshi 21 total markets"),
    ("1f57f6c3-a11b-491b-a039-3f8394b50bf5", "KXCLOSESTGOVERNOR: not exhaustive — named states only, write-ins possible"),
    ("07a4cf65-9888-4a66-a66c-ba0115ca5221", "KXCLOSESTGOVERNOR: duplicate row of 1f57f6c3"),
    ("951d6cd6-c52e-4e82-b874-0810d71799d0", "KXGEORGIAPARLI-28: partial coverage — 3 legs vs Kalshi 5 total markets"),
    ("9a177b23-ca12-406e-b95c-ecd3fe058e2e", "KXNASDAQ100Y: T/B numeric price-threshold bins, not a CE arb"),
    ("4cb6b505-1265-4d30-8fcc-52a895bbc3c5", "KXFEDGOVNOM-27: Kalshi mutually_exclusive=False — not CE eligible"),
    ("c54e43fe-13ef-453a-a9bc-555a1997ebac", "KXMONGOLIAPRES-27: duplicate of de718711 (lower net edge copy)"),
]

url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not url:
    print("ERROR: no DB URL found in environment")
    sys.exit(1)

pg = psycopg2.connect(url)
cur = pg.cursor()

for opp_id, reason in FALSE_POSITIVES:
    cur.execute(
        "UPDATE arbitrage_opportunities SET status='closed', notes=%s WHERE opportunity_id=%s",
        (f"false_positive: {reason}", opp_id),
    )
    print(f"  closed {opp_id[:8]}… — {reason[:60]}")

pg.commit()
pg.close()

print(f"\nClosed {len(FALSE_POSITIVES)} false-positive rows.")
