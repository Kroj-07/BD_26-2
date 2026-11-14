#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Costo de escritura indice por indice (complemento de la fase 3 de experimento.py).

Mide inserciones y actualizaciones sobre una copia de la base de un millon de
registros en 11 configuraciones: sin los 9 indices de 04_indices.sql, cada
indice por separado y los 9 juntos. Las claves primarias y UNIQUE se mantienen.

Metodologia identica a la fase 3: 5 iteraciones por operacion; en cada una
VACUUM ANALYZE de la tabla, BEGIN, triggers de usuario deshabilitados,
EXPLAIN (ANALYZE) de la operacion y ROLLBACK. VACUUM (FULL, ANALYZE) al cambiar
de configuracion.

Uso (la conexion se toma de PGHOST, PGPORT, PGUSER y PGPASSWORD):
  python escritura_indices.py --base agro1m_escritura --plataforma "texto"
Salidas: resultados/escritura_indices.json y resultados/escritura_indices.csv
"""
import argparse, csv, json, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experimento import IT, OPERACIONES, PRENDER, RES, conectar, leer_indices  # noqa: E402

# Operaciones de la fase 3 (Lectura_Suelo y Evento_Riego) y las equivalentes para
# las demas tablas indexadas: 5 000 filas, como la insercion de eventos de riego.
OPS = [(tabla, nombre, sql) for nombre, tabla, sql in OPERACIONES] + [
    ("Evento_Riego", "Actualizacion de duracion (5 000 eventos)", """
UPDATE Evento_Riego SET duracion_min = CASE WHEN duracion_min < 720 THEN duracion_min + 1
                                            ELSE duracion_min - 1 END
WHERE id_evento <= 5000"""),
    ("Cosecha", "Insercion de 5 000 cosechas", """
INSERT INTO Cosecha (id_campania, id_operario, fecha, cantidad_kg, categoria,
                     humedad_producto_pct, precio_kg_soles)
SELECT c.ids[1 + (g % array_length(c.ids, 1))], NULL, DATE '2026-09-16' + (g % 90),
       round((500 + random() * 4500)::numeric, 2),
       (ARRAY['Primera','Segunda','Tercera','Descarte'])[1 + (g % 4)], NULL, 2.50
FROM generate_series(1, 5000) g,
     (SELECT array_agg(id_campania ORDER BY id_campania) AS ids FROM Campania) c"""),
    ("Cosecha", "Actualizacion de cantidad (5 000 cosechas)", """
UPDATE Cosecha SET cantidad_kg = cantidad_kg + 1 WHERE id_cosecha <= 5000"""),
    ("Campania", "Insercion de 5 000 campanias", """
INSERT INTO Campania (id_parcela, id_cultivo, fecha_siembra, fecha_fin_estimada,
                      densidad_plantas_ha, estado)
SELECT p.ids[1 + (g % array_length(p.ids, 1))], 1 + (g % 12),
       DATE '2027-01-01' + g, DATE '2027-01-01' + g + 150, NULL,
       CASE WHEN g % 2 = 0 THEN 'En curso' ELSE 'Planificada' END
FROM generate_series(1, 5000) g,
     (SELECT array_agg(id_parcela ORDER BY id_parcela) AS ids FROM Parcela) p"""),
    ("Campania", "Actualizacion de estado (todas las campanias)", """
UPDATE Campania SET estado = CASE WHEN estado = 'En curso' THEN 'Cerrada' ELSE 'En curso' END"""),
    ("Dispositivo", "Insercion de 5 000 dispositivos", """
INSERT INTO Dispositivo (codigo_serie, modelo, fabricante, fecha_instalacion, estado,
                         id_parcela, num_zona)
SELECT 'PRB-' || lpad(g::text, 6, '0'), 'Prueba', NULL, DATE '2026-09-16', 'Activo',
       z.ps[1 + (g % z.n)], z.zs[1 + (g % z.n)]
FROM generate_series(1, 5000) g,
     (SELECT array_agg(id_parcela ORDER BY id_parcela, num_zona) AS ps,
             array_agg(num_zona  ORDER BY id_parcela, num_zona) AS zs,
             count(*)::int AS n
        FROM Zona_Manejo) z"""),
    ("Dispositivo", "Actualizacion de estado (todos los dispositivos)", """
UPDATE Dispositivo SET estado = CASE WHEN estado = 'Activo' THEN 'Inactivo' ELSE 'Activo' END"""),
    ("Alerta", "Insercion de 5 000 alertas de humedad baja", """
INSERT INTO Alerta (id_dispositivo, fecha_hora, tipo, severidad, valor_registrado)
SELECT id_dispositivo, fecha_hora, 'Humedad baja',
       CASE WHEN humedad_pct < 20 THEN 'Critica' WHEN humedad_pct < 30 THEN 'Alta'
            ELSE 'Media' END, humedad_pct
FROM _prueba_alerta"""),
    ("Alerta", "Actualizacion de atencion (5 000 alertas)", """
UPDATE Alerta SET atendida = NOT atendida,
                  fecha_atencion = CASE WHEN atendida THEN NULL
                                        ELSE fecha_hora + INTERVAL '1 hour' END
WHERE id_alerta <= %(tope_alerta)s AND tipo = 'Humedad baja'"""),
]

# Lecturas sin alerta de humedad baja: la insercion de alertas lee de esta tabla
# auxiliar para no medir la busqueda de candidatas junto con la escritura.
PREPARAR = """
DROP TABLE IF EXISTS _prueba_alerta;
CREATE TABLE _prueba_alerta AS
SELECT l.id_dispositivo, l.fecha_hora, l.humedad_pct
FROM Lectura_Suelo l
WHERE l.humedad_pct IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM Alerta a WHERE a.id_dispositivo = l.id_dispositivo
                    AND a.fecha_hora = l.fecha_hora AND a.tipo = 'Humedad baja')
ORDER BY l.id_dispositivo, l.fecha_hora
LIMIT 5000;
"""


def sentencias_indices(texto):
    """Nombre -> (tabla, CREATE INDEX) para cada indice de 04_indices.sql."""
    out = {}
    for m in re.finditer(r"(CREATE INDEX IF NOT EXISTS (\w+)\s+ON (\w+).*?;)", texto, re.S):
        out[m.group(2)] = (m.group(3), m.group(1))
    return out


def medir_operacion(cur, tabla, sql):
    t, filas = [], None
    for _ in range(IT):
        cur.execute("VACUUM ANALYZE %s;" % tabla)
        cur.execute("BEGIN;")
        cur.execute("ALTER TABLE %s DISABLE TRIGGER USER;" % tabla)
        cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
        plan = cur.fetchone()[0][0]
        cur.execute("ROLLBACK;")
        t.append(round(plan["Execution Time"], 3))
        hijos = plan["Plan"].get("Plans", [])
        filas = hijos[0].get("Actual Rows") if hijos else None
    return t, filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--plataforma", default="")
    a = ap.parse_args()

    _, nombres = leer_indices()
    idx = sentencias_indices(leer_indices()[0])
    assert sorted(idx) == sorted(nombres) and len(nombres) == 9, nombres

    con = conectar(a.base)
    cur = con.cursor()
    cur.execute(PREPARAR)
    cur.execute("SELECT max(id_alerta) FROM (SELECT id_alerta FROM Alerta "
                "WHERE tipo = 'Humedad baja' ORDER BY id_alerta LIMIT 5000) s;")
    params = {"tope_alerta": cur.fetchone()[0]}
    ops = [(t, n, s % params if "%(" in s else s) for t, n, s in OPS]

    configs = [("sin", [])] + [(n, [n]) for n in nombres] + [("todos", list(nombres))]
    res = []
    for etiqueta, activos in configs:
        for n in nombres:
            cur.execute("DROP INDEX IF EXISTS %s;" % n)
        for n in activos:
            cur.execute(idx[n][1])
        cur.execute("VACUUM (FULL, ANALYZE);")
        cur.execute(PRENDER)
        tablas = {idx[n][0].lower() for n in activos} if len(activos) == 1 else None
        for tabla, nombre, sql in ops:
            if tablas is not None and tabla.lower() not in tablas:
                continue
            t, filas = medir_operacion(cur, tabla, sql)
            res.append(dict(configuracion=etiqueta, tabla=tabla, operacion=nombre, filas=filas,
                            tiempos=t, promedio=round(statistics.mean(t), 4),
                            desviacion=round(statistics.stdev(t), 4)))
            print("%-26s %-50s %10.3f ms" % (etiqueta, nombre, res[-1]["promedio"]), flush=True)

    # deja la copia con los 9 indices, como la base nominal
    for n in nombres:
        cur.execute(idx[n][1])
    cur.execute("DROP TABLE IF EXISTS _prueba_alerta;")
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    ajustes = {}
    for k in ("shared_buffers", "work_mem", "effective_cache_size",
              "max_parallel_workers_per_gather", "random_page_cost"):
        cur.execute("SHOW %s;" % k)
        ajustes[k] = cur.fetchone()[0]
    cur.close()
    con.close()

    salida = dict(plataforma=a.plataforma, version=version, ajustes=ajustes, iteraciones=IT,
                  indices={n: idx[n][0] for n in nombres}, resultados=res)
    with open(os.path.join(RES, "escritura_indices.json"), "w", encoding="utf-8") as fh:
        json.dump(salida, fh, indent=1, ensure_ascii=False)
    with open(os.path.join(RES, "escritura_indices.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["configuracion", "tabla", "operacion", "filas"] +
                   ["it%d" % (i + 1) for i in range(IT)] + ["promedio", "desviacion"])
        for r in res:
            w.writerow([r["configuracion"], r["tabla"], r["operacion"], r["filas"]] +
                       ["%.3f" % x for x in r["tiempos"]] +
                       ["%.4f" % r["promedio"], "%.4f" % r["desviacion"]])
    print("guardado en resultados/escritura_indices.json y .csv")


if __name__ == "__main__":
    main()
