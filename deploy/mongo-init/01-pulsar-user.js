// Creates PULSAR's least-privilege MongoDB user. Safe to re-run.
//
// Runs automatically on a fresh data volume (mounted into /docker-entrypoint-initdb.d).
// For an existing database, run it once as root:
//   docker compose -f docker-compose.prod.yml exec mongo sh -c \
//     'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" /docker-entrypoint-initdb.d/01-pulsar-user.js'
//
// The app user can read and write events, but can only ADD to the audit trail: a compromised
// app cannot rewrite or delete the record of what was done through it.

const appDb = process.env.MONGO_APP_DB || "micro_soc";
const auditDb = process.env.MONGO_AUDIT_DB || "micro_soc_audit";
const password = process.env.MONGO_APP_PASSWORD;
if (!password) throw new Error("MONGO_APP_PASSWORD is not set");

const admin = db.getSiblingDB("admin");

const auditRole = "pulsarAuditInsertOnly";
const privileges = [{ resource: { db: auditDb, collection: "" }, actions: ["insert"] }];
if (admin.getRole(auditRole)) admin.updateRole(auditRole, { privileges, roles: [] });
else admin.createRole({ role: auditRole, privileges, roles: [] });

const roles = [
  { role: "readWrite", db: appDb },
  { role: auditRole, db: "admin" },
];
if (admin.getUser("pulsar")) admin.updateUser("pulsar", { pwd: password, roles });
else admin.createUser({ user: "pulsar", pwd: password, roles });

// One-time copy of audit records written before the audit trail had its own database.
const legacy = db.getSiblingDB(appDb).pulsar_audit;
const target = db.getSiblingDB(auditDb).pulsar_audit;
if (legacy.countDocuments() > 0 && target.countDocuments() === 0) {
  legacy.aggregate([{ $out: { db: auditDb, coll: "pulsar_audit" } }]);
  print(`Copied ${target.countDocuments()} audit records to ${auditDb}.pulsar_audit.`);
  print(`Drop ${appDb}.pulsar_audit once you have checked the copy: the app user can still delete there.`);
}

print("PULSAR MongoDB user ready.");
