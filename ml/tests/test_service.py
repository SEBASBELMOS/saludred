"""Contract of the prediction service: validation, imputation, thresholds."""

from __future__ import annotations

PSEUDONYM = "pz-0123456789abcdef"
SICK = {
    "pseudonym": PSEUDONYM,
    "age": 78,
    "sex": "male",
    "temperature_c": 38.9,
    "heart_rate": 118,
    "resp_rate": 28,
    "o2sat": 86,
    "sbp": 92,
    "dbp": 50,
    "acuity": 2,
}


def _serve_family(artifacts, tmp_path, key: str):
    """A service whose criticality model is ``key`` (the winner may be any family)."""

    import shutil

    import joblib
    from fastapi.testclient import TestClient

    import compare
    import train
    from app import create_app
    from features import CRITICALITY_SPEC, FeatureSpec

    out, _ = artifacts
    folder = tmp_path / key
    shutil.copytree(out, folder)
    records, y, groups = train.load_criticality(train.DATA_DIR / "criticality_triage.csv")
    spec = FeatureSpec(CRITICALITY_SPEC.numeric, dict(CRITICALITY_SPEC.categorical))
    spec.fit_defaults(records)
    model = compare.model_by_key(key)
    estimator, _, _ = compare.fit_model(
        model, spec.matrix(records), y, groups=groups, spec=spec, use_augmentation=True, seed=1
    )
    joblib.dump(
        {
            "name": "criticality",
            "version": f"criticality-{model.code}-1.0-test",
            "family": key,
            "explain": "occlusion",
            "spec": spec,
            "estimator": estimator,
            "threshold": 0.5,
            "positive_label": "critico",
            "negative_label": "no_critico",
        },
        folder / "criticality.joblib",
    )
    return TestClient(create_app(folder))


def test_a_non_linear_winner_is_served_with_the_same_contract(artifacts, tmp_path) -> None:
    for key in ("rf", "svm"):
        with _serve_family(artifacts, tmp_path, key) as service:
            body = service.post("/predict/criticality", json=SICK).json()
            assert body["model_version"] == f"criticality-{key}-1.0-test"
            assert body["model_family"] == key
            assert "valor típico" in body["explanation"]
            assert {"feature", "value", "contribution", "imputed"} == set(body["top_features"][0])
            assert len(body["top_features"]) == 3
            assert service.post(
                "/predict/criticality", json={**SICK, "name": "Maria"}
            ).status_code == 422


def test_occlusion_contribution_is_zero_for_inputs_that_were_imputed(
    artifacts, tmp_path
) -> None:
    with _serve_family(artifacts, tmp_path, "rf") as service:
        bundle = service.app.state.criticality
        record = {"age": None, "sex": None, "heart_rate": 118.0, "o2sat": 86.0, "sbp": 92.0}
        full = {name: record.get(name) for name in bundle.spec.inputs}
        vector, imputed = bundle.spec.encode(full)
        probability = float(bundle._probability(vector.reshape(1, -1))[0])
        scores = bundle._scores(full, vector, probability)
        # An imputed input already holds the training default: replacing it changes nothing.
        assert imputed and all(scores[name] == 0 for name in imputed)
        assert any(scores[name] != 0 for name in scores if name not in imputed)


def test_health_lists_both_models(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert set(body["models"]) == {"criticality", "cleaning_delay"}


def test_models_returns_the_comparison_and_names_the_served_winner(client) -> None:
    body = client.get("/models").json()
    codes = {"lr", "dt", "knn", "rf", "gb", "svm", "mlp", "stack"}
    for task, prefix in (("criticality", "criticality-"), ("cleaning_delay", "cleaning-delay-")):
        block = body[task]
        comparison = block["comparacion"]
        assert len(comparison["models"]) == 8
        winner = next(m for m in comparison["models"] if m["winner"])
        assert winner["id"] == comparison["winner"] == block["winner"]
        # The version carries the family of the model that is actually served.
        family = block["model_version"][len(prefix) :].split("-1.0-")[0]
        assert family in codes
        assert block["evaluation"]["threshold"] == winner["threshold"]
        assert client.get("/health").json()["models"][task] == block["model_version"]


def test_models_exposes_metrics_threshold_and_bias(client) -> None:
    body = client.get("/models").json()
    crit = body["criticality"]
    assert crit["model_version"].startswith("criticality-")
    assert 0 < crit["evaluation"]["threshold"] < 1
    assert crit["evaluation"]["at_threshold"]["recall"] >= 0.85
    assert {"sex=female", "sex=male", "age=<45", "age=45-64", "age=>=65"} <= set(
        crit["bias_by_subgroup"]
    )
    for group in crit["bias_by_subgroup"].values():
        assert group["n"] > 0
    assert crit["validation"]["evaluated_on_synthetic_rows"] is False
    assert body["cleaning_delay"]["evaluation"]["auc"]["mean"] > 0.75


def test_criticality_prediction_shape(client) -> None:
    response = client.post("/predict/criticality", json=SICK)
    assert response.status_code == 200, response.text
    body = response.json()
    assert 0 <= body["probability"] <= 1
    assert body["nivel"] in {"critico", "no_critico"}
    assert body["positive"] == (body["nivel"] == "critico")
    assert body["pseudonym"] == PSEUDONYM
    assert body["imputed"] == []
    assert len(body["top_features"]) == 3
    assert {"feature", "value", "contribution", "imputed"} == set(body["top_features"][0])
    assert body["model_family"] and body["explanation"]
    assert body["model_version"] == client.get("/models").json()["criticality"]["model_version"]
    assert body["threshold"] == client.get("/models").json()["criticality"]["evaluation"]["threshold"]


def test_sicker_patient_scores_higher_than_a_stable_one(client) -> None:
    stable = {**SICK, "o2sat": 99, "sbp": 135, "dbp": 80, "temperature_c": 36.6, "acuity": 4}
    high = client.post("/predict/criticality", json=SICK).json()["probability"]
    low = client.post("/predict/criticality", json=stable).json()["probability"]
    assert high > low


def test_missing_features_are_imputed_and_reported(client) -> None:
    body = client.post(
        "/predict/criticality",
        json={"pseudonym": PSEUDONYM, "heart_rate": 90, "o2sat": 95, "sbp": 120},
    ).json()
    assert set(body["imputed"]) == {
        "age", "temperature_c", "resp_rate", "dbp", "acuity", "sex"
    }


def test_identifiers_are_rejected(client) -> None:
    for field, value in (
        ("name", "Maria Perez"),
        ("document_number", "1032456789"),
        ("email", "maria@example.org"),
        ("phone", "3004567890"),
        ("patient_id", "6b9f0f64-6a3a-4a52-9d24-1d8b7d3a4f10"),
    ):
        response = client.post("/predict/criticality", json={**SICK, field: value})
        assert response.status_code == 422, field


def test_pseudonym_format_is_enforced(client) -> None:
    assert client.post("/predict/criticality", json={**SICK, "pseudonym": "Maria Perez"}).status_code == 422
    missing = {k: v for k, v in SICK.items() if k != "pseudonym"}
    assert client.post("/predict/criticality", json=missing).status_code == 422


def test_implausible_values_and_empty_requests_are_rejected(client) -> None:
    assert client.post("/predict/criticality", json={**SICK, "o2sat": 140}).status_code == 422
    assert client.post("/predict/criticality", json={**SICK, "sbp": 60, "dbp": 70}).status_code == 422
    assert client.post("/predict/criticality", json={"pseudonym": PSEUDONYM, "age": 40}).status_code == 422


def test_cleaning_delay_prediction(client) -> None:
    busy_night = {
        "service": "UCI Adultos",
        "release_reason": "transfer",
        "isolation": "spores",
        "release_hour": 3,
        "weekday": 2,
        "previous_stay_hours": 200,
        "cleaning_staff_on_shift": 1,
        "pending_cleaning_beds": 4,
        "service_occupancy": 0.97,
        "curtains_change_required": True,
        "patient_waiting": True,
    }
    easy_morning = {
        **busy_night,
        "service": "Urgencias",
        "release_reason": "discharge",
        "isolation": "none",
        "release_hour": 10,
        "cleaning_staff_on_shift": 5,
        "pending_cleaning_beds": 0,
        "service_occupancy": 0.7,
        "curtains_change_required": False,
        "previous_stay_hours": 5,
    }
    late = client.post("/predict/cleaning-delay", json=busy_night).json()
    on_time = client.post("/predict/cleaning-delay", json=easy_morning).json()
    assert late["probability"] > on_time["probability"]
    assert late["nivel"] == "retraso" and on_time["nivel"] == "a_tiempo"
    assert late["imputed"] == [] and late["pseudonym"] is None


def test_cleaning_accepts_partial_input_and_unknown_service(client) -> None:
    body = client.post(
        "/predict/cleaning-delay", json={"service": "Cuidados Especiales", "release_hour": 14}
    ).json()
    assert "unrecognized" in body and body["unrecognized"] == ["service"]
    assert "weekday" in body["imputed"]


def test_cleaning_rejects_patient_data(client) -> None:
    assert client.post("/predict/cleaning-delay", json={"patient_name": "Maria"}).status_code == 422
    assert client.post("/predict/cleaning-delay", json={"pseudonym": PSEUDONYM}).status_code == 422
