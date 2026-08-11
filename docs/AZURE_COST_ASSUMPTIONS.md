# Azure Cost and Runtime Assumptions

This project uses configurable cost estimates. The app must not present
unverified pricing as official Azure or OpenAI pricing.

The source configuration is:

`config/cost_assumptions.json`

## Current Hosting Assumptions

| Item | Assumption |
|---|---|
| Hosting service | Azure App Service Basic B1 |
| Region | Sweden Central |
| Instance count | 1 |
| Runtime | Python 3.11 |
| Deployment | GitHub Actions |
| vCPU | 1 |
| Memory | 1.75 GB |

The monthly App Service price should be supplied in the config file before
budgeting or quoting costs externally.

## Token Cost Formula

The dashboard uses this formula:

```text
monthly_llm_cost =
calls_per_month * (
  input_tokens_per_call / 1_000_000 * input_rate_per_1m
  + output_tokens_per_call / 1_000_000 * output_rate_per_1m
)
```

If either token rate is missing, the scenario is labelled
`pricing_pending_confirmation`.

## Frontier Model Assumption Label

The config may use an assumption label such as:

`ChatGPT 5.6 / latest frontier model assumption`

That label is not a pricing claim. It is a placeholder until current model
availability and rates are confirmed from official provider pricing.

## Scenarios

The config separates:

- demo/pilot usage
- low usage
- medium usage
- high usage
- model retraining estimates
- storage estimates for tabular data
- storage estimates for scan/image uploads

## Retraining Cost

Retraining cost is estimated separately from runtime hosting. The estimate
should include:

- number of retraining runs per month
- GPU hours per run
- GPU hour rate
- storage/read/write overhead

Until all rates are provided, retraining cost remains a planning estimate only.

## Safety Note

The cost dashboard is a planning tool. It is not a bill, quotation, or official
pricing document.
