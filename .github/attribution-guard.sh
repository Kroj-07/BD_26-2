#!/usr/bin/env bash
# Revisa mensajes de commit y archivos rastreados contra el patron
# guardado en .github/attribution-pattern.b64 (sin distinguir mayusculas).
# Uso:
#   attribution-guard.sh msg <archivo>       mensaje de un commit (hook commit-msg)
#   attribution-guard.sh range <rev-list>    mensajes de los commits de un rango
#   attribution-guard.sh tree                archivos rastreados del arbol actual
set -euo pipefail

raiz=$(git rev-parse --show-toplevel)
archivo_patron=".github/attribution-pattern.b64"
patron=$(base64 -d "$raiz/$archivo_patron" | tr -d '\r\n')

case "${1:-}" in
  msg)
    if grep -v '^#' "$2" | grep -q -i -E -- "$patron"; then
      echo "Mensaje de commit rechazado: contiene un termino no permitido." >&2
      exit 1
    fi
    ;;
  range)
    shift
    malos=""
    for c in $(git rev-list "$@"); do
      if git log -1 --format=%B "$c" | grep -q -i -E -- "$patron"; then
        malos="$malos $c"
      fi
    done
    if [ -n "$malos" ]; then
      echo "Commits con terminos no permitidos en el mensaje:" >&2
      for c in $malos; do git log -1 --format='  %h %s' "$c" >&2; done
      exit 1
    fi
    echo "Mensajes revisados: $(git rev-list --count "$@") commits sin coincidencias."
    ;;
  tree)
    cd "$raiz"
    encontrados=$(git ls-files -z | grep -z -v -x -F "$archivo_patron" |
                  xargs -0 -r grep -a -l -i -E -- "$patron" || true)
    if [ -n "$encontrados" ]; then
      echo "Archivos rastreados con terminos no permitidos:" >&2
      echo "$encontrados" | sed 's/^/  /' >&2
      exit 1
    fi
    echo "Archivos revisados: $(git ls-files | wc -l) sin coincidencias."
    ;;
  *)
    echo "Uso: $0 msg <archivo> | range <rev-list> | tree" >&2
    exit 2
    ;;
esac
