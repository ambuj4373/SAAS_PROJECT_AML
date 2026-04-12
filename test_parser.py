#!/usr/bin/env python3
"""Quick test of parse_cc_printout against the sample PDF."""
import json, re as _re, sys, fitz, traceback

# Read app.py source
with open("app.py", "r") as f:
    src = f.read()

# Extract _parse_cc_amount function
start2 = src.index("def _parse_cc_amount(")
lines2 = src[start2:].split("\n")
end2 = len(lines2)
for i, line in enumerate(lines2[1:], 1):
    if line and not line[0].isspace() and not line.startswith("#"):
        end2 = i
        break
amt_code = "\n".join(lines2[:end2])
exec(amt_code)

# Extract parse_cc_printout function
start = src.index("def parse_cc_printout(")
lines = src[start:].split("\n")
end = len(lines)
for i, line in enumerate(lines[1:], 1):
    if line and not line[0].isspace() and not line.startswith("#"):
        end = i
        break
func_code = "\n".join(lines[:end])
print(f"Function code length: {len(func_code)} chars, {end} lines")
print(f"First 200 chars: {func_code[:200]}")
print(f"Last 200 chars: {func_code[-200:]}")
try:
    exec(func_code)
except Exception as e:
    print(f"ERROR compiling function: {e}")
    traceback.print_exc()
    sys.exit(1)

# Parse the sample PDF
with open("RAFAH INTERNATIONAL - 1168436.pdf", "rb") as f:
    data = parse_cc_printout(f.read())

# Print key fields
for k in [
    "charity_number", "charity_name", "declared_policies",
    "registration_history", "trustees_detailed", "financial_breakdown",
    "organisation_type", "what_the_charity_does", "where_the_charity_operates",
    "charitable_objects", "address", "email", "phone",
]:
    v = data.get(k)
    if isinstance(v, (list, dict)):
        print(f"\n{k}:")
        print(json.dumps(v, indent=2, default=str))
    else:
        print(f"\n{k}: {v}")

print(f"\n--- Total fields: {len(data)} ---")
