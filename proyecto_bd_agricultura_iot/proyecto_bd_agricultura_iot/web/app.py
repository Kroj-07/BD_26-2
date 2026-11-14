"""
Aplicacion web minima sobre la base de agricultura de precision.

Ejecuta las cinco consultas del experimento (leidas de sql/03_consultas.sql),
muestra las vistas de usuario y permite registrar lecturas y atender alertas,
lo que activa el procedimiento sp_registrar_lectura y el trigger de alertas.

Conexion: variables estandar de PostgreSQL (PGHOST, PGPORT, PGUSER,
PGPASSWORD). La base por defecto es agro1m; se cambia con PGDATABASE.
Uso:  python app.py   y abrir http://localhost:5000
"""
import os
import re
import time
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, abort, flash, redirect, render_template, request, url_for

AQUI = os.path.dirname(os.path.abspath(__file__))
RUTA_CONSULTAS = os.path.join(AQUI, "..", "sql", "03_consultas.sql")
BASE = os.environ.get("PGDATABASE", "agro1m")

app = Flask(__name__)
app.secret_key = os.environ.get("CLAVE_SESION", "agro-mvp-local")

INFO = {
    1: ("Parcelas con estrés hídrico", "Agrónomo",
        "Humedad promedio del último mes en cada parcela, comparada con el mínimo "
        "óptimo de su cultivo vigente. Devuelve las parcelas por debajo del umbral."),
    2: ("Eficiencia hídrica por campaña", "Gerencia",
        "Litros de riego aplicados por kilo cosechado en cada campaña. Solo las "
        "campañas que consumen más agua que el promedio."),
    3: ("Sensores con lecturas anómalas", "Técnico de mantenimiento",
        "Proporción de lecturas fuera del rango óptimo por sensor. Devuelve los que "
        "superan la proporción de toda la red; suelen estar descalibrados."),
    4: ("Ranking de rendimiento por fundo", "Gerencia",
        "Kilos por hectárea de cada campaña (primera y segunda categoría), con su "
        "puesto dentro del fundo y el promedio del fundo."),
    5: ("Respuesta de riego ante alertas", "Jefatura de operaciones",
        "Porcentaje de alertas de humedad baja (alta o crítica) que recibieron un "
        "riego en su zona dentro de las seis horas siguientes."),
}


def cargar_consultas():
    texto = open(RUTA_CONSULTAS, encoding="utf-8").read()
    partes = re.split(r"-- -+ CONSULTA (\d) -+\n", texto)
    consultas = {}
    for i in range(1, len(partes), 2):
        lineas = [l for l in partes[i + 1].strip().splitlines() if not l.startswith("--")]
        consultas[int(partes[i])] = "\n".join(lineas).strip().rstrip(";")
    return consultas


CONSULTAS = cargar_consultas()


def conectar():
    return psycopg2.connect(dbname=BASE)


def ejecutar(sql, params=None):
    """Devuelve (columnas, filas, milisegundos medidos desde la aplicacion)."""
    with conectar() as con, con.cursor() as cur:
        inicio = time.perf_counter()
        cur.execute(sql, params)
        filas = cur.fetchall() if cur.description else []
        ms = (time.perf_counter() - inicio) * 1000
        columnas = [c.name for c in cur.description] if cur.description else []
    con.close()
    return columnas, filas, ms


def uno(sql, params=None):
    with conectar() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        fila = cur.fetchone()
    con.close()
    return fila


@app.context_processor
def comunes():
    return {"base": BASE, "info": INFO}


@app.route("/")
def inicio():
    totales = uno("""
        SELECT (SELECT count(*) FROM Fundo)         AS fundos,
               (SELECT count(*) FROM Parcela)       AS parcelas,
               (SELECT count(*) FROM Sensor)        AS sensores,
               (SELECT count(*) FROM Lectura_Suelo) AS lecturas,
               (SELECT count(*) FROM Evento_Riego)  AS riegos,
               (SELECT count(*) FROM Alerta WHERE NOT atendida) AS pendientes""")
    _, diagnostico, _ = ejecutar("""
        SELECT diagnostico, count(*) FROM vw_estado_hidrico_zona
        GROUP BY diagnostico ORDER BY 1""")
    return render_template("inicio.html", t=totales, diagnostico=dict(diagnostico))


@app.route("/consulta/<int:n>")
def consulta(n):
    if n not in CONSULTAS:
        abort(404)
    columnas, filas, ms = ejecutar(CONSULTAS[n])
    plan, ms_servidor = None, None
    if request.args.get("plan"):
        _, lineas, _ = ejecutar("EXPLAIN (ANALYZE, BUFFERS) " + CONSULTAS[n])
        plan = "\n".join(l[0] for l in lineas)
        m = re.search(r"Execution Time: ([\d.]+) ms", plan)
        ms_servidor = float(m.group(1)) if m else None
    return render_template("consulta.html", n=n, sql=CONSULTAS[n], columnas=columnas,
                           filas=filas, ms=ms, plan=plan, ms_servidor=ms_servidor)


@app.route("/estado")
def estado():
    fundo = request.args.get("fundo", "")
    diag = request.args.get("diagnostico", "")
    columnas, filas, ms = ejecutar("""
        SELECT fundo, codigo_parcela, num_zona, cultivo, ultima_medicion,
               humedad_pct, humedad_optima_min, humedad_optima_max, diagnostico
        FROM vw_estado_hidrico_zona
        WHERE (%(f)s = '' OR fundo = %(f)s) AND (%(d)s = '' OR diagnostico = %(d)s)
        ORDER BY fundo, codigo_parcela, num_zona""", {"f": fundo, "d": diag})
    _, fundos, _ = ejecutar("SELECT nombre FROM Fundo ORDER BY 1")
    return render_template("estado.html", columnas=columnas, filas=filas, ms=ms,
                           fundos=[f[0] for f in fundos], fundo=fundo, diag=diag)


@app.route("/alertas")
def alertas():
    fundo = request.args.get("fundo", "")
    sev = request.args.get("severidad", "")
    columnas, filas, ms = ejecutar("""
        SELECT id_alerta, fundo, codigo_parcela, num_zona, sensor, tipo, severidad,
               valor_registrado, fecha_hora, horas_sin_atender
        FROM vw_alertas_pendientes
        WHERE (%(f)s = '' OR fundo = %(f)s) AND (%(s)s = '' OR severidad = %(s)s)
        LIMIT 200""", {"f": fundo, "s": sev})
    _, total, _ = ejecutar("""
        SELECT count(*) FROM vw_alertas_pendientes
        WHERE (%(f)s = '' OR fundo = %(f)s) AND (%(s)s = '' OR severidad = %(s)s)""",
                           {"f": fundo, "s": sev})
    _, fundos, _ = ejecutar("SELECT nombre FROM Fundo ORDER BY 1")
    _, operarios, _ = ejecutar("""
        SELECT id_operario, nombre || ' ' || apellido || ' (' || rol || ')'
        FROM Operario ORDER BY apellido, nombre""")
    return render_template("alertas.html", columnas=columnas, filas=filas, ms=ms,
                           total=total[0][0], fundos=[f[0] for f in fundos],
                           operarios=operarios, fundo=fundo, sev=sev)


@app.route("/alertas/<int:id_alerta>/atender", methods=["POST"])
def atender(id_alerta):
    operario = request.form.get("operario") or None
    with conectar() as con, con.cursor() as cur:
        cur.execute("""
            UPDATE Alerta SET atendida = TRUE, fecha_atencion = now()::timestamp(0),
                              id_operario = %s
            WHERE id_alerta = %s AND NOT atendida""", (operario, id_alerta))
        cambiadas = cur.rowcount
    con.close()
    if cambiadas:
        flash("Alerta %d marcada como atendida." % id_alerta, "ok")
    else:
        flash("La alerta %d ya estaba atendida o no existe." % id_alerta, "error")
    return redirect(url_for("alertas", fundo=request.form.get("fundo", ""),
                            severidad=request.form.get("severidad", "")))


SENSORES_ACTIVOS = """
    SELECT d.id_dispositivo, d.codigo_serie, s.tipo_sensor, f.nombre, p.codigo_parcela,
           d.num_zona, cu.nombre_comun, cu.humedad_optima_min, cu.humedad_optima_max
    FROM Sensor s
    JOIN Dispositivo d ON d.id_dispositivo = s.id_dispositivo
    JOIN Parcela     p ON p.id_parcela = d.id_parcela
    JOIN Fundo       f ON f.id_fundo = p.id_fundo
    LEFT JOIN Campania ca ON ca.id_parcela = p.id_parcela AND ca.estado = 'En curso'
    LEFT JOIN Cultivo  cu ON cu.id_cultivo = ca.id_cultivo
    WHERE d.estado = 'Activo' AND s.tipo_sensor IN ('Humedad', 'Multiparametro')
    ORDER BY f.nombre, p.codigo_parcela, d.num_zona, d.codigo_serie"""


def numero(valor, entero=False):
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return None
    return int(valor) if entero else float(valor)


@app.route("/lecturas/nueva", methods=["GET", "POST"])
def nueva_lectura():
    resultado = None
    if request.method == "POST":
        f = request.form
        try:
            sensor = int(f["sensor"])
            fecha = datetime.strptime(f["fecha"], "%Y-%m-%dT%H:%M")
            valores = (numero(f.get("humedad")), numero(f.get("temperatura")),
                       numero(f.get("ph")), numero(f.get("ce")),
                       numero(f.get("bateria"), entero=True))
        except (KeyError, ValueError):
            flash("Revisa los datos: hay un valor con formato incorrecto.", "error")
            return redirect(url_for("nueva_lectura"))
        try:
            with conectar() as con, con.cursor() as cur:
                cur.execute("SELECT count(*) FROM Lectura_Suelo WHERE id_dispositivo = %s "
                            "AND fecha_hora = date_trunc('minute', %s::timestamp)",
                            (sensor, fecha))
                existia = cur.fetchone()[0] > 0
                cur.execute("CALL sp_registrar_lectura(%s, %s, %s, %s, %s, %s, %s)",
                            (sensor, fecha) + valores)
                cur.execute("""
                    SELECT a.id_alerta, a.tipo, a.severidad, a.valor_registrado
                    FROM Alerta a
                    WHERE a.id_dispositivo = %s
                      AND a.fecha_hora = date_trunc('minute', %s::timestamp)
                    ORDER BY a.id_alerta""", (sensor, fecha))
                generadas = cur.fetchall()
            con.close()
            resultado = {"existia": existia, "alertas": generadas,
                         "sensor": sensor, "fecha": fecha}
        except psycopg2.Error as e:
            mensaje = (e.diag.message_primary if e.diag and e.diag.message_primary
                       else str(e)).strip()
            flash("PostgreSQL rechazó la lectura: " + mensaje, "error")
    _, sensores, _ = ejecutar(SENSORES_ACTIVOS)
    ahora = datetime.now().strftime("%Y-%m-%dT%H:%M")
    return render_template("lectura.html", sensores=sensores, ahora=ahora,
                           resultado=resultado, form=request.form)


@app.errorhandler(psycopg2.OperationalError)
def sin_conexion(e):
    return render_template("error.html", detalle=str(e).strip()), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PUERTO", 5000)), debug=False)
