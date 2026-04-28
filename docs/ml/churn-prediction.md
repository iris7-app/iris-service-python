# Customer Churn prediction — Python inference (Phase C)

> **Status** : Phases A (training) ✅ + B (Java inference) ✅ +
> C (Python inference) ✅ shipped. Phases D (UI), E (drift SLO),
> F (ConfigMap promotion) in progress.

The `iris-service-python` backend exposes the same Customer
Churn prediction as the Java sibling — same feature contract, same
ONNX model file, same wire shape. The "interchangeable backends"
contract from common ADR-0008 extends to ML predictions :
swapping the Python service for the Java one (or vice versa) is
indistinguishable from the UI's perspective.

## Architecture (Phase C summary)

```
┌─ HTTP / MCP request : customer_id ─────────────────────────────────┐
│                                                                     │
│  POST /customers/{id}/churn-prediction        predict_customer_churn│
│  (iris_service.ml.router)                  (iris_service.mcp.tools) │
│         │                                       │                   │
│         │  loads :                              │                   │
│         │   • Customer (SQLAlchemy)             │                   │
│         │   • Orders (select where customer_id) │                   │
│         │   • OrderLines (select where order_id IN …)               │
│         ▼                                       ▼                   │
│         iris_service.ml.inference.extract_features               │
│         (8-feature numpy.float32 vector — see ADR-0061)             │
│                       │                                             │
│                       ▼                                             │
│         iris_service.ml.inference.ChurnPredictor                 │
│         (ONNX Runtime in-process — same .onnx file as Java)         │
│                       │                                             │
│                       ▼ raw logit                                   │
│         sigmoid(logit) → probability ∈ [0, 1]                       │
│                       │                                             │
│                       ▼                                             │
│         iris_service.ml.dtos.ChurnPrediction                     │
│  (customer_id, probability, risk_band, top_features, model_version, │
│   predicted_at)                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## REST endpoint

```
POST /customers/{id}/churn-prediction
Authorization: Bearer <JWT>     OR     X-API-Key: <key>
```

**Responses** :

- `200` + `ChurnPrediction` JSON
- `404` if the customer doesn't exist
- `503` if the ONNX model isn't loaded yet (file missing under the
  configured path — see [Model provisioning](#model-provisioning))
- `422` if the path id is `≤ 0`

**Example** :

```bash
curl -s -X POST -H "X-API-Key: demo-api-key-2026" \
    http://localhost:8001/customers/42/churn-prediction | jq
```

```json
{
  "customer_id": 42,
  "probability": 0.731,
  "risk_band": "HIGH",
  "top_features": ["days_since_last_order", "total_revenue_90d", "order_frequency"],
  "model_version": "v3-2026-04-27",
  "predicted_at": "2026-04-27T15:42:18.392+00:00"
}
```

## MCP tool

```
predict_customer_churn(customer_id: int) →
    ChurnPrediction | ChurnNotFound | ChurnServiceUnavailable
```

**Same logic, MCP-compatible soft-error DTOs** instead of HTTP
status codes — the LLM caller receives a parseable JSON shape that
lets it reason about retry / fallback rather than catching an
exception.

```bash
# Wire it up in claude.
claude mcp add --transport http mirador-python http://localhost:8001/mcp \
    --header "X-API-Key: demo-api-key-2026"

# Ask in natural language :
claude
> Predict the churn risk for customer 42. Show me the probability and the band.
```

## Model provisioning

Per [shared ADR-0062](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0062-mlflow-registry-configmap-promotion.md),
the ONNX artefact is distributed via a Kubernetes ConfigMap. Pods
mount it read-only at `/etc/models/churn_predictor.onnx`.

**Configuration env vars** (mirror Java's `application.yml`) :

- `IRIS_CHURN_MODEL_PATH` (default `/etc/models/churn_predictor.onnx`)
- `IRIS_CHURN_MODEL_VERSION` (default `unspecified`) — surfaced
  in every `ChurnPrediction` response for audit + drift correlation

**Local dev** : the training pipeline writes to
`./.models/churn_predictor.onnx` by default. Override via :

```bash
export IRIS_CHURN_MODEL_PATH="$(pwd)/.models/churn_predictor.onnx"
uv run iris-service
```

**Production promotion** is `bin/ml/promote_to_configmap.sh`
(Phase F) — pulls the latest `Production`-tagged ONNX from
MLflow, generates the ConfigMap YAML, and triggers a rolling
restart.

## Dependency footprint

The runtime serving image now ships `onnxruntime>=1.21,<2`
(~30 MB). The training stack (`torch`, `mlflow`, `sklearn`, `Faker`)
stays in the optional `[ml]` extra (~500 MB) so the runtime
container doesn't pay that cost. Only the lightweight inference
engine is in the default deps.

## Graceful degradation

The model is **not strictly required for the application to boot**.
If the ONNX file is missing :

- `ChurnPredictor.is_ready()` returns `False`.
- REST endpoint returns `503` with the ConfigMap hint.
- MCP tool returns `ChurnServiceUnavailable` with a hint.
- All other Iris endpoints (Customer / Order / Product / MCP /
  actuator-equivalent) keep working unchanged.

This pattern lets us deploy the Python service before the model is
ready (e.g. on a fresh cluster where the ConfigMap hasn't been
provisioned yet) without gating the whole stack on the ML
promotion.

## Cross-language parity

The 8 features + their canonical order (per [shared ADR-0061](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0061-customer-churn-prediction.md)
§"Feature engineering") are the contract — both
[`iris_service.ml.inference.extract_features`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/src/iris_service/ml/inference.py)
(Python runtime, single-customer) and the Java
[`ChurnFeatureExtractor`](https://gitlab.com/iris-7/iris-service-java/-/blob/main/src/main/java/com/mirador/ml/ChurnFeatureExtractor.java)
implement the same logic. Tests on both sides assert determinism
on golden inputs.

A separate training-side
[`feature_engineering.build_features`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/src/iris_service/ml/feature_engineering.py)
operates on pandas DataFrames (vectorised across millions of
customers). At inference time we use the lightweight per-customer
extractor since pandas would only add overhead for one row.

## Testing

Unit tests under `tests/ml/` :

- `test_risk_band.py` (10) — boundary classification + custom-threshold
  rejection. Mirrors Java's `RiskBandTest`.
- `test_dtos.py` (4) — Pydantic shape + Field constraints.
- `test_inference.py` (12) — feature engineering parity with Java +
  graceful-degradation paths + ONNX session stubbing for the
  inference path.
- `test_router_churn.py` (5) — REST endpoint 200 / 404 / 503 paths
  with `app.dependency_overrides` swapping in a stub predictor.

These run on every CI invocation (no `--extra ml` needed) — the
runtime side ships in the default deps. The training-side tests
(`test_features.py`, `test_model.py`, `test_onnx_export.py`) skip
themselves via `pytest.importorskip` when `--extra ml` is absent.

## What's next (Phase D → F)

| Phase | Repo | Scope |
|---|---|---|
| **D** | iris-ui | `/insights/churn` page : top-10 at-risk + search-by-id + drift |
| **E** | iris-service-shared | MLflow tracking + drift SLO + dashboard + runbook |
| **F** | iris-service-shared | `bin/ml/promote_to_configmap.sh` + K8s volumeMount + Argo CD GitOps |

## References

- [shared ADR-0060 — ONNX cross-language inference](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0060-onnx-cross-language-ml-inference.md)
- [shared ADR-0061 — Customer Churn pipeline](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0061-customer-churn-prediction.md)
- [shared ADR-0062 — MLflow registry + ConfigMap promotion](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0062-mlflow-registry-configmap-promotion.md)
- [Java sibling — Phase B feature documentation](https://gitlab.com/iris-7/iris-service-java/-/blob/main/docs/ml/churn-prediction.md)
- [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)
