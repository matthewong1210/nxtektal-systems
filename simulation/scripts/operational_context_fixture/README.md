# Operational-context synthetic fixture (Pilot Course A, 2026-08-08)

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Synthetic exports for the `nxt_operational_context` adapters. Every value is
invented for the simulated Pilot Course A storyline on operating date
2026-08-08 in `Asia/Shanghai`; timestamps carry an explicit `+08:00` offset and
are stored in UTC. Worker references (`W-001` … `W-006`) are pseudonymous and
stable; no file carries a name, phone number, address, pay, or free text. The
`rejected/` files exist only to demonstrate whole-batch rejection.

| File | Source | Content |
|---|---|---|
| `staffing_2026-08-08.csv` | staffing | planned shifts, one cancellation, clock events, one pending change request, one approved change, one absence |
| `sales_2026-08-08.csv` | sales | captured ball-bucket sales, one void, one partial refund, one unmapped SKU |
| `play_2026-08-08.csv` | play | bookings, starts, finishes, one cancellation, one actual player count, one session with no finish |
| `profiles.json` | all | declared source profiles, including the SKU-to-ball-unit mapping |
| `rejected/staffing_forbidden_header.csv` | staffing | a forbidden header (`employee_name`) rejects the whole batch |
| `rejected/sales_bad_row.csv` | sales | one invalid row rejects the whole batch with its row number |
