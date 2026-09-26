# Archivos de prueba

| Archivo | Qué es | Origen |
|---|---|---|
| `CT_small.dcm` | Un corte de tomografía de 128 × 128, anonimizado | Datos de prueba del proyecto pydicom (`src/pydicom/data/test_files` en https://github.com/pydicom/pydicom), distribuidos para pruebas de software |

Se usa en `tests/test_pacs.py` para probar la subida de DICOM al PACS sin datos de
ninguna persona real.
