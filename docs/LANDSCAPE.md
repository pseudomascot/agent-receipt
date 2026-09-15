# Landscape: what exists next to Agent Receipt (checked 2026-09-15)

A web search done before deciding whether to keep building. Sources are
linked; judgements are ours. Re-check before any launch — this space moves
monthly.

## Nearest neighbours

| | What it is | Where it runs | Sees | Human vs agent? | Verdict |
|---|---|---|---|---|---|
| **[PortEden](https://porteden.com/blog/ai-audit-trail/)** | A cloud "data firewall" between AI tools (Claude, ChatGPT, Copilot, Gemini) and your email/calendar/drive/tasks; logs which records each request touched, one timeline across providers. Free / $8 / $49 / enterprise ([pricing](https://porteden.com/pricing/)). | Their cloud (on-prem only at enterprise tier) | Only traffic routed *through* them — connected-app reads and writes | No | **Closest in purpose and audience.** Different architecture: a proxy you must route through, blind to what an agent does on your own machine (files, shell, browser, Cowork). We read the agents' own transcripts locally. Complementary more than competing; also proof the small-firm buyer exists. |
| **[Nylas CLI audit](https://cli.nylas.com/guides/audit-ai-agent-activity)** | Local audit log of Nylas CLI commands run by Claude Code / Copilot / MCP agents; records invoker + agent source; CSV/JSON export. | Local | Only Nylas CLI email/calendar commands | Invoker username, not presence | Narrow. Same instinct (local, per-agent), one vendor's surface. |
| **[claudit-sec](https://github.com/HarmonicSecurity/claudit-sec)** (Harmonic Security) | Read-only inventory of Claude Desktop config: MCP servers, plugins, scheduled tasks, permissions. 295★. | Local | Configuration, not activity | No | Not a receipt. Pairs well: "what is installed" next to our "what happened". |
| **[Pipelock / Agent Action Receipts](https://pipelab.org/learn/agent-action-receipts/)** (PipeLab, CNCF landscape) | Open-source agent firewall; a mediator outside the agent signs Ed25519 receipts (action, target, principal, delegation chain, policy verdict) into a hash-chained evidence file. | Wherever the agent runs, as a proxy | Only traffic through the mediator | No | Developer infrastructure, not a product for a business owner. Strong idea: receipts signed by something the agent can't forge. Our inbox format could accept its evidence file. |
| **[Notarized Agents / Sello](https://arxiv.org/abs/2606.04193)** (paper) | Receiver-attested receipts: the *service receiving* an agent's call signs the receipt, because "the entity producing the log is the entity being logged". | Proposal + reference implementation | — | No | Research. The same objection we build on (independence from the audited party), taken to the protocol layer. |
| **[Agent Audit Trail, IETF draft](https://datatracker.ietf.org/doc/draft-sharif-agent-audit-trail/)** (Raza Sharif, CyberSecAI; draft-04, Sept 2026, no formal standing) | A JSON record format: agent_id, session_id, action_type, outcome, trust_level, prev_hash SHA-256 chain, human_override. | Format only | — | `human_override` object | Worth aligning our receipt-line and CSV export to, cheaply, so an auditor sees a familiar shape. |
| Enterprise "AI audit trail" lists ([Maxim](https://www.getmaxim.ai/articles/top-5-ai-audit-trail-tools-to-track-agent-activity-in-2026/), [miniOrange](https://www.miniorange.com/blog/ai-agent-audit-trail/)) | Gateways (Bifrost), OpenTelemetry tracing, CloudTrail, SIEMs, LLM observability (LangSmith etc.). | Cloud / VPC | Model calls, tool spans, infra events | Rarely | Developer and security-team tooling. None is a statement a bookkeeper reads. |
| Non-human identity vendors (Astrix, Oasis, Entro; [MintMCP](https://www.mintmcp.com/blog/non-human-identity-management-ai-agents)) | Inventory and revoke service accounts / agent credentials; "one identity per agent, kill switch" is now standard advice ([TechTarget](https://www.techtarget.com/ai/tip/Why-businesses-need-an-AI-agent-kill-switch)). | Enterprise SaaS | Credentials, not actions | No | Same principle as our Agents/Stop/Ramp pages, sold to CISOs at enterprise prices. |
| Agent payment rails (Stripe Issuing + agent toolkit, Ramp agent cards, Visa Intelligent Commerce, Mastercard Agent Pay, Payman, Skyfire) | Give an agent money with limits. | Issuer | Their own rail | No | Rails, not the statement across rails. We sit on top (Ramp connector first). |

Our own repository is the top result for "AI agent bank statement" — the framing is unclaimed.

## What nobody found does
1. **Cross-vendor, side-effect-level, on the user's own machine** — Claude Code, Cowork and Codex transcripts on one statement, plus any agent via the inbox, without routing traffic through anyone's cloud. (Cowork is explicitly *excluded* from Anthropic's own audit logs — [MintMCP](https://www.mintmcp.com/blog/claude-cowork-audit-logging-gap) — so the gap is real.)
2. **"Was a person there?"** answered with evidence (keyboard timing, credential ownership) rather than a role field.
3. **A statement for a non-programmer**: plain names and sentences, coverage stated on every page, checksummed CSV for a bookkeeper, insurer or client.
4. **Identity with a lifecycle**: name → retire → stop → *issue* (a card and budget per agent or sub-agent, attribution by construction).

## Demand signal
Insurers are ending "silent AI" cover at 2026 renewals: ISO endorsements CG 40 47 / CG 40 48 (Jan 2026) let carriers exclude generative-AI claims from general liability; some carriers add "shadow AI" (untracked AI use) exclusions; carriers now ask *how AI is governed and what evidence the buyer can produce on demand* ([Fenwick](https://www.fenwick.com/insights/publications/end-silent-ai-emerging-ai-exclusions-coverage-fragmentation-and-practical-implications), [Business Insurance USA](https://www.businessinsuranceusa.com/blog/insurance/ai-coverage-gap-small-business-insurance-2026/), [AI Policy Desk](https://www.aipolicydesk.com/blog/ai-insurance-exclusion-checklist-2026)). The EU AI Act's logging obligations for high-risk uses become fully operational August 2026. "Can I send this to my insurer?" is no longer a hypothetical question.

## What this changes in the plan
- Keep building. The object is unclaimed; the demand signal is external and recent.
- **Cheap alignment:** export in the IETF draft's record shape (and accept Pipelock evidence files through the inbox) so the receipt speaks the emerging dialect without depending on it.
- **Position against PortEden explicitly:** they are a firewall you route through; we are a statement of what already happened on your machine and your accounts. A business could use both.
- **Watch:** PortEden adding local agent transcripts; Anthropic adding Cowork to its audit logs; Ramp/Stripe shipping their own per-agent statements.
