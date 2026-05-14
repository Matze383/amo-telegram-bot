# CP-I1 Self-Improvement (Read-Only Proposal Mode)

## Deutsch

### Übersicht
CP-I1 ermöglicht eine **nur-vorschlagende** Selbstverbesserungsfähigkeit.
Autonome Änderungen sind **nicht erlaubt**.

### Erlaubte Aktionen
- `propose`-Aktion mit strukturiertem Vorschlag (`title`, `rationale`, `steps`, optionale `risk_notes`)
- Ausgabe ist rein textbasiert (Plan/Vorschlag)

### Ausdrücklich Verboten
Folgende Aktionen werden mit Audit-Event-Grund `self_improvement_action_denied` abgelehnt:
- `modify_runtime`
- `modify_prompt`
- `modify_policy`
- `push_code`
- `merge_pr`

### Audit-Verhalten
Jede Anfrage erzeugt:
1. `requested`
2. Policy-Entscheidung (`allowed` oder `denied`)

Reason-Codes sind sicher/lowercase:
- `policy_allow_read_only_proposal`
- `self_improvement_action_denied`
- `proposal_required`
- `actor_type_not_allowed`
- `scope_not_allowed`

### Sicherheitsabsicht
Dieses Slice beschränkt Selbstverbesserung absichtlich auf **nicht-ausführende Textvorschläge**, um selbstmodifizierendes Verhalten zu verhindern.

---

## English

### Overview
CP-I1 enables a **proposal-only** self-improvement capability.
Autonomous modifications are **not permitted**.

### Allowed Actions
- `propose` action with structured proposal payload (`title`, `rationale`, `steps`, optional `risk_notes`)
- Output is text only (plan/proposal)

### Explicitly Denied
The following actions are denied with audit event reason `self_improvement_action_denied`:
- `modify_runtime`
- `modify_prompt`
- `modify_policy`
- `push_code`
- `merge_pr`

### Audit Behavior
Each request emits:
1. `requested`
2. policy decision (`allowed` or `denied`)

Reason codes are safe/lowercase and include:
- `policy_allow_read_only_proposal`
- `self_improvement_action_denied`
- `proposal_required`
- `actor_type_not_allowed`
- `scope_not_allowed`

### Security Intent
This slice intentionally constrains self-improvement to **non-executing text proposals** to prevent self-modifying behavior.
