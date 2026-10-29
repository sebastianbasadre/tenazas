# Desarrollo y validación

## Entorno local

Python **3.11+**, conforme a [pyproject.toml](../pyproject.toml). En Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

En PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Las dependencias de entrenamiento son opcionales en `pyproject.toml`; revisar
compatibilidad de Python, PyTorch/CUDA y scripts del workbench antes de instalarlas.
`requirements*.txt` no fija todas las versiones transitivas: no equivale a un lockfile.

## Pruebas

```bash
python -m pytest --collect-only
python -m pytest tests/test_runtime_config.py
# En Linux con Bash y dependencias requeridas:
bash tests/run_tests.sh
# Dentro de un despliegue preparado:
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

`pyproject.toml` agrega raíz, workspace y runtime al path de pytest. El runner Bash
configura su propio `PYTHONPATH` y ejecuta una selección curada de suites; no equivale
a descubrir automáticamente todos los archivos de `tests/`.

Los marcadores `integration` y `live` están declarados, pero no asumir que todas
las pruebas dependientes de infraestructura están marcadas. Inspeccionar la suite
antes de ejecutarla. Coleccionar tests tampoco garantiza ausencia de efectos de
importación. Usar un entorno aislado, sin tokens ni acceso a equipos personales,
para la primera revisión.

Para un cambio documental, validar rutas, enlaces, bloques de código y coherencia
con las fuentes. No hace falta ejecutar workflows reales, bots o jobs financieros
para verificar Markdown. No publicar conteos de tests como garantía permanente.

## Estilo y contratos

```bash
python -m ruff check workspace/runtime tools tests
```

Ruff se configura en `pyproject.toml`; el repositorio puede tener deuda previa.
Acotar la revisión al cambio y documentar fallos preexistentes.

Antes de cambiar routing, leer el
[contrato](../workspace/runtime/INTERFACE_CONTRACT.md). Mantener labels en
`routes_contract.py`; una modificación debe revisar clasificación, dispatch,
métricas, dashboards y pruebas correspondientes.

## Configuración y cambios

- Leer [CLAUDE.md](../CLAUDE.md) y las instrucciones locales aplicables. Los prompts
  GSD están en `.codex/prompts/`; requieren un engine GSD instalado aparte.
- El checkout no contiene el engine `.claude/get-shit-done`; no presentar sus
  comandos como disponibles sin verificar la instalación local/global.
- Separar configuración válida del gateway de extensiones de Tenazas.
- Usar plantillas con placeholders. Revisar el diff antes de incluir estado, CV,
  credenciales, logs o resultados de servicios reales.
- Describir en el PR qué cambió, cómo se validó y qué requiere verificación en NAS.

## Mantener esta documentación

Actualizar las guías junto con cambios de Compose, mounts, flags, puertos o APIs.
Usar enlaces relativos para que funcionen en GitHub. Conservar los reportes fechados
como historia y registrar fecha/alcance de las revisiones operativas.
