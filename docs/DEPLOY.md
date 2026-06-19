# Despliegue en la nube

## TL;DR de arquitectura

```
Vercel            →  Frontend (UI; hoy se sirve desde el back, luego Next.js)
Railway/Render/Fly →  Backend FastAPI + motor (DuckDB en volumen) + sync ClickHouse
Supabase           →  Auth (login finanzas) · Postgres (cobros/config/resultados) · Storage (Excel + PDFs)
ClickHouse Cloud   →  Fuente de verdad del sistema (ya existe)
```

**El backend NO va en Supabase.** Supabase = Postgres + Auth + Storage + Edge Functions (Deno, máx ~2 min / 256 MB). El motor parsea Excel de millones de filas, sincroniza ClickHouse y reconcilia — eso necesita un **contenedor siempre-on**.

## Dónde montar el backend (recomendación)

| Opción | Por qué | Volumen DuckDB |
|---|---|---|
| **Railway** (recomendado para empezar) | Deploy desde GitHub en minutos, soporta volúmenes | Sí |
| **Render** | Similar, free tier | Sí (disk) |
| **Fly.io** | Más control, global | Sí (volumes) |
| **Google Cloud Run** | Escala a cero, pero sin disco persistente → habría que migrar DuckDB a Postgres | No (efímero) |

## Fase 1 — Online rápido (mínimo cambio)
1. Backend a **Railway** con este `Dockerfile` + un **volumen montado en `/app/data`** (persiste el DuckDB).
2. Variables de entorno en Railway: `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD`, `CH_DATABASE`, `CH_SECURE`.
3. La UI se sirve desde el mismo back (`/`). Proteger con **Supabase Auth** o basic-auth para finanzas.

## Fase 2 — Producción
- **Supabase Postgres**: mover `cobros`, `config_credito`, `tarifas`, `periodos`, resultados de conciliación (para multiusuario + monitoreo).
- **Supabase Storage**: archivos de paquetería subidos + PDFs de facturas.
- **Supabase Auth**: login del equipo de finanzas (+ Row Level Security).
- **Vercel**: frontend como app Next.js (reusa el design system Nexus), llamando al back de Railway.
- El motor (reconcile) sigue en el contenedor, leyendo ClickHouse y escribiendo resultados a Postgres.

## Build/run local con Docker
```bash
docker build -t conciliacion .
docker run -p 8770:8770 --env-file .env.local -v $(pwd)/data:/app/data conciliacion
```
