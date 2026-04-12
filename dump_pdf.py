#!/usr/bin/env python3
"""Dump raw PDF text line-by-line to debug parser issues."""
import fitz

doc = fitz.open("RAFAH INTERNATIONAL - 1168436.pdf")
for pn in range(min(len(doc), 8)):
    text = doc[pn].get_text()
    lines = text.split('\n')
    print(f"\n{'='*60}")
    print(f"PAGE {pn+1} ({len(lines)} lines)")
    print('='*60)
    for i, line in enumerate(lines):
        print(f"  {i:3d}: {repr(line)}")
doc.close()
