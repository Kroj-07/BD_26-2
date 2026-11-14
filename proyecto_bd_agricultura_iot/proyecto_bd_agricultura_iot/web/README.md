# Aplicación web (MVP)

Aplicación en Flask conectada a la base `agro1m` de PostgreSQL. Permite:

- Ejecutar las **5 consultas del experimento** (se leen de `sql/03_consultas.sql`),
  ver sus resultados, el tiempo de respuesta y el plan de ejecución.
- Revisar el **estado hídrico** de cada zona (vista `vw_estado_hidrico_zona`).
- Atender **alertas pendientes** (vista `vw_alertas_pendientes`, actualiza `Alerta`).
- **Registrar lecturas** con el procedimiento `sp_registrar_lectura`; si la humedad
  queda bajo el mínimo del cultivo, el trigger `trg_alerta_lectura` crea la alerta.

## Cómo ejecutarla (Windows)

1. Instalar las dependencias (una sola vez), desde esta carpeta:

   ```
   pip install -r requirements.txt
   ```

2. Indicar la contraseña de PostgreSQL y arrancar:

   ```
   set PGUSER=postgres
   set PGPASSWORD=tu_clave
   python app.py
   ```

   En PowerShell: `$env:PGUSER="postgres"; $env:PGPASSWORD="tu_clave"; python app.py`

3. Abrir <http://localhost:5000> en el navegador. Para detenerla: `Ctrl + C`.

Por defecto usa la base `agro1m`. Para otra base: `set PGDATABASE=agro100k`.
El servidor y el puerto se cambian con `PGHOST`, `PGPORT` y `PUERTO`.
