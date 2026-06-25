"""Generate the two Phase 0 test QRs (PRD §13).

Run:  python scripts/make_phase0_qrs.py
Then pay S$0.10 with each QR from your own bank app and check DBS FLYMAX
transaction history — see the printed instructions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clubbot import phase0, qrgen  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "phase0_qrs"

INSTRUCTIONS = """
Phase 0 test (PRD section 13) - what to do now:

1. Open each PNG in {out_dir}
   and pay S$0.10 with your personal bank app:
     variant_A.png - keeps the school's bill number; our code BDMTEST01 rides
                     in the EMVCo "reference label" field
     variant_B.png - our code BDMTEST02 REPLACES the school's bill number
2. Wait for both to clear, then check the DBS FLYMAX transaction history:
     a. Did BOTH payments arrive in the club account?
     b. What text shows for each payment (which code, if any)?
     c. Did variant B still get allocated to the club account?
3. Report the answers back - the outcome is recorded in MEMORY.md and decides
   how member reference codes are placed in real payment QRs (Phase 2).
"""


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, payload in [
        ("variant_A", phase0.variant_a_payload()),
        ("variant_B", phase0.variant_b_payload()),
    ]:
        path = OUT_DIR / f"{name}.png"
        path.write_bytes(qrgen.render_png(payload))
        print(f"wrote {path}")
    print(INSTRUCTIONS.format(out_dir=OUT_DIR))


if __name__ == "__main__":
    main()
