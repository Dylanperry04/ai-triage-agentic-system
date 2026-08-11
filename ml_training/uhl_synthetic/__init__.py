"""UHL synthetic-data training workflow.

Rows are loaded from one CSV/CSV.GZ file, validated against the UHL modelling
contract, and trained using only the columns in ``approved_model_inputs``.
"""
