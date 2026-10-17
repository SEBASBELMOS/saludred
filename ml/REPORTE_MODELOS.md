# Reporte de modelos de IA de SaludRed

Generado por `ml/train.py` en cada construcción de la imagen; los números salen de la misma corrida que `metrics.json` y de los modelos que atiende el servicio. Semilla fija: 2026.

Modelos comparados: regresión logística, KNN, SVM con kernel RBF, árbol de decisión, random forest (bagging), gradient boosting de scikit-learn, LightGBM, XGBoost, stacking (logística + random forest + boosting con meta-modelo logístico) y una red neuronal pequeña (MLP). Todos reciben los datos estandarizados y con los faltantes imputados de forma explícita (KNN, ajustado solo con el entrenamiento de cada fold).

Limitaciones que aplican a todo el reporte: la cohorte de criticidad es una muestra pequeña (demo de MIMIC-IV-ED); el umbral se elige con las mismas predicciones fuera de muestra con que se reporta, por lo que la sensibilidad objetivo se cumple por construcción; los modelos apoyan la priorización y no sustituyen la decisión clínica.

## Criticidad del paciente

- Datos: 192 filas, tasa de positivos 66.1%. Validación: 3 repeticiones de validación cruzada estratificada de 5 folds agrupada por paciente (las visitas de un paciente quedan en el mismo fold).
- Se evalúa solo con filas reales; el aumento de datos se usa únicamente al entrenar cada fold.
- Ajuste de hiperparámetros dentro de cada fold de entrenamiento (3 folds internos, AUC). Umbral elegido sobre predicciones fuera de muestra, sensibilidad mínima 85.0%.
- Regla del ganador: El umbral de cada modelo es el más alto cuya sensibilidad fuera de muestra alcanza 85%. Gana el modelo con mayor F1 fuera de muestra entre los que cumplen esa sensibilidad; si otro modelo queda a menos de un error estándar del F1 del mejor, se considera un empate práctico y gana el más simple y explicable (regresión logística, árbol, KNN, random forest, gradient boosting, LightGBM, XGBoost, SVM, red neuronal, stacking, en ese orden).

### Comparación de modelos

| Modelo | AUC (media ± DE) | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP | Ajuste (s/fold) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0.623 ± 0.038 | 0.746 | 0.666 | 0.850 | 0.164 | 0.415 | 10.7 | 54.3 | 19.0 | 108.0 | 0.0 |
| Árbol de decisión | 0.554 ± 0.016 | 0.754 | 0.676 | 0.853 | 0.200 | 0.450 | 13.0 | 52.0 | 18.7 | 108.3 | 0.1 |
| KNN | 0.585 ± 0.018 | 0.753 | 0.671 | 0.861 | 0.174 | 0.330 | 11.3 | 53.7 | 17.7 | 109.3 | 0.1 |
| Random forest (bagging) | 0.576 ± 0.035 | 0.760 | 0.686 | 0.853 | 0.236 | 0.490 | 15.3 | 49.7 | 18.7 | 108.3 | 0.2 |
| Gradient boosting | 0.581 ± 0.024 | 0.752 | 0.674 | 0.850 | 0.195 | 0.465 | 12.7 | 52.3 | 19.0 | 108.0 | 0.7 |
| LightGBM | 0.558 ± 0.027 | 0.747 | 0.667 | 0.850 | 0.169 | 0.440 | 11.0 | 54.0 | 19.0 | 108.0 | 0.2 |
| XGBoost | 0.574 ± 0.013 | 0.749 | 0.669 | 0.850 | 0.179 | 0.415 | 11.7 | 53.3 | 19.0 | 108.0 | 0.2 |
| SVM (kernel RBF) | 0.550 ± 0.048 | 0.751 | 0.673 | 0.850 | 0.195 | 0.395 | 12.7 | 52.3 | 19.0 | 108.0 | 0.3 |
| Red neuronal (MLP) | 0.616 ± 0.034 | 0.753 | 0.675 | 0.850 | 0.200 | 0.430 | 13.0 | 52.0 | 19.0 | 108.0 | 0.9 |
| Stacking (LR + RF + GB) | 0.574 ± 0.035 | 0.758 | 0.684 | 0.850 | 0.231 | 0.430 | 15.0 | 50.0 | 19.0 | 108.0 | 0.6 |

Las métricas son el promedio de 3 repeticiones de validación cruzada de 5 folds; la matriz de confusión es el conteo promedio por repetición (TN = verdaderos negativos, FP = falsos positivos, FN = falsos negativos, TP = verdaderos positivos).

Línea base (marcar a todos): sensibilidad 100%, precisión 66.1%, F1 0.796. Ningún modelo la supera en F1: con tantos positivos, marcar a todos ya da un F1 alto; lo que aportan los modelos es especificidad (descartar casos), no F1.

El error estándar del F1 del mejor modelo (Random forest (bagging)) es 0.023: diferencias de F1 menores que eso entre modelos son ruido.

**Por qué ganó Regresión logística:** mejor F1 entre los modelos que cumplen la sensibilidad objetivo; Random forest (bagging) tiene el F1 más alto (0.760), pero la diferencia con Regresión logística (0.746) es de 0.014, menor que un error estándar del F1 (0.023): es un empate práctico y se prefiere el modelo más simple y explicable.

**SVM, C y gamma juntos:** se ajustaron en una sola malla (C en 0.25, 1, 4 y gamma en «scale», 0.01, 0.1). El valor más elegido en los folds fue C = 1.0 y gamma = 0.01; AUC 0.550 ± 0.048, F1 0.751.

**Boosting:** Gradient boosting AUC 0.581 ± 0.024, F1 0.752; LightGBM AUC 0.558 ± 0.027, F1 0.747; XGBoost AUC 0.574 ± 0.013, F1 0.749.

### Imputación de valores faltantes: mediana frente a KNN

Filas de entrenamiento con algún valor faltante: 0 (0 celdas). El conjunto de entrenamiento está completo: con él ambos imputadores darían exactamente lo mismo, así que la comparación se hizo borrando al azar 14.4% de las celdas numéricas (mismas celdas para los dos métodos, sin aumento de datos porque no puede interpolar vacíos). En el servicio los faltantes sí aparecen (por ejemplo, un paciente sin nivel de triage).

| Imputación | AUC | F1 | Error estándar del F1 | Sensibilidad | Precisión |
|---|---|---|---|---|---|
| Mediana | 0.621 | 0.765 | 0.017 | 0.858 | 0.690 |
| KNN (k = 5) | 0.631 | 0.759 | 0.016 | 0.861 | 0.679 |

Modelo usado en la comparación: Regresión logística (el ganador). Mismo protocolo para ambos: el imputador se ajusta solo con las filas de entrenamiento de cada fold y se aplica después de estandarizar. **Decisión:** KNN no es peor que la mediana más allá de un error estándar del F1 (0.006 frente a 0.016); se usa KNN. Imputación en servicio: KNN.

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
| Temperatura | 0.020 |
| Frecuencia respiratoria | 0.005 |
| Saturación de oxígeno | 0.001 |
| Edad | -0.002 |

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
- Regla del ganador: El umbral de cada modelo es el más alto cuya sensibilidad fuera de muestra alcanza 80%. Gana el modelo con mayor F1 fuera de muestra entre los que cumplen esa sensibilidad; si otro modelo queda a menos de un error estándar del F1 del mejor, se considera un empate práctico y gana el más simple y explicable (regresión logística, árbol, KNN, random forest, gradient boosting, LightGBM, XGBoost, SVM, red neuronal, stacking, en ese orden).

### Comparación de modelos

| Modelo | AUC (media ± DE) | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP | Ajuste (s/fold) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0.823 ± 0.002 | 0.525 | 0.389 | 0.806 | 0.671 | 0.150 | 426.0 | 209.0 | 32.0 | 133.0 | 0.0 |
| Árbol de decisión | 0.777 ± 0.003 | 0.487 | 0.349 | 0.806 | 0.609 | 0.115 | 386.7 | 248.3 | 32.0 | 133.0 | 0.0 |
| KNN | 0.783 ± 0.003 | 0.507 | 0.368 | 0.814 | 0.636 | 0.160 | 404.0 | 231.0 | 30.7 | 134.3 | 0.1 |
| Random forest (bagging) | 0.808 ± 0.004 | 0.519 | 0.383 | 0.804 | 0.664 | 0.180 | 421.3 | 213.7 | 32.3 | 132.7 | 0.2 |
| Gradient boosting | 0.807 ± 0.000 | 0.507 | 0.370 | 0.806 | 0.643 | 0.145 | 408.3 | 226.7 | 32.0 | 133.0 | 0.7 |
| LightGBM | 0.808 ± 0.009 | 0.499 | 0.361 | 0.808 | 0.628 | 0.140 | 399.0 | 236.0 | 31.7 | 133.3 | 0.2 |
| XGBoost | 0.810 ± 0.006 | 0.507 | 0.371 | 0.804 | 0.645 | 0.150 | 409.7 | 225.3 | 32.3 | 132.7 | 0.2 |
| SVM (kernel RBF) | 0.779 ± 0.006 | 0.457 | 0.315 | 0.832 | 0.530 | 0.160 | 336.3 | 298.7 | 27.7 | 137.3 | 0.5 |
| Red neuronal (MLP) | 0.812 ± 0.004 | 0.521 | 0.386 | 0.802 | 0.669 | 0.155 | 424.7 | 210.3 | 32.7 | 132.3 | 1.2 |
| Stacking (LR + RF + GB) | 0.816 ± 0.003 | 0.515 | 0.378 | 0.808 | 0.655 | 0.130 | 415.7 | 219.3 | 31.7 | 133.3 | 0.6 |

Las métricas son el promedio de 3 repeticiones de validación cruzada de 5 folds; la matriz de confusión es el conteo promedio por repetición (TN = verdaderos negativos, FP = falsos positivos, FN = falsos negativos, TP = verdaderos positivos).

Línea base (marcar a todos): sensibilidad 100%, precisión 20.6%, F1 0.342. La superan en F1: Regresión logística, Árbol de decisión, KNN, Random forest (bagging), Gradient boosting, LightGBM, XGBoost, SVM (kernel RBF), Red neuronal (MLP), Stacking (LR + RF + GB).

El error estándar del F1 del mejor modelo (Regresión logística) es 0.013: diferencias de F1 menores que eso entre modelos son ruido.

**Por qué ganó Regresión logística:** mejor F1 entre los modelos que cumplen la sensibilidad objetivo.

**SVM, C y gamma juntos:** se ajustaron en una sola malla (C en 0.25, 1, 4 y gamma en «scale», 0.01, 0.1). El valor más elegido en los folds fue C = 0.25 y gamma = 0.01; AUC 0.779 ± 0.006, F1 0.457.

**Boosting:** Gradient boosting AUC 0.807 ± 0.000, F1 0.507; LightGBM AUC 0.808 ± 0.009, F1 0.499; XGBoost AUC 0.810 ± 0.006, F1 0.507.

### Imputación de valores faltantes: mediana frente a KNN

Filas de entrenamiento con algún valor faltante: 0 (0 celdas). El conjunto de entrenamiento está completo: con él ambos imputadores darían exactamente lo mismo, así que la comparación se hizo borrando al azar 14.3% de las celdas numéricas (mismas celdas para los dos métodos, sin aumento de datos porque no puede interpolar vacíos). En el servicio los faltantes sí aparecen (por ejemplo, un paciente sin nivel de triage).

| Imputación | AUC | F1 | Error estándar del F1 | Sensibilidad | Precisión |
|---|---|---|---|---|---|
| Mediana | 0.817 | 0.527 | 0.012 | 0.804 | 0.393 |
| KNN (k = 5) | 0.817 | 0.525 | 0.013 | 0.810 | 0.388 |

Modelo usado en la comparación: Regresión logística (el ganador). Mismo protocolo para ambos: el imputador se ajusta solo con las filas de entrenamiento de cada fold y se aplica después de estandarizar. **Decisión:** KNN no es peor que la mediana más allá de un error estándar del F1 (0.002 frente a 0.013); se usa KNN. Imputación en servicio: KNN.

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
| Aislamiento | 0.092 |
| Servicio | 0.039 |
| Cambio de cortinas | 0.018 |
| Personal de limpieza en turno | 0.011 |
| Camas pendientes de limpieza | 0.007 |

La variable que más pesa es «Aislamiento»: al mezclar sus valores entre pacientes el AUC baja 0.092. Cuanto más baja el AUC, más depende el modelo de esa variable.

### Razones de momios (Regresión logística)

Regresión logística estandarizada (L2, C = 1) ajustada con todas las filas. Para una variable numérica, la razón de momios es exp(coeficiente): el cambio de los momios por una desviación estándar. Para una categoría, compara estar en ella frente a no estarlo. La regularización encoge un poco los coeficientes.

| Variable | Razón de momios | Lectura |
|---|---|---|
| Camas pendientes de limpieza | 1.66 | Por cada desviación estándar más de «Camas pendientes de limpieza»: los momios de retraso de limpieza aumentan 66% (razón de momios 1.66). |
| Servicio: Urgencias | 0.25 | Estar en «Servicio: Urgencias» frente a no estarlo: los momios de retraso de limpieza disminuyen 75% (razón de momios 0.25). |
| Servicio: UCI Adultos | 2.34 | Estar en «Servicio: UCI Adultos» frente a no estarlo: los momios de retraso de limpieza aumentan 134% (razón de momios 2.34). |
| Aislamiento: esporas | 6.65 | Estar en «Aislamiento: esporas» frente a no estarlo: los momios de retraso de limpieza aumentan 565% (razón de momios 6.65). |
| Cambio de cortinas | 1.42 | Por cada desviación estándar más de «Cambio de cortinas»: los momios de retraso de limpieza aumentan 42% (razón de momios 1.42). |

### Regresión: minutos hasta que la cama queda lista

Variable continua `minutes_to_ready` (media 73.9 min, mediana 71.3 min, rango 27.1-147.2). Mismas variables de entrada, imputación KNN dentro del fold y 3 repeticiones de validación cruzada de 5 folds.

| Modelo | MAE (min) | DE del MAE | R² |
|---|---|---|---|
| Línea base (promedio) | 17.07 | 0.84 | -0.007 |
| **Regresión lineal (en servicio)** | 11.19 | 0.88 | 0.530 |
| Random forest (regresión) | 11.89 | 0.91 | 0.465 |

Regresión lineal tiene el menor error absoluto medio (MAE) entre los modelos servibles. Frente al promedio, el modelo en servicio se equivoca 5.88 minutos menos en promedio. Se atiende en `POST /predict/cleaning-minutes` (versión `cleaning-minutes-lin-1.0-906099de`).

## Qué se aplicó de la clase del 06/10

| Lo que enseñó el profesor | Dónde aparece |
|---|---|
| SVM: optimizar C y gamma a la vez | Una sola malla C × gamma dentro del CV (limpieza: C = 0.25, gamma = 0.01). |
| La mediana no es el método más apropiado; usar KNN | Imputación KNN dentro de cada fold; ablación contra mediana en ambas tareas (limpieza: knn, criticidad: knn). |
| No depender de la imputación interna del modelo | Todos los modelos son pipelines estandarizar -> imputar -> modelo. |
| Bagging frente a boosting; LightGBM, XGBoost, CatBoost | Random forest (bagging), gradient boosting, LightGBM y XGBoost en la misma comparación. CatBoost no se incluyó: su ventaja es manejar variables categóricas con muchas categorías y aquí hay pocas, de modo que no justifica una dependencia más pesada en la imagen. |
| Importancia de variables para detectar sesgo | Importancia por permutación por tarea (criticidad: «Presión sistólica»; limpieza: «Aislamiento») y desempeño por subgrupo. |
| Stacking | Modelo «Stacking (LR + RF + GB)» con meta-modelo logístico. |
| Regresión lineal (variable continua) | Minutos hasta cama lista: línea base, regresión lineal y random forest, con MAE y R². |
| Métrica y umbral | F1, sensibilidad y especificidad; umbral elegido fuera de muestra; regla de un error estándar. |
