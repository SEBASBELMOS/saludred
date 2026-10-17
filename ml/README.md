# Servicio `ml`

Servicio de predicción de SaludRed, en su propio contenedor. Dos tareas:

- **Criticidad del paciente** (`POST /predict/criticality`): probabilidad de que el paciente necesite ingreso con urgencia.
- **Retraso de limpieza** (`POST /predict/cleaning-delay`): probabilidad de que una cama que entra a limpieza tarde más de 90 minutos.

Para cada tarea, al construir la imagen se entrenan y comparan diez modelos supervisados (regresión logística, árbol, KNN, random forest, gradient boosting, LightGBM, XGBoost, SVM, red neuronal y stacking) con el mismo protocolo (pipeline estandarizar, imputar con KNN, modelo), y el servicio atiende al ganador. Para limpieza también se ajusta una regresión de los minutos hasta cama lista, atendida en `POST /predict/cleaning-minutes`. La comparación completa queda en `GET /models` (sección `comparacion`) y en `artifacts/REPORTE_MODELOS.md`; una copia del último reporte está en `REPORTE_MODELOS.md`.

También expone `GET /models` (métricas, comparación, umbrales y sesgo por subgrupo) y `GET /health`.

No accede a la base de datos, no publica puertos en `docker-compose.yml` y rechaza (422) cualquier campo que no esté en su contrato, de modo que ningún identificador puede llegarle. El diseño, las métricas y la justificación del umbral están en `docs/IA.md`.

## Contenido

| Ruta | Qué es |
|---|---|
| `app.py` | API del servicio |
| `train.py` | Orquesta el entrenamiento; escribe `artifacts/` (modelos ganadores, `metrics.json` y `REPORTE_MODELOS.md`) |
| `compare.py` | Los diez modelos, imputación KNN, ablación mediana frente a KNN, validación cruzada anidada, umbral fuera de muestra y regla del ganador |
| `regress.py` | Regresión de los minutos hasta cama lista (línea base, regresión lineal, random forest) |
| `explain.py`, `report.py` | Importancia por permutación, razones de momios y el reporte en español |
| `features.py`, `augment.py` | Codificación de variables, imputación y aumento por grupos |
| `data/` | CSV de entrenamiento, sin identificadores |
| `tools/` | Scripts para regenerar los CSV (herramientas de desarrollo, no entran a la imagen) |
| `tests/` | Pruebas del servicio y del protocolo de evaluación |

## Datos y atribución

- Criticidad: MIMIC-IV-ED Demo v2.2 y MIMIC-IV Clinical Database Demo v2.2 (PhysioNet, ODbL 1.0), 192 visitas reales desidentificadas. Citas y detalle en `docs/DATASETS.md`.
- Limpieza: eventos simulados (`tools/generate_cleaning_events.py`, semilla 2026).

## Uso local

```bash
cd ml
pip install -r requirements-dev.txt
python train.py          # escribe artifacts/
pytest                   # entrena en un directorio temporal y prueba el servicio
uvicorn app:app --port 8001
```

En Docker los modelos se entrenan al construir la imagen (`docker compose build ml`).
