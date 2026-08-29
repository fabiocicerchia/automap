function normalizeAdminRecord(rec) {
  const name = String(rec.name || "").trim().toLowerCase();
  const email = String(rec.email || "").trim().toLowerCase();
  const id = parseInt(rec.id, 10) || 0;
  const active = Boolean(rec.active) && !rec.deleted;
  return { id: id, name: name, email: email, active: active };
}
