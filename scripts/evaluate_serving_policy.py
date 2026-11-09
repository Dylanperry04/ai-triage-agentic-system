"""Replay the frozen live ML + safety policy; never train or alter input data.

This is a post-hoc replay of a previously accessed synthetic test split, not
independent hospital validation. Aggregate output contains no patient rows.
"""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone
import numpy as np


def replay(output: Path):
    from app.config import settings
    from app.agents.ml_prediction_agent import _select_uhl_serving_acuity, _apply_artifact_decision_rule
    from app.rules.decision_policy import system_identity
    from app.rules.mts_catalog import propose_flowchart, CHARTS
    from app.rules.acuity_override import apply_acuity_override
    from app.analytics.mts_evaluation import _category_metrics
    from ml_training.uhl_synthetic.dataset import load_uhl_dataset
    from ml_training.uhl_synthetic.serving import validate_uhl_serving_bundle, resolve_uhl_presenting_complaint
    import joblib
    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / 'artifacts/reports/single_seed/uhl_synthetic_training_provenance.json').read_text())
    data_path, model_path = settings.uhl_data_path, settings.uhl_model_path
    data_hash = hashlib.sha256(data_path.read_bytes()).hexdigest()
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if data_hash != provenance['dataset_sha256'] or data_hash != settings.expected_dataset_sha256:
        raise ValueError('Dataset differs from the frozen training source')
    if model_hash != settings.expected_model_sha256:
        raise ValueError('Model differs from the trusted pinned artifact; not loading pickle')
    data = load_uhl_dataset(data_path, seed=provenance['split_seed'])
    if data.metadata['split_assignment_sha256'] != provenance['split_assignment_sha256']:
        raise ValueError('Split differs from the historical split; refusing a new random evaluation')
    bundle = joblib.load(model_path)
    validate_uhl_serving_bundle(bundle, require_deployable=True)
    model = bundle['model']
    X = data.X.loc[data.split == 'TEST'].copy()
    y = data.y[data.split == 'TEST']
    # The historical loader checks numeric/calendar validity. Confirm that
    # these rows also stay inside the actual fitted serving domain before
    # reporting complete prediction coverage.
    support = bundle['fitted_age_support']
    if not X.age.between(support['minimum'], support['maximum']).all():
        raise ValueError('TEST rows exceed fitted age support; report abstentions before replaying')
    for complaint in X.presenting_complaint.unique():
        resolve_uhl_presenting_complaint(complaint, allowed_complaints=bundle['seen_presenting_complaints'])
    probabilities = model.predict_proba(X)
    labels = [int(v) for v in model.classes_]
    ml = np.array([_select_uhl_serving_acuity(dict(zip(map(str, labels), map(float, row)))) for row in probabilities])
    # Apply the exact scalar serving override, avoiding a second threshold implementation.
    from types import SimpleNamespace
    final = np.array([apply_acuity_override(int(pred), SimpleNamespace(**row, temperature_unit='F'))['final_acuity']
                      for pred, row in zip(ml, X.to_dict('records'))])
    historical = np.array([_apply_artifact_decision_rule(model, labels[int(np.argmax(row))], np.array([row]), bundle.get('decision_rule')) for row in probabilities])
    groups = []
    for complaint in X.presenting_complaint:
        chart = propose_flowchart(complaint)['flowchart_id']
        groups.append(CHARTS[chart]['complaint_group'] if chart else 'unmapped_or_ambiguous')
    pairs = [{'reference_category': int(ref), 'ml_category': int(pred), 'ml_safety_category': int(safe), 'historical_category': int(old), 'group': group}
             for ref, pred, safe, old, group in zip(y, ml, final, historical, groups)]
    result = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Post-hoc replay of previously accessed synthetic TEST rows; estimated labels; not independent clinical validation',
        'model_sha256': model_hash, 'dataset_sha256': data_hash,
        'split_assignment_sha256': data.metadata['split_assignment_sha256'], 'split_seed': provenance['split_seed'],
        'system_identity': system_identity(), 'evaluated_rows': len(pairs),
        'fitted_serving_domain_verified': True,
        'mts_assessment_evaluated': False, 'mts_reason': 'No clinical discriminator reference observations are supplied by these rows',
        'policy_difference_before_safety': int(np.count_nonzero(ml != historical)),
        'vital_safety_escalations': int(np.count_nonzero(final < ml)),
        'prediction_sha256': hashlib.sha256(np.column_stack([ml, final]).astype('<i8').tobytes()).hexdigest(),
        'by_presenting_complaint': {group: {field: _category_metrics(pairs if group == 'all' else [p for p in pairs if p['group'] == group], field)
            for field in ('ml_category', 'ml_safety_category', 'historical_category')}
            for group in ('all', 'chest_pain', 'abdominal_pain', 'trauma', 'respiratory_symptoms', 'unmapped_or_ambiguous')},
        'limitations': ['Estimated acuity labels and synthetic vital signs are not nurse-led reference categories.',
            'The original row split does not establish patient-disjoint or temporal hospital validation.',
            'The selected 100 cases are a demonstration cohort and are not used to estimate these performance rates.',
            'Unsupported and ambiguous complaints remain separate; no inferred clinical findings were generated.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'rows': len(pairs), 'all': result['by_presenting_complaint']['all']}))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts/reports/serving_policy_evaluation.json'))
    replay(parser.parse_args().output)
