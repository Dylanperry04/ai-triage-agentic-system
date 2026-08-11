# Corrected 22.4 UHL data/model swap

## Preserved from 22.4.0

- FastAPI route structure and same-origin React hosting
- agent orchestration and LLM explanation boundary
- deterministic Manchester-style research rules and escalate-only vital override
- authentication, RBAC, redaction, audit, review queue, reassessment, and follow-up flows
- React layouts, interactions, and visualization components

## Switched to UHL

- active case source and case pagination/search
- model feature construction and serving-bundle validation
- selected CatBoost artifact and decision rule
- model-performance reports and visualization payloads
- health/runtime/model provenance displays
- packaged Azure/Docker data and artifact paths

## Recommendation display rule

The deployment uses the modal model class unless a more urgent individual
class reaches 25%. If several more urgent classes qualify, the most urgent is
selected. The recommendation card and accepted/logged value use the same
backend result; the previous decision-rule explanation box is not rendered.

## Deliberately not imported from 23.1.1

The rewritten 23.1.1 backend, schemas, APIs, security/state implementation,
frontend redesign, and replacement test suite were not transplanted. They were
unrelated to the requested data/model migration and would have changed the
working 22.4 system.

The corrected release ZIP also excludes the old `model_outputs/` MIMIC model
bundle, the `data/demo/` MIMIC-shaped cohort, generated SQLite caches, audit
logs, bytecode, test caches, and frontend `node_modules`. Historic MIMIC source
modules and tests remain only as inactive 22.4 compatibility/reference code.

## Pinned identities

- Dataset SHA-256: `f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c`
- Model SHA-256: `7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b`
- Feature-schema SHA-256: `fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd`

Research demonstration only. Not for clinical use.
