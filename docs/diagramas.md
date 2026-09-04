# Diagramas

Los diagramas están escritos en Mermaid, que GitHub renderiza directamente: no
hace falta ninguna herramienta ni mantener imágenes sincronizadas con el código.

---

## 1. Arquitectura

```mermaid
flowchart TB
    subgraph externo["Acceso público"]
        nav["Navegador / Postman<br/>Swagger en /docs"]
    end

    subgraph tuneles["Cloudflare Tunnel"]
        tapi["tunnel-api"]
        tfhir["tunnel-fhir"]
    end

    subgraph docker["Docker Compose"]
        api["API FastAPI<br/>autenticación · RBAC · soft ops"]
        integ["Servicio de integración<br/>relacional → FHIR R4"]
        hapi["HAPI FHIR R4"]
        hapidb[("PostgreSQL<br/>de HAPI")]
    end

    neon[("PostgreSQL gestionado · Neon<br/>fuente de verdad operativa")]

    nav --> tapi & tfhir
    tapi --> api
    tfhir --> hapi
    api --> neon
    api --> integ
    integ -->|"PUT condicional<br/>por identifier"| hapi
    hapi --> hapidb

    classDef db fill:#e8f0fe,stroke:#4285f4,color:#111
    classDef svc fill:#e6f4ea,stroke:#34a853,color:#111
    class neon,hapidb db
    class api,integ,hapi svc
```

El servidor FHIR conserva su propia base de datos, separada de la de la
aplicación. Son dos sistemas con ciclos de vida distintos: la aplicación es dueña
del modelo operativo; HAPI es dueño de la representación interoperable y de su
esquema interno, que no controlamos. Compartir una sola base acoplaría nuestras
migraciones a decisiones de un producto de terceros.

---

## 2. Modelo entidad-relación

```mermaid
erDiagram
    ORGANIZATIONS ||--o{ ORGANIZATIONS : "parent_organization_id (EPS → IPS)"
    ORGANIZATIONS ||--o{ LOCATIONS : opera
    ORGANIZATIONS ||--o{ USERS : emplea
    ORGANIZATIONS ||--o{ PATIENTS : afilia
    ORGANIZATIONS ||--o{ ENCOUNTERS : atiende

    LOCATIONS ||--o{ LOCATIONS : "parent_location_id (sede→servicio→hab→cama)"
    LOCATIONS ||--o{ BED_STATUS_EVENTS : registra
    LOCATIONS ||--o{ BED_ASSIGNMENTS : recibe
    LOCATIONS ||--o{ ENCOUNTERS : aloja

    ROLES ||--o{ USERS : define
    USERS ||--o| PATIENTS : "patient_id (cuenta del paciente)"

    PATIENTS ||--o{ ENCOUNTERS : tiene
    PATIENTS ||--o{ OBSERVATIONS : tiene

    ENCOUNTERS ||--o{ OBSERVATIONS : contiene
    ENCOUNTERS ||--o{ BED_REQUESTS : origina

    BED_REQUESTS ||--o{ BED_ASSIGNMENTS : resuelve

    ORGANIZATIONS {
        uuid id PK
        string code UK
        string name
        enum organization_type "EPS o IPS"
        uuid parent_organization_id FK
        timestamp deleted_at "NULL = viva"
    }
    LOCATIONS {
        uuid id PK
        uuid organization_id FK
        uuid parent_location_id FK
        enum location_type "FACILITY, WARD, ROOM, BED"
        string code
        enum status "estado de la cama"
    }
    ROLES {
        uuid id PK
        enum code UK "ADMIN, EPS_COORDINATOR, IPS_CLINICAL_OPERATOR, PATIENT"
    }
    USERS {
        uuid id PK
        string username UK
        string password_hash "bcrypt"
        uuid role_id FK
        uuid organization_id FK "ámbito institucional"
        uuid patient_id FK "solo rol PATIENT"
        bool is_active "cuenta habilitada"
    }
    PATIENTS {
        uuid id PK
        enum document_type
        string document_number
        date birth_date
        enum gender "value set FHIR"
        uuid created_by FK "habilita la regla de pertenencia"
        timestamp deleted_at
    }
    ENCOUNTERS {
        uuid id PK
        uuid patient_id FK
        uuid organization_id FK
        uuid location_id FK "cama ocupada"
        enum encounter_class "IMP, AMB, EMER"
        enum status "código FHIR literal"
        timestamp started_at
    }
    OBSERVATIONS {
        uuid id PK
        uuid patient_id FK
        uuid encounter_id FK
        string code "LOINC"
        numeric value_numeric
        string unit "UCUM"
    }
    BED_REQUESTS {
        uuid id PK
        uuid encounter_id FK
        uuid requesting_organization_id FK
        uuid target_organization_id FK
        enum status "PENDING, ASSIGNED, y otros"
    }
    BED_ASSIGNMENTS {
        uuid id PK
        uuid bed_request_id FK
        uuid location_id FK "única activa por cama"
        timestamp released_at
    }
    BED_STATUS_EVENTS {
        uuid id PK
        uuid location_id FK
        enum previous_status
        enum new_status
        timestamp event_at
    }
    RECORD_VERSIONS {
        uuid id PK
        string entity_type
        uuid entity_id
        int version_number
        jsonb snapshot_json "estado anterior"
    }
    AUDIT_LOG {
        uuid id PK
        uuid user_id FK
        enum action "SOFT_EDIT, SOFT_DELETE, RESTORE, y otros"
        string entity_type
        uuid entity_id
    }
    FHIR_SYNC_LOG {
        uuid id PK
        string entity_type UK
        uuid entity_id UK
        string fhir_resource_id "id en el servidor FHIR"
        enum sync_status
    }
```

`RECORD_VERSIONS`, `AUDIT_LOG` y `FHIR_SYNC_LOG` no tienen llaves foráneas hacia
las entidades que describen: son tablas genéricas que sirven a cualquier entidad
mediante el par `(entity_type, entity_id)`. Por eso aparecen sueltas en el
diagrama. Agregar una entidad auditable nuevo no cuesta ninguna tabla adicional.

Dos jerarquías son autorreferenciadas —`ORGANIZATIONS` y `LOCATIONS`—, y eso es
lo que permite que **agregar una IPS o una cama sea una inserción de datos y no
una migración de esquema**.

---

## 3. Sincronización idempotente hacia FHIR

Este es el mecanismo que hace que ejecutar la sincronización dos veces no
duplique recursos.

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant API as API
    participant Log as fhir_sync_log
    participant HAPI as HAPI FHIR R4

    Admin->>API: POST /integration/fhir/observations/{id}

    Note over API: Las dependencias van primero:<br/>una referencia solo puede apuntar a<br/>un recurso que ya existe en el servidor.

    API->>HAPI: PUT /Organization?identifier=...|IPS-NORTE
    HAPI-->>API: 201 Created · id=4
    API->>Log: Organization → 4

    API->>HAPI: PUT /Patient?identifier=...|CC-1032456789
    HAPI-->>API: 201 Created · id=7
    API->>Log: Patient → 7

    API->>HAPI: PUT /Encounter?identifier=...|{uuid}
    HAPI-->>API: 201 Created · id=8
    API->>Log: Encounter → 8

    API->>HAPI: PUT /Observation?identifier=...|{uuid}<br/>subject: Patient/7 · encounter: Encounter/8
    HAPI-->>API: 201 Created · id=9
    API->>Log: Observation → 9
    API-->>Admin: 200 · SYNCED · id=9

    rect rgb(240, 248, 255)
        Note over Admin,HAPI: Segunda ejecución, mismos datos
        Admin->>API: POST /integration/fhir/observations/{id}
        API->>HAPI: PUT /Observation?identifier=...|{uuid}
        Note right of HAPI: El identificador ya existe:<br/>ACTUALIZA en vez de crear.
        HAPI-->>API: 200 OK · id=9 (el mismo)
        API-->>Admin: 200 · SYNCED · id=9 · attempt_count=2
    end
```

La clave está en el verbo: en lugar de `POST`, se usa una **actualización
condicional** `PUT [servidor]/{Recurso}?identifier={sistema}|{valor}`, que FHIR
resuelve como *"crea si no existe, actualiza si ya existe"*. El identificador es
el dato de negocio —el documento de identidad del paciente, por ejemplo—, porque
es el valor con el que el servidor puede reconocer que se trata de la misma
entidad.

Como segunda garantía, `fhir_sync_log` tiene una restricción única sobre
`(entity_type, entity_id)`: una fila local solo puede corresponder a un recurso
FHIR.

Beneficio adicional: el enunciado cuenta `PUT` como un extra opcional. Esta
solución lo implementa por diseño.

---

## 4. Flujo de una petición y control de acceso

```mermaid
flowchart LR
    req(["Petición<br/>con token Bearer"]) --> dep

    subgraph capa1["Dependencia de autenticación"]
        dep{"¿Token válido<br/>y cuenta activa?"}
    end

    dep -->|no| e401(["401"])
    dep -->|sí| rol

    subgraph capa2["Autorización · tres condiciones"]
        rol{"1 · ¿El ROL<br/>permite la operación?"}
        org{"2 · ¿El registro es de<br/>SU INSTITUCIÓN?"}
        own{"3 · ¿ÉL CREÓ<br/>el registro?"}
    end

    rol -->|no| e403(["403"])
    rol -->|sí| org
    org -->|no| e403
    org -->|sí| own
    own -->|no| e403
    own -->|sí| svc

    subgraph capa3["Servicio"]
        svc["Reglas del dominio"]
        ver["Versiona el estado previo<br/>y audita"]
        db[("Base de datos")]
        svc --> ver --> db
    end

    db --> ok(["200 / 201"])

    classDef err fill:#fce8e6,stroke:#d93025,color:#111
    class e401,e403 err
```

El usuario se relee de la base de datos en cada petición: **el token prueba
identidad, nunca permisos**. Así, deshabilitar una cuenta surte efecto de
inmediato en lugar de esperar a que el token expire.

En los listados, la condición institucional no se aplica descartando resultados
después de consultarlos: **se inyecta dentro de la consulta SQL**. Los registros
de otra institución no se ocultan, simplemente nunca se consultan.
