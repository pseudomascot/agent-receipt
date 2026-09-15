# Stop: taking away what an agent acts with

Retiring an agent (`docs/AGENTS.md`) records a decision. **Stop**
(`http://127.0.0.1:8765/stop`) is how you actually cut an agent off — and it
is honest about which buttons really do that.

## Three kinds of button

| Kind | What happens when you press it | Examples |
|---|---|---|
| **the app makes the call** | A real API call, right now. | Freeze or cancel the agent's Stripe Issuing cards; revoke the receipt's own Google token. |
| **the app ends the processes** | `pkill` on this Mac. Stops it *now*, not forever. | End running Codex processes; end running Claude Code sessions. |
| **open + steps** | The issuer has no API. The button opens the right page and lists the steps; **Mark as done** records that you went and did it. | Sign a Gmail account out everywhere and revoke its app passwords; roll a Stripe key; pause Cowork's scheduled tasks; remove a macOS permission. |

Every control also carries a pill saying what it *achieves*:

- **stops the agent** — the agent can no longer act through that credential.
- **blinds the receipt** — the receipt stops seeing something; the agent is
  untouched. (Revoking the receipt's own Google token; removing its Calendar
  permission.) These exist so you are never misled into thinking a revoke
  stopped an agent when it only stopped the watching.
- **stops it for now** — running processes end; nothing prevents a restart.

## Freeze all
Presses every button the app can press itself and skips anything permanent
(cancelling cards). Link-type controls are still listed underneath so nothing
is forgotten. It will also end any Claude Code session you are talking to.

## Everything is recorded
Each press — success or failure — becomes a line on today's receipt as a
*person's* action ("Freeze the agent's Issuing cards: froze 2 cards (•••• 4242,
•••• 1111)"), with the reversibility stated. The Stop page keeps a "Presses"
list. Nothing on this page deletes history.

## What is not possible from an app, and why
- **A personal Gmail account cannot be suspended or signed out by API.** Only
  a Google Workspace admin can do that in one click (Admin console → Users →
  Suspend). This is the strongest argument for giving business agents
  Workspace accounts rather than gmail.com ones.
- **Stripe secret keys cannot be rolled by API.** Dashboard only.
- **Cowork's scheduled tasks** live inside the Claude app; there is no
  outside switch.
- **macOS permissions** can only be changed in System Settings.

The way to make *every* agent stoppable in one press is to issue its
credentials from something with an API in the first place — an Issuing card
per agent, a Workspace user per agent. That is the planned step 4
(provisioning) and needs Stripe Issuing activated and a Workspace domain.

## On this Mac today
The Stripe sandbox has no Issuing cards (Issuing is not activated) and the
agent mailbox is a personal Gmail, so the "instant" buttons here are built and
unit-tested but not yet proven against a live card or a live suspension.
