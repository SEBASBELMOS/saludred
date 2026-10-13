# Reporte de modelos de IA de SaludRed

Generado por `ml/train.py` en cada construcción de la imagen; los números salen de la misma corrida que `metrics.json` y de los modelos que atiende el servicio. Semilla fija: 2026.

Modelos comparados: regresión logística, KNN, SVM con kernel RBF, árbol de decisión, random forest (bagging), gradient boosting de scikit-learn, stacking (logística + random forest + boosting con meta-modelo logístico) y una red neuronal pequeña (MLP).

Limitaciones que aplican a todo el reporte: la cohorte de criticidad es una muestra pequeña (demo de MIMIC-IV-ED); el umbral se elige con las mismas predicciones fuera de muestra con que se reporta, por lo que la sensibilidad objetivo se cumple por construcción; los modelos apoyan la priorización y no sustituyen la decisión clínica.

## Criticidad del paciente

- Datos: 192 filas, tasa de positivos 66.1%. Validación: 3 repeticiones de validación cruzada estratificada de 5 folds agrupada por paciente (las visitas de un paciente quedan en el mismo fold).
- Se evalúa solo con filas reales; el aumento de datos se usa únicamente al entrenar cada fold.
- Ajuste de hiperparámetros dentro de cada fold de entrenamiento (3 folds internos, AUC). Umbral elegido sobre predicciones fuera de muestra, sensibilidad mínima 85.0%.
- Regla del ganador: El umbral de cada modelo es el más alto cuya sensibilidad fuera de muestra alcanza 85%. Gana el modelo con mayor F1 fuera de muestra entre los que cumplen esa sensibilidad; si otro modelo queda a menos de un error estándar del F1 del mejor, se considera un empate práctico y gana el más simple y explicable (regresión logística, árbol, KNN, random forest, boosting, SVM, red neuronal, stacking, en ese orden).

### Comparación de modelos

| Modelo | AUC (media ± DE) | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP | Ajuste (s/fold) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0.623 ± 0.038 | 0.746 | 0.666 | 0.850 | 0.164 | 0.415 | 10.7 | 54.3 | 19.0 | 108.0 | 0.2 |
| Árbol de decisión | 0.556 ± 0.017 | 0.750 | 0.669 | 0.856 | 0.169 | 0.435 | 11.0 | 54.0 | 18.3 | 108.7 | 0.2 |
| KNN | 0.585 ± 0.018 | 0.753 | 0.671 | 0.861 | 0.174 | 0.330 | 11.3 | 53.7 | 17.7 | 109.3 | 0.2 |
| Random forest (bagging) | 0.576 ± 0.035 | 0.760 | 0.686 | 0.853 | 0.236 | 0.490 | 15.3 | 49.7 | 18.7 | 108.3 | 0.3 |
| Gradient boosting | 0.581 ± 0.025 | 0.752 | 0.674 | 0.850 | 0.195 | 0.465 | 12.7 | 52.3 | 19.0 | 108.0 | 0.8 |
| SVM (kernel RBF) | 0.538 ± 0.021 | 0.756 | 0.680 | 0.853 | 0.215 | 0.365 | 14.0 | 51.0 | 18.7 | 108.3 | 0.3 |
| Red neuronal (MLP) | 0.616 ± 0.034 | 0.753 | 0.675 | 0.850 | 0.200 | 0.430 | 13.0 | 52.0 | 19.0 | 108.0 | 1.1 |
| Stacking (LR + RF + GB) | 0.573 ± 0.035 | 0.759 | 0.684 | 0.853 | 0.231 | 0.430 | 15.0 | 50.0 | 18.7 | 108.3 | 0.8 |

Las métricas son el promedio de 3 repeticiones de validación cruzada de 5 folds; la matriz de confusión es el conteo promedio por repetición (TN = verdaderos negativos, FP = falsos positivos, FN = falsos negativos, TP = verdaderos positivos).

Línea base (marcar a todos): sensibilidad 100%, precisión 66.1%, F1 0.796. Ningún modelo la supera en F1: con tantos positivos, marcar a todos ya da un F1 alto; lo que aportan los modelos es especificidad (descartar casos), no F1.

El error estándar del F1 del mejor modelo (Random forest (bagging)) es 0.023: diferencias de F1 menores que eso entre modelos son ruido.

**Por qué ganó Regresión logística:** mejor F1 entre los modelos que cumplen la sensibilidad objetivo; Random forest (bagging) tiene el F1 más alto (0.760), pero la diferencia con Regresión logística (0.746) es de 0.014, menor que un error estándar del F1 (0.023): es un empate práctico y se prefiere el modelo más simple y explicable.

### Matriz de confusión de Regresión logística

|  | Marcado por el modelo | No marcado |
|---|---|---|
| **Caso real** | Críticos detectados: 108.0 | Críticos perdidos: 19.0 |
| **No es caso** | Falsas alarmas: 54.3 | No críticos descartados: 10.7 |

F1 0.746, precisión 66.6%, sensibilidad 85.0%, especificidad 16.4% con umbral 0.415.

### Importancia de variables (Random forest (bagging))

Importancia por permutación sobre filas fuera de muestra: caída del AUC al mezclar una variable (se promedia en los folds de la primera repetición).

| Variable | Caída del AUC |
|---|---|
| Presión sistólica | 0.046 |
| Temperatura | 0.021 |
| Frecuencia respiratoria | 0.006 |
| Saturación de oxígeno | 0.002 |
| Edad | -0.001 |

La variable que más pesa es «Presión sistólica»: al mezclar sus valores entre pacientes el AUC baja 0.046. Cuanto más baja el AUC, más depende el modelo de esa variable.

### Razones de momios (Regresión logística)

Regresión logística estandarizada (L2, C = 1) ajustada con todas las filas. Para una variable numérica, la razón de momios es exp(coeficiente): el cambio de los momios por una desviación estándar. Para una categoría, compara estar en ella frente a no estarlo. La regularización encoge un poco los coeficientes.

| Variable | Razón de momios | Lectura |
|---|---|---|
| Saturación de oxígeno | 0.59 | Por cada desviación estándar más de «Saturación de oxígeno»: los momios de ser crítico disminuyen 41% (razón de momios 0.59). |
| Presión sistólica | 0.60 | Por cada desviación estándar más de «Presión sistólica»: los momios de ser crítico disminuyen 40% (razón de momios 0.60). |
| Nivel de triage ESI | 0.63 | Por cada desviación estándar más de «Nivel de triage ESI»: los momios de ser crítico disminuyen 37% (razón de momios 0.63). |
| Temperatura | 1.29 | Por cada desviación estándar más de «Temperatura»: los momios de ser crítico aumentan 29% (razón de momios 1.29). |
| Frecuencia respiratoria | 1.21 | Por cada desviación estándar más de «Frecuencia respiratoria»: los momios de ser crítico aumentan 21% (razón de momios 1.21). |

Aumento de datos sintéticos en el entrenamiento (Regresión logística): AUC 0.623 con aumento y 0.634 sin aumento.

## Retraso de limpieza de camas

- Datos: 800 filas, tasa de positivos 20.6%. Validación: 3 repeticiones de validación cruzada estratificada de 5 folds.
- Los datos son simulados: las métricas describen la simulación, no hospitales reales.
- Ajuste de hiperparámetros dentro de cada fold de entrenamiento (3 folds internos, AUC). Umbral elegido sobre predicciones fuera de muestra, sensibilidad mínima 80.0%.
- Regla del ganador: El umbral de cada modelo es el más alto cuya sensibilidad fuera de muestra alcanza 80%. Gana el modelo con mayor F1 fuera de muestra entre los que cumplen esa sensibilidad; si otro modelo queda a menos de un error estándar del F1 del mejor, se considera un empate práctico y gana el más simple y explicable (regresión logística, árbol, KNN, random forest, boosting, SVM, red neuronal, stacking, en ese orden).

### Comparación de modelos

| Modelo | AUC (media ± DE) | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP | Ajuste (s/fold) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0.823 ± 0.002 | 0.525 | 0.389 | 0.806 | 0.671 | 0.150 | 426.0 | 209.0 | 32.0 | 133.0 | 0.0 |
| Árbol de decisión | 0.778 ± 0.003 | 0.487 | 0.349 | 0.806 | 0.609 | 0.115 | 386.7 | 248.3 | 32.0 | 133.0 | 0.0 |
| KNN | 0.783 ± 0.003 | 0.507 | 0.368 | 0.814 | 0.636 | 0.160 | 404.0 | 231.0 | 30.7 | 134.3 | 1.1 |
| Random forest (bagging) | 0.808 ± 0.004 | 0.520 | 0.384 | 0.804 | 0.665 | 0.180 | 422.3 | 212.7 | 32.3 | 132.7 | 0.2 |
| Gradient boosting | 0.807 ± 0.000 | 0.507 | 0.370 | 0.806 | 0.643 | 0.145 | 408.3 | 226.7 | 32.0 | 133.0 | 0.8 |
| SVM (kernel RBF) | 0.770 ± 0.007 | 0.478 | 0.339 | 0.810 | 0.590 | 0.140 | 374.3 | 260.7 | 31.3 | 133.7 | 0.2 |
| Red neuronal (MLP) | 0.812 ± 0.004 | 0.521 | 0.386 | 0.802 | 0.669 | 0.155 | 424.7 | 210.3 | 32.7 | 132.3 | 1.3 |
| Stacking (LR + RF + GB) | 0.816 ± 0.003 | 0.521 | 0.385 | 0.802 | 0.668 | 0.135 | 424.0 | 211.0 | 32.7 | 132.3 | 0.7 |

Las métricas son el promedio de 3 repeticiones de validación cruzada de 5 folds; la matriz de confusión es el conteo promedio por repetición (TN = verdaderos negativos, FP = falsos positivos, FN = falsos negativos, TP = verdaderos positivos).

Línea base (marcar a todos): sensibilidad 100%, precisión 20.6%, F1 0.342. La superan en F1: Regresión logística, Árbol de decisión, KNN, Random forest (bagging), Gradient boosting, SVM (kernel RBF), Red neuronal (MLP), Stacking (LR + RF + GB).

El error estándar del F1 del mejor modelo (Regresión logística) es 0.013: diferencias de F1 menores que eso entre modelos son ruido.

**Por qué ganó Regresión logística:** mejor F1 entre los modelos que cumplen la sensibilidad objetivo.

### Matriz de confusión de Regresión logística

|  | Marcado por el modelo | No marcado |
|---|---|---|
| **Caso real** | Retrasos detectados: 133.0 | Retrasos perdidos: 32.0 |
| **No es caso** | Falsas alarmas: 209.0 | A tiempo descartados: 426.0 |

F1 0.525, precisión 38.9%, sensibilidad 80.6%, especificidad 67.1% con umbral 0.150.

### Importancia de variables (Random forest (bagging))

Importancia por permutación sobre filas fuera de muestra: caída del AUC al mezclar una variable (se promedia en los folds de la primera repetición).

| Variable | Caída del AUC |
|---|---|
| Aislamiento | 0.091 |
| Servicio | 0.038 |
| Cambio de cortinas | 0.018 |
| Personal de limpieza en turno | 0.010 |
| Camas pendientes de limpieza | 0.007 |

La variable que más pesa es «Aislamiento»: al mezclar sus valores entre pacientes el AUC baja 0.091. Cuanto más baja el AUC, más depende el modelo de esa variable.

### Razones de momios (Regresión logística)

Regresión logística estandarizada (L2, C = 1) ajustada con todas las filas. Para una variable numérica, la razón de momios es exp(coeficiente): el cambio de los momios por una desviación estándar. Para una categoría, compara estar en ella frente a no estarlo. La regularización encoge un poco los coeficientes.

| Variable | Razón de momios | Lectura |
|---|---|---|
| Camas pendientes de limpieza | 1.66 | Por cada desviación estándar más de «Camas pendientes de limpieza»: los momios de retraso de limpieza aumentan 66% (razón de momios 1.66). |
| Servicio: Urgencias | 0.25 | Estar en «Servicio: Urgencias» frente a no estarlo: los momios de retraso de limpieza disminuyen 75% (razón de momios 0.25). |
| Servicio: UCI Adultos | 2.34 | Estar en «Servicio: UCI Adultos» frente a no estarlo: los momios de retraso de limpieza aumentan 134% (razón de momios 2.34). |
| Aislamiento: esporas | 6.65 | Estar en «Aislamiento: esporas» frente a no estarlo: los momios de retraso de limpieza aumentan 565% (razón de momios 6.65). |
| Cambio de cortinas | 1.42 | Por cada desviación estándar más de «Cambio de cortinas»: los momios de retraso de limpieza aumentan 42% (razón de momios 1.42). |
