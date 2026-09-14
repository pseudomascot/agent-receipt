// Append receipt lines for Agent Receipt. Copy this file into your agent.
//
//   const { record } = require("./receipt_line");
//   record("purchase", { target: "AWS", amount: 42.1, currency: "USD", agent: "ops-bot", id: invoiceId });
//
// Writes one JSON line per call to ~/.agent-receipt/inbox/<agent>.jsonl.
// Format: docs/RECEIPT_LINE.md. No dependencies.

const fs = require("fs");
const os = require("os");
const path = require("path");

const INBOX = process.env.AGENT_RECEIPT_INBOX || path.join(os.homedir(), ".agent-receipt", "inbox");
const ACTIONS = new Set(["send_email", "create_event", "purchase", "file_write", "post", "execute", "other"]);

function record(action, fields = {}) {
  if (!ACTIONS.has(action)) throw new Error(`action must be one of ${[...ACTIONS].join(", ")}`);
  const agent = fields.agent || "agent";
  const line = { ts: fields.ts || new Date().toISOString(), agent, action };
  for (const key of ["target", "id", "amount", "currency", "link", "reversible", "reason", "session", "cwd", "project", "detail"]) {
    if (fields[key] !== undefined && fields[key] !== null) line[key] = fields[key];
  }
  fs.mkdirSync(INBOX, { recursive: true });
  const name = agent.replace(/[^A-Za-z0-9_.-]+/g, "_") || "agent";
  fs.appendFileSync(path.join(INBOX, `${name}.jsonl`), JSON.stringify(line) + "\n");
  return line;
}

module.exports = { record };

if (require.main === module) {
  console.log(record("other", { target: "self-test", agent: "receipt-line-example", id: `selftest-${Date.now()}` }));
}
