---
paths:
  - "src/trmnl_frontend/**/*"
---

# TRMNL dashboard — the 5-minute truth contract

The display is an 800x480 e-ink panel, 1-bit, non-interactive, refreshed every 5 minutes. The data
contract, the Framework v2 class list and the view layouts are in `src/trmnl_frontend/README.md`.

- **Never show live clocks.** Always show time as buckets (`10:45–10:50`) and remaining time as ranges (`40–45m`), with progress as discrete 5-minute dots, and always display the snapshot time.
- Why: with a 5-minute refresh a "10:47" display is wrong for 4 out of 5 minutes, and users assume the device is broken when time "jumps" (incident I17).
- Quantise every time to a 5-minute boundary in the backend. The template stays dumb — no time math in Liquid, just render pre-computed values.
- Forbidden: button affordances, smooth progress bars, precise time claims, colour gradients, animations.
