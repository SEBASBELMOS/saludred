# Paridad del frontend

Inventario previo a la migración, obtenido de la interfaz y de los contratos de la API.
Migración completada el 29 de septiembre de 2026. ✅ indica implementación revisada
y comprobada localmente; no equivale a una prueba contra la API desplegada. Todas
las rutas siguientes usan el prefijo `/api/v1`. La API mantiene la autoridad sobre
permisos y reglas.

| Estado / área | Comportamiento y validación que se conserva | API | Evidencia en `frontend/` |
|---|---|---|---|
| ✅ Sesión | Usuario y contraseña obligatorios; mensaje genérico 401; bloqueo ámbar 423; privacidad Ley 1581; token por pestaña, restauración y cierre; expiración en cualquier consulta | POST auth/login; GET auth/me | `src/features/auth/{AuthProvider,LoginPage}.tsx; src/api/client.ts; auth.test.tsx` |
| ✅ Navegación | Menús de cuatro roles, guardas, navegación sin recarga, foco, enlace de salto, actualizar y hora; errores con reintento, sin ceros falsos | Consultas activas de cada vista | `src/features/auth/{permissions.ts,RouteGuard.tsx,guards.test.tsx}; src/App.tsx; src/components/AppLayout.tsx` |
| ✅ Resumen | Métricas con filtros; una marca por cama; disponibilidad por IPS; tres solicitudes; apoyo de red; tabla por servicio | GET network/capacity; organizations/{id}/beds; bed-requests/queue | `src/features/capacity/CapacityPage.tsx; src/api/capacity.ts` |
| ✅ Cola | Orden del servidor; nombre/documento, IPS, servicio y prioridad; nueva solicitud según rol; ficha para operador | GET bed-requests/queue | `src/features/queue/QueuePage.tsx; src/components/Filters.tsx` |
| ✅ Asignación | Hasta ocho alternativas; misma IPS/otro servicio; anticipar solicitud anterior; conflicto 409; ver anterior o excepción con motivo obligatorio de hasta 300 caracteres; confirmación y actualización | GET bed-requests/{id}/candidates; POST bed-requests/{id}/assign | `src/features/queue/{AssignDialog.tsx,priority.ts,queue.test.tsx}` |
| ✅ Camas | Búsqueda/filtros; diálogo, motivo obligatorio; nunca opción Ocupada; liberar solo coordinación/admin hacia limpieza; cuatro movimientos recientes | POST beds/{id}/status; POST beds/{id}/release; GET beds/{id}/history | `src/features/beds/{BedsPage,BedDialog}.tsx; beds.test.tsx` |
| ✅ Búsqueda | Nunca listar sin criterio; documento exacto de seis caracteres o nombre de tres letras; máximo diez resultados y total; búsqueda con espera de 350 ms | GET patients?document_number=… o name=…&page_size=10 | `src/features/patients/{PatientsPage.tsx,validation.ts,patients.test.tsx}` |
| ✅ Registro | Nombres/apellidos 120; documento 4–32; fecha no futura; sexo, teléfono 40, correo 200; primera EPS; confirmación y búsqueda del registro | GET organizations?organization_type=EPS; POST patients | `src/features/patients/PatientFormDialog.tsx` |
| ✅ Ficha | Datos personales, perfil, últimas mediciones numéricas por LOINC, atenciones y estudios; errores independientes para imágenes | GET patients/{id}; patients/{id}/encounters; observations?patient_id=…&page_size=100; patients/{id}/imaging-studies?page_size=100 | `src/features/patients/{PatientRecordDialog,PatientRecordView}.tsx` |
| ✅ Edición | Admin o autor operador; documento de solo lectura; cambios parciales; fecha no futura; dirección 300; sin cambios no escribe | PUT patients/{id} | `src/features/patients/PatientFormDialog.tsx; patients.test.tsx` |
| ✅ Atención | Admin/operador; IPS propia o elegida, clase, prioridad, fecha no futura, motivo 500; nueve rangos LOINC; sistólica > diastólica; notificar mediciones parcialmente fallidas | POST encounters; POST observations por medición | `src/features/patients/{EncounterDialog.tsx,validation.ts,patients.test.tsx}` |
| ✅ Solicitud | Documento exacto o ficha actual; IPS, servicio, prioridad, motivo; reutilizar ingreso abierto en esa IPS o crear EMER/IMP; no duplicarlo al reintentar | GET patients?document_number=…; patients/{id}/encounters; POST encounters; POST bed-requests | `src/features/queue/{RequestDialog.tsx,queue.test.tsx}` |
| ✅ Imágenes | Carga binaria DICOM/PNG/JPEG; lista de instancias; blob autenticado; liberación de URLs; brillo 40–250, contraste 40–300, zoom 100–400, inversión, restablecer; aviso no diagnóstico | POST/GET imaging-studies/{id}/images; GET imaging/instances/{id}/preview | `src/features/imaging/{ImageViewer,StudyList}.tsx; imaging.test.tsx; src/api/imaging.ts` |
| ✅ Análisis | Recomendación por IPS; espera mediana/P90/máximo; cohortes; agrupamiento automático o 2–8, centroides, silueta, Rand y pureza; alistamiento; errores por panel | GET analytics/decision-summary, wait-times, patient-cohorts, patient-clusters?k=…, bed-turnaround | `src/features/analytics/AnalyticsPage.tsx; src/api/analytics.ts` |
| ✅ Cuentas | Admin; primeras 100 y filtro bloqueadas; intentos, estado, último ingreso; no actuar sobre sí mismo; bloquear/desbloquear con motivo 300; alta por rol, usuario 3–80 patrón permitido, clave 8–128, vínculo IPS/EPS/documento; auditoría últimos 20 | GET/POST admin/users; POST admin/users/{id}/lock o unlock; GET audit-logs?page_size=20; GET organizations?organization_type=EPS | `src/features/accounts/{AccountsPage,AccountDialogs}.tsx; src/api/accounts.ts` |
| ✅ Red de atención | Solo ADMIN; EPS expandibles e IPS anidadas con conteos; altas dedicadas sin padre en el cuerpo; edición parcial de código/nombre; historial con fecha y autor; borrado lógico, Deshacer y restauración desde lista temporal; límites 32/200, errores inline y reintento | GET organizations?organization_type=EPS&page_size=100; GET/POST eps/{id}/ips; POST eps; PUT/DELETE organizations/{id}; GET organizations/{id}/history; POST organizations/{id}/restore | `src/features/organizations/{OrganizationsPage,OrganizationDialogs}.tsx; organizationState.ts; organizations.test.tsx; src/api/organizations.ts; src/features/auth/{permissions.ts,guards.test.tsx}` |
| ✅ Portal | Solo información propia, atenciones, últimas mediciones y estudios con visor | GET me/patient, me/encounters, me/observations, me/imaging-studies | `src/features/portal/PortalPage.tsx; src/api/portal.ts` |

## Dirección visual

Teal de marca #126e62, acento #14b8a6, tinta #0f172a, fondo #f1f5f9,
blanco #ffffff y separación #cbd5e1. Estados semánticos emerald/rose/amber/sky/slate,
siempre acompañados por texto o símbolos. Encabezados con Barlow Semi Condensed,
texto con Source Sans 3, cifras tabulares. Fuentes alojadas en la aplicación.
El mapa de camas funciona como tablero operativo; las fichas usan secciones clínicas,
la cola usa filas y el portal usa una lectura más pausada. No se repite el mismo
mosaico de tarjetas en todas las pantallas.

## Estructura entregada

```text
frontend/
├── index.html
├── public/logo.svg
├── logo.svg
├── src/
│   ├── main.tsx, App.tsx, tailwind.css
│   ├── api/            cliente, contratos y módulos por recurso
│   ├── components/     navegación, estados, métricas, formularios y diálogos
│   ├── features/
│   │   ├── auth/       sesión, menús únicos y guardas
│   │   ├── capacity/   resumen y disponibilidad
│   │   ├── queue/      cola, solicitud y asignación
│   │   ├── beds/       gestión y movimientos
│   │   ├── patients/   búsqueda, ficha, edición y atenciones
│   │   ├── imaging/    carga y visor
│   │   ├── analytics/  seis paneles
│   │   ├── accounts/   cuentas y auditoría
│   │   ├── organizations/ red EPS/IPS, historial y restauración
│   │   └── portal/     información propia
│   └── test/           configuración, proveedores y datos sintéticos de pruebas
├── package.json, package-lock.json
├── vite.config.ts, tsconfig.json, eslint.config.js
├── tailwind.config.js, postcss.config.js
├── Dockerfile, nginx.conf, .dockerignore
└── PARIDAD.md
```

Los contenedores consultan y mutan mediante TanStack Query. Los componentes de
presentación reciben datos y acciones por propiedades; ejemplos: `CapacityView`,
`QueueTable`, `BedGrid`, `PatientResults`, `PatientRecordView`, `ClusterResults`,
`AccountsTable` y `ImageCanvas`. Las pruebas están junto a cada funcionalidad.
Las pantallas se cargan por ruta. Las claves de consulta separan los recursos y
las mutaciones invalidan los datos afectados. No se implementaron funcionalidades
futuras ni conexiones de eventos.

## Diseño y adaptación de pantalla

- ✅ Modo claro, logo y favicon originales, sin pie de página institucional.
- ✅ PrimeReact 10.9.9 en modo unstyled, preset Tailwind y Tailwind 3.4.19;
  se escanea también `node_modules/primereact`. React 18.3.1, Vite 7.3.6,
  React Router 7.18.4 y TanStack Query 5.104.0 quedan fijados en el lockfile.
- ✅ Tipografías locales, sin solicitudes a CDNs. Iconos de PrimeIcons.
- ✅ Estados de cama y prioridades con texto/símbolos además del color.
  Texto oscuro sobre fondos claros y botones primarios con texto blanco;
  se evita usar el teal brillante como texto pequeño sobre blanco.
- ✅ Menú lateral móvil, cabecera que permite saltos de línea, contenedores
  `min-w-0`, tablas con desplazamiento interno y diálogos limitados al viewport.
- ✅ Enlace de salto, etiquetas de campos, foco visible, errores con alerta,
  tablas con encabezados, diálogos PrimeReact con Escape y retorno del foco.
- ✅ `styles.css` eliminado. `src/tailwind.css` contiene solo las tres directivas
  de Tailwind; no hay selectores CSS escritos a mano.

## Verificaciones ejecutadas

| Comprobación | Resultado |
|---|---|
| `npm install` | Dependencias y lockfile generados |
| `npm ci --no-fund --no-audit` | Instalación limpia correcta, 346 paquetes |
| `npx prettier --write src package.json tsconfig.json vite.config.ts tailwind.config.js postcss.config.js eslint.config.js index.html` | Código formateado |
| `npm run lint` | ✅ Sin errores ni advertencias |
| `npm test` | ✅ 45 pruebas en seis archivos |
| `npm run build` | ✅ TypeScript estricto y Vite, 186 módulos; recursos con hash y pantallas separadas |
| `npm run dev -- --host 127.0.0.1` | Revisión visual con respuestas sintéticas temporales; servidor detenido al terminar |
| `npm run preview -- --host 127.0.0.1 --port 4173` | Login compilado correcto, fuentes locales, sin errores de consola |
| `git diff --check` | ✅ Sin errores de espacios |
| Comparación estática de nginx y archivos protegidos | ✅ Proxy y resolver idénticos; backend, migraciones, scripts, pruebas Python y capturas sin cambios |

`npm ci` emitió avisos de deprecación de ESLint 9 y `whatwg-encoding`, y avisos
de npm 11 sobre scripts de instalación de esbuild/fsevents. La instalación, el
lint, las pruebas y la compilación finalizaron con código 0. No se ejecutó una
auditoría de dependencias como parte de esta verificación.

Cobertura funcional automatizada:

- Mensajes exactos 401/423 y color ámbar del bloqueo; limpiar la clave al fallar.
- Cuatro menús y guardas de ruta para anónimo y los cuatro roles.
- Restaurar sesión de pestaña; cerrar sesión y purgar datos privados del caché;
  expirar ante un 401 tanto JSON como binario, con mensaje de sesión terminada.
- Mínimos de búsqueda, documentos alfanuméricos, no consultar sin criterio,
  máximo diez resultados y mensaje con total; documento de solo lectura.
- Los nueve rangos LOINC, límites inclusive, valores no finitos, coma decimal,
  sistólica mayor que diastólica y fechas de nacimiento sin desplazamiento
  por zona horaria.
- Motivo de excepción obligatorio y no vacío; conflicto real simulado 409;
  payload con `override_priority` y motivo; refrescar la cola antes de abrir
  la solicitud anterior descubierta por el servidor.
- Reutilizar la atención abierta o crearla antes de solicitar cama.
- Ausencia de «Ocupada» como opción; motivo obligatorio; liberar solo desde
  coordinación/administración; un fallo de una IPS no se transforma en ceros.
- Carga de imagen como cuerpo binario, Authorization en la vista previa y
  revocación de la URL del blob al desmontar.

Revisión en navegador con datos sintéticos (no contra el backend):

- Las seis vistas del personal a **360, 768, 1024 y 1440 px**: en todas,
  `document.documentElement.scrollWidth === clientWidth`. La diferencia de
  15 px respecto al viewport cuando aparece corresponde a la barra vertical.
- Login a 360 px y compilación final a 1280 px; portal a 360, 768 y 1440 px.
- Diálogo de prioridad y sus ocho alternativas, motivo obligatorio, ficha y
  formulario de atención a 360 px; foco inicial en el botón de cerrar.
- Visor con dos imágenes, selección, inversión, zoom hasta 400 % y restablecer;
  el zoom desplaza solo la imagen dentro del visor, sin ampliar la página.
- Cabecera «Conectado» y nombres largos de cuenta se mantienen dentro de su
  contenedor. El portal no ofrece carga ni edición de registros.

Las respuestas visuales temporales fueron eliminadas; no hay un acceso alterno,
cuentas de demostración integradas ni interceptores de red en la aplicación
entregada. Los datos de prueba permanentes solo se importan desde los tests.

## Decisiones y diferencias justificadas

1. **Análisis tiene seis paneles.** La interfaz anterior tenía cinco contando
   agrupamiento. Para cumplir «cinco paneles más clustering» se añadió
   «Ingresos y altas» usando el endpoint existente `GET analytics/occupancy-timeline`
   (14 días). No se cambió el backend.
2. **Búsqueda explícita opcional.** Se conserva la detección automática y se
   permite elegir documento exacto para documentos alfanuméricos; se mantienen
   los mínimos y el límite de resultados.
3. **Errores visibles.** Fallos de estudios, movimientos o auditoría tienen
   reintento. Un fallo de red no genera totales vacíos ni ceros ficticios.
4. **Estilos inline acotados.** El visor pasa cuatro variables CSS numéricas para
   controles continuos, consumidas por utilidades arbitrarias de Tailwind.
   PrimeReact también calcula estilos inline de overlays y transiciones.
   Por ello CSP permite `style-src 'self' 'unsafe-inline'`; scripts y conexiones
   permanecen en `self`. No hay estilos externos ni reglas CSS personalizadas.
5. **CSP del frontend y documentación.** La política se aplica a los recursos
   estáticos y las rutas SPA. Las rutas del proxy conservan la política de la
   API para no romper Swagger/ReDoc. `X-Frame-Options: DENY` se envía en todas.
6. **Límite de carga nginx.** Se fija `client_max_body_size 15m` para que el
   frontend admita los mismos 15 MB del endpoint de imágenes, conservando
   intacto el bloque de proxy.
7. **Documentación.** Solo se cambió la sección «La interfaz web» del README.
   La guía `../GUIA-OPERACIONES.md` cambia las instrucciones de pruebas/lint y
   la ruta del formulario de atención. Las demás secciones se conservaron.
   Las capturas de `docs/capturas/` están desactualizadas y no se regeneraron.
8. **Límites existentes conservados.** Cuentas muestra las primeras 100,
   auditoría 20, búsqueda 10 y ficha 100 observaciones/estudios. El alta de
   atención y sus mediciones siguen siendo peticiones separadas: un fallo
   parcial se informa y no revierte una atención ya guardada. No se añadió
   una transacción que requiriese cambiar la API.

## Pendiente para el servidor

- ❌ **Integración real con API/PACS y datos persistidos:** no ejecutada en este
  equipo por la restricción de trabajo local. Contratos contrastados con
  rutas y esquemas; pruebas locales con respuestas simuladas.
- ❌ **Construcción y arranque del contenedor nginx/Node 22:** no ejecutados.
  Dockerfile multietapa preparado, mismo contexto de compose; configuración
  validada estáticamente. La compilación local usó Node 26.10 / npm 11.19.
- ❌ **Prueba integral de accesibilidad con lector de pantalla:** pendiente;
  se revisaron semántica, navegación/foco y adaptación visual, sin certificar
  conformidad completa WCAG.

Al desplegar en el ASUS, recorrer la guía con las cuentas sintéticas:

1. Ingresar con cada rol, recargar una ruta interna y comprobar restauración;
   probar credenciales erróneas/bloqueo en una cuenta desechable y desbloquearla.
2. Crear/buscar/editar un paciente; registrar atención y comprobar LOINC, unidades
   y fechas en la ficha. Crear una solicitud reutilizando esa atención.
3. Asignar siguiendo prioridad y repetir con excepción justificada; contrastar
   cola, ocupación y auditoría. Liberar hacia limpieza y luego disponibilidad.
4. Cargar DICOM/PNG/JPEG en un estudio registrado; comprobar acceso autorizado,
   listado de instancias, visor y el mismo estudio desde el portal del paciente.
5. Cambiar agrupamiento Automático/2–8 y contrastar totales con los datos de la
   API. Provocar un fallo de conexión para verificar «Reintentar», sin ceros.
6. Verificar cabeceras CSP/X-Frame-Options, caché de HTML y `/assets/`, las rutas
   directas de BrowserRouter y todos los destinos del proxy. Probar una carga
   válida cercana al límite para confirmar el tamaño permitido por nginx.

No se ejecutaron Docker, compose, seed, API ni pruebas Python. No se crearon
commits ni se hizo push.

## Interacciones y movimiento — 29 de septiembre de 2026

Antes de editar esta capa se ejecutaron `npm run lint`, `npm test` y
`npm run build`: los tres finalizaron correctamente, con 45 pruebas aprobadas.

| Componente / archivo | Cambio de presentación |
|---|---|
| `tailwind.config.js` | Teal de marca, acento, paletas semánticas, radios, sombras y movimiento centralizados: 120/200/320 ms, conteo 400 ms y easing `cubic-bezier(0.2, 0.8, 0.2, 1)`. Preferencia de movimiento reducido aplicada globalmente. |
| `src/components/ui.tsx` | Botones con cambio de relleno, sombra, escala 1.02/0.98, foco visible y carga sin cambiar el ancho; diálogos con entrada/salida manual, desenfoque de fondo; esqueletos; notificaciones arriba a la derecha con barra de autodismiss a siete segundos. |
| `src/components/motion.tsx` | Hooks de movimiento reducido, conteo cancelable y detección de cambios; entrada escalonada limitada a ocho posiciones; transiciones compartidas. |
| `src/components/MetricCard.tsx` | Elevación, sombra y acento semántico; conteo desde el valor anterior, sin animar al montar ni si el valor no cambió. El valor final queda disponible para lectores de pantalla. |
| `src/components/AppLayout.tsx` | Interacciones coherentes en navegación, cierre de sesión y cierre del menú móvil. |
| `src/components/{StatusBadge,PriorityBadge}.tsx` | Uso de tokens semánticos; transición de color del estado de cama, conservando texto y símbolos. |
| `src/features/capacity/{CapacityPage,BedMark}.tsx` | Marcas con tooltip de código/estado al pasar el puntero o enfocar; escala y transición de color; entrada de métricas, instituciones y prioridades. |
| `src/features/beds/BedsPage.tsx` | Tarjetas interactivas, carga con esqueletos y resaltado cuando cambia el estado/dato mostrado. |
| `src/features/queue/AssignDialog.tsx` | Alternativas de cama con entrada escalonada, respuesta al puntero y foco dentro de la opción. |
| `src/features/patients/PatientRecordView.tsx` | Entrada y resaltado de las filas de atenciones; las tablas usan la transición compartida. |
| `src/features/imaging/ImageViewer.tsx` | Espera de decodificación antes de revelar la imagen; transición de opacidad, brillo, contraste, inversión y ancho; marcador desenfocado; URLs de blobs liberadas al reemplazar o cerrar. |
| `src/features/auth/{LoginPage,NetworkIllustration}.tsx` | Composición dividida con esquema SVG de salas/camas conectadas. La ilustración se anima durante dos ciclos breves y se detiene. |
| `src/components/motion.test.tsx`, `src/features/imaging/imaging.test.tsx`, `src/test/{render.tsx,setup.ts}` | Pruebas de conteo, cancelación, movimiento reducido, ancho de carga, identidad de filas, cierre accesible y decodificación/liberación de imágenes. El proveedor de pruebas se conserva al hacer rerender. |

Verificación final:

- `npm run lint`: correcto, sin advertencias.
- `npm test`: **51 pruebas aprobadas en siete archivos**.
- `npm run build`: correcto, TypeScript y Vite, 189 módulos.
- `git diff --check`: correcto.
- Navegador: ancho del botón idéntico antes/durante la carga; transición de
  200 ms; fila actualizada con resaltado; diálogo de 328 px dentro de viewport
  de 360 px; fondo desenfocado; retorno del foco al control de apertura;
  notificación con barra animada y sin desplazamiento horizontal.
- Login revisado en móvil y escritorio. La revisión de interacciones utilizó
  componentes reales con datos locales temporales, eliminados al terminar.

Al activar movimiento reducido, conteos, escalas, transiciones, esqueletos,
barra de progreso y animación de la ilustración se vuelven estados estáticos.
Los cambios de datos y los controles permanecen operativos; el cierre de
notificaciones conserva su tiempo original.

Decisiones y límites:

- El marcador de carga del visor es un SVG neutro desenfocado de baja resolución.
  No es una miniatura clínica: la API solo entrega la vista previa completa.
  No se añadió otra petición ni se muestra la imagen de otro estudio durante
  la espera.
- Se animan los cierres manuales de diálogos (aspa, Escape, Cancelar, Cerrar y
  Volver). Los desmontajes provocados por navegación o por completar una
  operación mantienen el cierre inmediato del flujo existente.
- La barra de la notificación se restablece al pasar el puntero, acompañando
  la pausa/reinicio del temporizador de PrimeReact. En movimiento reducido
  permanece estática.
- No cambian endpoints, reglas de validación, permisos ni cálculos clínicos.
  No hay dependencias nuevas. No se ejecutaron Docker, API ni seed; no se
  generó ningún commit. Las capturas de `docs/capturas/` siguen intactas.


## Red de atención — 30 de septiembre de 2026

Ruta `/organizations` y menú «Red de atención» exclusivos de administración.
La guarda reutiliza el mapa único de permisos; las altas envían exactamente
`{code, name}` a los endpoints dedicados. El formulario de IPS se abre dentro
de una EPS y muestra ese vínculo como texto. La edición envía solo los campos
modificados; no permite cambiar tipo ni padre.

Se reutilizan botones, diálogos, esqueletos, estados vacíos, reintentos y
transiciones. Las notificaciones compartidas admiten ahora una acción asíncrona
con carga y errores. «Deshacer» verifica que continúe la misma sesión antes de
restaurar. Cada mutación invalida organizaciones, catálogos EPS, capacidad,
análisis, cola, auditoría y cuentas, sin recargar la página.

Límites explícitos:

- El listado muestra las primeras 100 EPS y avisa si hay más; las IPS se
  consultan por EPS y su conteo nunca se sustituye por cero ante un error.
- «Eliminadas recientemente» vive en memoria del caché, por usuario. Se conserva
  al navegar dentro de la aplicación y se limpia al cerrar sesión o recargar.
  No es una papelera completa: el backend no lista organizaciones eliminadas.
- El historial muestra fecha con año, hora, versión, datos anteriores, campos
  modificados y el identificador del autor; el contrato no incluye su nombre.
- El servidor rechaza eliminar una EPS con IPS activas. Si se eliminan ambos
  niveles, conviene restaurar primero la EPS y después sus IPS.

Verificación local: `npm run lint`, `npm test` y `npm run build` aprobados;
**70 pruebas en ocho archivos**, incluidas altas EPS/IPS y sus payloads exactos,
errores 409/422, límites, edición parcial, permisos, historial, conteos, reintento,
eliminación rechazada, restauración desde notificación/lista y cambio de sesión.
Las pruebas usan respuestas simuladas. No se ejecutaron API, Docker ni seed,
ni se modificó el backend para esta pantalla. No se generaron commits.

Guion manual para el servidor:

1. Ingresar como ADMIN y abrir «Red de atención». Crear una EPS con un código
   nuevo (por ejemplo `EPS-DEMO-RED`) y nombre «EPS de demostración». Comprobar
   que aparece sin solicitar organización padre.
2. Expandir esa EPS y pulsar «Nueva IPS». Crear `IPS-DEMO-RED`, «Clínica de
   demostración». Comprobar el vínculo y que el conteo pasa a una IPS.
3. Repetir el código de la EPS en «Nueva EPS» y el de la IPS en «Nueva IPS».
   Comprobar el aviso de código duplicado dentro del formulario, sin perder
   los valores escritos ni cerrar la ventana.
4. Editar nombre y código de la IPS. Confirmar el cambio en la tarjeta y abrir
   «Historial»: debe mostrar la versión anterior con fecha y autor. Repetir
   con la EPS. El tipo y el padre no deben ser editables.
5. Intentar eliminar la EPS con su IPS activa: debe permanecer y mostrar el
   rechazo del servidor en la confirmación.
6. Eliminar la IPS, leer la explicación de borrado lógico y confirmar. Debe
   desaparecer de la EPS y aparecer en «Eliminadas recientemente». Pulsar
   «Deshacer» en la notificación: debe volver a su EPS y actualizar el conteo.
7. Eliminar otra vez la IPS y restaurarla desde la lista temporal. Para probar
   ambos niveles, eliminar la IPS y luego la EPS; restaurar primero la EPS y
   luego la IPS. Revisar el historial después de restaurar.
8. Cambiar a coordinación EPS, operación IPS o paciente: el menú no debe
   ofrecer esta pantalla y abrir `/organizations` debe redirigir a la primera
   vista permitida. Volver a ADMIN y comprobar que no heredó la lista temporal.

Pendiente: ejecutar este guion contra la API del servidor y revisar la pantalla
con datos reales. Las capturas de `docs/capturas/` siguen sin regenerarse.
