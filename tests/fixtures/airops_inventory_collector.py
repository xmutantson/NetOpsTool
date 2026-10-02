# Exact _collect_inventory excerpt (newline-normalized), captured 2026-10-02.
# AirOps modules/services/jobs.py at 93b71bffe7a48de3bea8fe09edc1c1900634bbf2.
# Complete source SHA256: 22124ff2f80a5f035c6fd4f2992fb1c2696de90fb9b13685bc62778f468f9f96
# Only this function is AST-extracted by tests; the application is never imported.
# Set AIROPS_JOBS_SOURCE to a local jobs.py to exercise another producer revision.

def _collect_inventory():
    """
    Current stock-on-hand summary using the same roll-up as UI:
      group by (category_id, sanitized_name, weight_per_unit),
      qty = Σ(in) − Σ(out), keep only qty > 0,
      weight_lbs = qty * weight_per_unit,
      updated_at = MAX(timestamp).
    """
    rows = dict_rows("""
      SELECT c.display_name     AS category,
             e.sanitized_name   AS item,
             e.weight_per_unit  AS unit_weight_lbs,
             SUM(
               CASE
                 WHEN e.direction = 'in'  THEN  e.quantity
                 WHEN e.direction = 'out' THEN -e.quantity
               END
             )                   AS qty,
             MAX(e.timestamp)    AS updated_at
        FROM inventory_entries e
        JOIN inventory_categories c ON c.id = e.category_id
       WHERE e.pending = 0
       GROUP BY e.category_id, e.sanitized_name, e.weight_per_unit
       HAVING qty > 0
       ORDER BY c.display_name, e.sanitized_name, e.weight_per_unit
    """)
    out = []
    for r in rows:
        qty = int(r.get("qty") or 0)
        wpu = float(r.get("unit_weight_lbs") or 0.0)
        total = round(qty * wpu, 1)
        ts = r.get("updated_at")
        try:
            upd = iso8601_ceil_utc(datetime.fromisoformat(ts)) if ts else None
        except Exception:
            upd = iso8601_ceil_utc()
        out.append({
            "category": r.get("category") or "",
            "item": r.get("item") or "",
            "unit_weight_lbs": wpu,
            "qty": qty,
            "weight_lbs": total,
            "updated_at": upd,
        })
    return out
