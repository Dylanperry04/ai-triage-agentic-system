from app.analytics.costing import build_cost_estimate, monthly_llm_cost


def test_monthly_llm_cost_formula():
    cost = monthly_llm_cost(
        calls_per_month=1000,
        input_tokens_per_call=2000,
        output_tokens_per_call=500,
        input_rate_per_1m=2.0,
        output_rate_per_1m=10.0,
    )
    assert cost == 1000 * ((2000 / 1_000_000 * 2.0) + (500 / 1_000_000 * 10.0))


def test_missing_pricing_rates_are_safe():
    assert monthly_llm_cost(
        calls_per_month=1000,
        input_tokens_per_call=2000,
        output_tokens_per_call=500,
        input_rate_per_1m=None,
        output_rate_per_1m=10.0,
    ) is None
    estimate = build_cost_estimate({
        "model_name": "ChatGPT 5.6 assumption",
        "input_token_rate_per_1m": None,
        "output_token_rate_per_1m": None,
        "scenarios": {
            "pilot": {
                "calls_per_month": 10,
                "input_tokens_per_call": 100,
                "output_tokens_per_call": 50,
            }
        },
    })
    assert estimate["pricing_pending_confirmation"] is True
    assert estimate["scenarios"][0]["monthly_llm_cost"] is None
    assert estimate["scenarios"][0]["pricing_status"] == "pricing_pending_confirmation"
