"""Explicit observation states for a nurse-confirmed MTS research checklist."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, model_validator


def current_checklist(state):
    """Do not reuse assessed findings after other observations/catalog changed.

    The original checklist remains in state history and its exact workflow run.
    A new nursing assessment is needed to assert it against current observations.
    """
    from app.rules.mts_catalog import VERSION, catalog_hash
    checklist = (state or {}).get("mts_checklist")
    if not checklist:
        return None
    if (checklist.get("expected_observations_at") != state.get("latest_triage_updates_at")
            or checklist.get("catalog_version") != VERSION
            or checklist.get("catalog_sha256") != catalog_hash()):
        return None
    return checklist


class MTSObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    state: Literal["not_assessed", "unavailable", "source_recorded", "recorded", "assessed", "not_applicable"]
    value: StrictBool | StrictFloat | None = None
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def observation_state(self):
        if self.state in ("assessed", "source_recorded", "recorded") and self.value is None:
            raise ValueError("An assessed/recorded observation requires its value")
        if self.state in ("not_assessed", "unavailable", "not_applicable") and self.value is not None:
            raise ValueError("Unassessed, unavailable and inapplicable observations cannot carry a value")
        if self.state == "not_applicable" and not self.note.strip():
            raise ValueError("Not applicable requires an explicit clinical reason")
        return self


class MTSChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_version: str
    catalog_sha256: str
    flowchart_id: str = Field(max_length=80)
    flowchart_confirmed: StrictBool
    selection_reason: str = Field(default="", max_length=1000)
    observations: dict[str, MTSObservation] = Field(default_factory=dict, max_length=100)
    notes: str = Field(default="", max_length=2000)
    expected_revision: str | None = Field(default=None, max_length=100)
    expected_observations_at: str | None = Field(default=None, max_length=100)
    reassessed_measurements: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def check_catalog(self):
        from app.rules.mts_catalog import CHARTS, MEASUREMENTS, VERSION, catalog_hash
        if self.catalog_version != VERSION or self.catalog_sha256 != catalog_hash():
            raise ValueError("Checklist catalog is stale; reload before recording observations")
        if self.flowchart_id not in CHARTS:
            raise ValueError("Unsupported flowchart")
        if self.flowchart_confirmed is not True:
            raise ValueError("A nurse must confirm the selected flowchart")
        chart = CHARTS[self.flowchart_id]
        if not set(self.reassessed_measurements) <= set(chart["measurement_fields"]):
            raise ValueError("Repeated measurements must belong to this flowchart")
        findings = {r["id"]: r for r in chart["rules"]}
        for key, observation in self.observations.items():
            if key not in findings and key not in chart["measurement_fields"]:
                raise ValueError("Observation does not belong to this flowchart: " + key)
            if key in findings:
                if findings[key].get("applicability") == "paediatric" and observation.value is True:
                    raise ValueError("Paediatric findings are outside the declared age-16-and-over scope")
                if findings[key]["kind"] == "measurement_predicate" and observation.state not in ("not_assessed", "unavailable"):
                    raise ValueError("This discriminator requires the assessed measurement and context; it cannot be asserted or excluded by checkbox")
                if observation.state in ("source_recorded", "recorded"):
                    raise ValueError("Source vital data cannot assert clinical findings")
                if observation.state == "assessed" and type(observation.value) is not bool:
                    raise ValueError("Clinical findings require explicit present/absent values")
            else:
                definition = MEASUREMENTS[key]
                if definition.get("kind") == "boolean":
                    if observation.state in ("source_recorded", "recorded") or (observation.state == "assessed" and type(observation.value) is not bool):
                        raise ValueError("Oxygen context requires an explicit nursing observation")
                    continue
                if observation.state in ("assessed", "source_recorded", "recorded"):
                    if type(observation.value) not in (float, int):
                        raise ValueError("Measurements require a finite numeric value")
                    if not definition["min"] <= observation.value <= definition["max"]:
                        raise ValueError("Measurement outside the supported input range: " + key)
        return self
