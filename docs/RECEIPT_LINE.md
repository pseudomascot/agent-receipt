# Receipt lines: how any agent gets onto the statement

Agent Receipt reads the transcripts of agents it knows (Claude Code, Cowork,
Codex). Any other agent — a company's own bot on the OpenAI Agents SDK, a
LangChain app, a cron script — gets on the statement by **declaring its own
side effects**: append one JSON line per action to a file in

```
~/.agent-receipt/inbox/<anything>.jsonl
```

The collector reads new lines within five minutes. Files are append-only;
never rewrite or reorder them (the receipt hashes what it has read and will
report a rewritten file as modified).

## One line

```json
{"ts":"2026-09-14T15:04:05-07:00","agent":"invoice-bot","action":"send_email","target":"billing@example.com","id":"msg_8f3a","reversible":false,"reason":"the message left the server","detail":{"subject":"Invoice 1042"}}
```

| field | required | meaning |
|---|---|---|
| `ts` | yes | when it happened, ISO 8601 with timezone (or epoch seconds/ms) |
| `agent` | yes | who did it, e.g. `invoice-bot`, `acme/quote-agent 2.1` |
| `action` | yes | one of `send_email`, `create_event`, `purchase`, `file_write`, `post`, `execute`, `other` |
| `target` | recommended | to whom or what: an address, a path, a URL, a merchant, a command |
| `id` | recommended | the action's own id (message id, order id, tool-call id). Makes re-sending the same line harmless. If absent, a hash of the line is used. |
| `amount`, `currency` | for `purchase` | number and ISO code, e.g. `12.50`, `USD` |
| `link` | optional | where to see the artifact: a URL or `file://` path |
| `reversible` | optional | `true` / `false` if the agent knows; omit if not |
| `reason` | optional | why it is or isn't reversible |
| `session` | optional | the agent's own session or run id |
| `cwd`, `project` | optional | working directory / project label for grouping |
| `detail` | optional | any object; kept verbatim (strings over 10,000 chars are truncated) |

Only side effects. Reads are not receipt lines.

## What the receipt does with it
- Attribution is `agent` — the line is the agent's own declaration, which is
  the strongest evidence the receipt accepts. Physical-input correlation and
  alert rules apply exactly as for built-in sources.
- The row's note reads "declared by the agent itself via the receipt inbox".
- If `reversible` is omitted, the receipt applies its own rules (a shell
  command in `detail.command` for `execute`, a path for `file_write`); otherwise
  it takes the agent's word and shows the `reason`.

## Emitting lines

Python (`examples/receipt_line.py`):
```python
from receipt_line import record
record("send_email", target="billing@example.com", agent="invoice-bot", id=msg_id,
       reversible=False, reason="the message left the server", detail={"subject": subject})
```

Node (`examples/receipt_line.js`):
```js
const { record } = require("./receipt_line");
record("purchase", { target: "AWS", amount: 42.10, currency: "USD", agent: "ops-bot", id: invoiceId });
```

Shell:
```bash
printf '%s\n' '{"ts":"2026-09-14T15:04:05Z","agent":"nightly.sh","action":"execute","target":"rm -rf build"}' >> ~/.agent-receipt/inbox/nightly.jsonl
```

## Why declare, rather than be watched
An email sent by an agent through an API and one sent by a person look
identical to the receiving service. Only the agent knows it was the agent. A
declaration is not an inference, so it can be shown to an insurer or auditor
as what the agent said it did — and the receipt's integrity check proves the
declaration was not edited afterwards.
