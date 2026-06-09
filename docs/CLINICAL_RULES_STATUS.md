# Clinical rules status

This repository does not implement a final Manchester Triage System ruleset.

Reason:

- MIMIC-IV-ED does not provide Manchester Triage System labels.
- The official Manchester discriminator rules are not included in the verified dataset documentation.
- Fabricating clinical rules would be unsafe.

Implemented:

- Data ingestion
- Schema validation
- Case construction
- Leakage separation
- Clinical-rule interface placeholder
- Human review status output

Not implemented yet:

- Clinician-validated Manchester discriminator rules
- Final automated Red/Orange/Yellow/Green/Blue assignment

Current engine status:

```text
NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED
```

Next required input:

A clinician-approved rules file specifying complaint pathways, discriminators, priorities, and escalation behaviour.
