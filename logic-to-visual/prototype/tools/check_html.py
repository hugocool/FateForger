import json
import re
import sys

html = open(
    "/Users/hugoevers/VScode-projects/admonish-1/docs/constraint_flow.html"
).read()

idx = html.find("const DETAIL_PANELS = ")
print("DETAIL_PANELS at byte:", idx)

chunk = html[idx + len("const DETAIL_PANELS = ") :]
decoder = json.JSONDecoder()
try:
    obj, end = decoder.raw_decode(chunk)
    print("JSON OK, keys count:", len(obj))
    print("Char after JSON:", repr(chunk[end : end + 5]))
except json.JSONDecodeError as e:
    print("JSON ERROR at pos", e.pos, ":", e.msg)
    ctx = chunk[max(0, e.pos - 80) : e.pos + 80]
    print("Context:", repr(ctx))
    sys.exit(1)

# Check EDGE_TOOLTIPS too
idx2 = html.find("const EDGE_TOOLTIPS = ")
chunk2 = html[idx2 + len("const EDGE_TOOLTIPS = ") :]
try:
    obj2, end2 = decoder.raw_decode(chunk2)
    print("EDGE_TOOLTIPS OK, keys count:", len(obj2))
except json.JSONDecodeError as e:
    print("EDGE_TOOLTIPS JSON ERROR at pos", e.pos, ":", e.msg)
    ctx = chunk2[max(0, e.pos - 80) : e.pos + 80]
    print("Context:", repr(ctx))

# Check STEPS too
idx3 = html.find("const STEPS        = ")
chunk3 = html[idx3 + len("const STEPS        = ") :]
try:
    obj3, end3 = decoder.raw_decode(chunk3)
    print("STEPS OK, count:", len(obj3))
except json.JSONDecodeError as e:
    print("STEPS JSON ERROR at pos", e.pos, ":", e.msg)

# Also validate: look for unescaped `</script>` inside the JSON blobs
if "</script>" in chunk[:end]:
    print("WARNING: </script> found inside DETAIL_PANELS JSON — will break JS parsing!")
else:
    print("No </script> tag contamination in DETAIL_PANELS — clean")

if html.count("</script>") > 1:
    print("Multiple </script> tags found — count:", html.count("</script>"))

# Check for backticks or </script> in panel content
idx2 = html.find("const DETAIL_PANELS = ")
chunk2 = html[idx2 + len("const DETAIL_PANELS = ") :]
decoder2 = json.JSONDecoder()
panels2, _ = decoder2.raw_decode(chunk2)
problems = []
for key, val in panels2.items():
    if "`" in val:
        problems.append(f"BACKTICK in {key!r}")
    if "</script>" in val.lower():
        problems.append(f"</script> TAG in {key!r}")
if problems:
    for p in problems:
        print("PROBLEM:", p)
else:
    print("No backticks or script tags in panel content — clean")

print("\nPanel content lengths:")
for key, val in panels2.items():
    print(f"  {key}: {len(val)} chars")
for key, val in panels2.items():
    print(f"  {key}: {len(val)} chars")
