# Validación read-only MCP en Linux/NAS — 2026-10-03

**Criterio de salida pendiente: no existe un servidor MCP remoto ni autenticación en esta rama, y no se proporcionó un gateway de staging.** No se declara autorización remota validada.

Rama exclusiva: `feature/tenazas-readonly-mcp`. El commit local previo `d5d1a4560484af3f7d081e41bc115760fda68caf` se preservó en un bundle Git. No hubo push, merge ni deploy.

## Entorno y comparación

Baseline anterior al PR: `59aa27df9793f33a2cc7ac71e1dc160d8962e7d3`. Se extrajeron ambos árboles sin cambiar de rama. Python 3.11.2, pytest 9.1.1; misma imagen y layout `/tmp/repo` para ambos.

Imagen de pruebas construida separadamente a partir de `sha256:bd6795ea0ff48bd365a79e405474cb27d3265c24bdcf5d0a95d3d46282fec9be` añadiendo pytest. UID/GID 1026, raíz read-only, red none, capacidades eliminadas y no-new-privileges. El código se monta read-only y las escrituras de tests quedan en tmpfs/staging. No se monta código/configuración de producción en la suite. Un hook adicional bloquea conexiones y comandos externos.

| Ejecución | Resultado |
| --- | --- |
| Adapter Linux | 70 aprobados; 0 fallados; 0 omitidos |
| Productor Linux | 6 aprobados; 0 fallados; 0 omitidos |
| `python3 -m pytest`, baseline y candidato | Ambos abortan colección con los mismos 6 errores y trazas idénticas |
| Suite baseline, `--continue-on-collection-errors` | 2688 aprobados, 114 fallados, 9 omitidos, 6 errores; 29 subtests aprobados |
| Suite candidato, mismo comando | 2764 aprobados, 114 fallados, 9 omitidos, 6 errores; 29 subtests aprobados |
| Regresiones nuevas entre casos ejecutados | 0: mismos 114 IDs y mensajes semánticos; +76 aprobados de esta frontera |

El flag de continuación no excluye archivos ni tests: permite ejecutar los casos recogidos pese a los errores de colección. La suite no está verde. La comparación de fallos usa mensajes exactos salvo dos direcciones de memoria de generadores y el orden de miembros de un set en un caso de catálogo. Las seis trazas de colección son idénticas completas.

## Los 38 errores iniciales de Windows

Se repitió la colección en Windows contra baseline y candidato: los 38 IDs y sus trazas coinciden tras normalizar sólo el directorio local. 29 son imports que sombrean el paquete `runtime` con `runtime.py`; 7 son módulos/dependencias ausentes; 2 son rutas absolutas que el aislamiento impide crear. Linux elimina los 29 imports, dispone de pypdf y permite las rutas heredadas únicamente en un tmpfs; quedan seis imports listados abajo.

| Caso | Causa reproducida antes del PR | Prueba |
| --- | --- | --- |
| `tests.test_advanced` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `41673f047f04` |
| `tests.test_background_agents` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `49343d88c28c` |
| `tests.test_cognitive` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `29722b0324e8` |
| `tests.test_compact` | ModuleNotFoundError: No module named 'compact' | traza idéntica `9a13f8e6da3a` |
| `tests.test_confidence` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `8f1af0f6da40` |
| `tests.test_daily_brief` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `f56bbe8eaec3` |
| `tests.test_daily_improvement` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `2d2185cb37a1` |
| `tests.test_eval_harness` | ModuleNotFoundError: No module named 'eval_metrics' | traza idéntica `4d833170c5d9` |
| `tests.test_expenses` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `e9ca0d680300` |
| `tests.test_finance` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `3526c7416aed` |
| `tests.test_goal_engine` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `2fc884c868d0` |
| `tests.test_idle_learning` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `6f55b6ec6641` |
| `tests.test_idle_learning_v2` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `6b1025148296` |
| `tests.test_intelligence` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `d423c828bdf2` |
| `tests.test_job_runner` | PermissionError: Review isolation: mutation outside local checkout blocked | traza idéntica `48b675c318f3` |
| `tests.test_jobs` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `b5827b2cb4c4` |
| `tests.test_model_router` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `8ddfa21fb79a` |
| `tests.test_multitool` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `61dd57951ca9` |
| `tests.test_n8n_workflows` | PermissionError: Review isolation: mutation outside local checkout blocked | traza idéntica `2d18ff2d7f3f` |
| `tests.test_observability` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `5b70cd6b5e95` |
| `tests.test_operational_memory` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `5d25b4527c6a` |
| `tests.test_orchestrator` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `6d3638152292` |
| `tests.test_personal_supervisor` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `fdee2515ec01` |
| `tests.test_planner` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `9d9e5be006b5` |
| `tests.test_query` | ModuleNotFoundError: No module named 'query' | traza idéntica `30b9735e91fa` |
| `tests.test_runtime` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `f95159019fa9` |
| `tests.test_runtime_utils` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `736405adeaa3` |
| `tests.test_safety_guard` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `f36ec68d60d2` |
| `tests.test_self_debugger` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `3fa99a0bc25c` |
| `tests.test_self_dev_engine` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `17e448280a3e` |
| `tests.test_shared_memory` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `4bd740a9deae` |
| `tests.test_skill_generator` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `0113e04f74d7` |
| `tests.test_smart_home_devices` | ModuleNotFoundError: No module named 'marantz_avr' | traza idéntica `5089258534cd` |
| `tests.test_smart_home_sync` | ModuleNotFoundError: No module named 'hue_sync_box' | traza idéntica `fbdf4a5b1e16` |
| `tests.test_stability` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `522a75bc2a48` |
| `tests.test_supervisor` | ImportError: cannot import name 'executors' from partially initialized module 'runtime' (most likely due to a circular import) (runtime.py del checkout local) | traza idéntica `4fc7ce585a37` |
| `tests.test_tailored_cv` | ModuleNotFoundError: No module named 'pypdf' | traza idéntica `00d96dc40210` |
| `tests.test_vacuum` | ModuleNotFoundError: No module named 'xiaomi_vacuum' | traza idéntica `4c2485d19f66` |

## Seis errores de colección en Linux

`compact` y `query` no están versionados en el árbol probado. `eval_metrics` está en `workspace/runtime/tool_tuning`; los módulos smart-home están en `workspace/runtime/devices`, mientras los tests intentan imports planos desde paths antiguos. No se copió código privado de skills ni credenciales de producción para hacerlos pasar.

| Archivo | Import ausente | Prueba |
| --- | --- | --- |
| `tests.test_compact` | ModuleNotFoundError: No module named 'compact' | traza idéntica `ae2bc1a5f4e2` |
| `tests.test_eval_harness` | ModuleNotFoundError: No module named 'eval_metrics' | traza idéntica `c6ee7750396d` |
| `tests.test_query` | ModuleNotFoundError: No module named 'query' | traza idéntica `8ff6bdaebce4` |
| `tests.test_smart_home_devices` | ModuleNotFoundError: No module named 'marantz_avr' | traza idéntica `b76e5a85214e` |
| `tests.test_smart_home_sync` | ModuleNotFoundError: No module named 'hue_sync_box' | traza idéntica `b5414359e641` |
| `tests.test_vacuum` | ModuleNotFoundError: No module named 'xiaomi_vacuum' | traza idéntica `6daacc9bf2de` |

## Clasificación de los 114 fallos heredados

Los tests usan expectativas anteriores al catálogo/política actual, skills no versionadas, rutas absolutas, registros métricos duplicados y aserciones ya incumplidas antes del PR. Cinco casos necesitan comandos que el aislamiento bloquea. La columna de prueba corresponde al hash del mensaje normalizado, idéntico en ambos árboles; cada caso aparece individualmente.

| Grupo | Casos |
| --- | --- |
| Dependencia/skill ausente | 46 |
| Aserción/lógica heredada: mismo síntoma antes del PR | 22 |
| Expectativa de catálogo/política desactualizada | 19 |
| Ruta/asset heredado fuera del layout de pruebas | 18 |
| Ejecución externa bloqueada por aislamiento | 5 |
| Registro Prometheus duplicado | 2 |
| Contrato de fecha/fixture heredado | 2 |

| Caso | Clasificación | Error/aserción reproducida | Prueba |
| --- | --- | --- | --- |
| `tests.simulation.governance.test_sim_ollama_gpu.TestSimOllamaGPU::test_action_registry_has_ollama_actions` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/action_registry.py' | idéntico `b74e2b59ffb3` |
| `tests.simulation.governance.test_sim_ollama_gpu.TestSimOllamaGPU::test_homelab_goals_has_evaluate` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/homelab_goals.py' | idéntico `6485ca1fa11e` |
| `tests.simulation.governance.test_sim_ollama_gpu.TestSimOllamaGPU::test_homelab_goals_has_ollama_check` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/homelab_goals.py' | idéntico `6485ca1fa11e` |
| `tests.simulation.governance.test_sim_ollama_gpu.TestSimOllamaGPU::test_infra_diagnostics_handles_ollama_offline` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/infra_diagnostics.py' | idéntico `9dec448f9b3f` |
| `tests.simulation.governance.test_sim_ollama_gpu.TestSimOllamaGPU::test_policy_engine_evaluates_risk` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/policy_engine.py' | idéntico `d6ba39f44dd2` |
| `tests.simulation.longrun.test_longrun_degradation.TestLongrunDegradation::test_benchmark_gate_blocks_degraded` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: True is not false | idéntico `f46e3f780121` |
| `tests.test_background_agents.TestAgentRegistry::test_three_agents_registered` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 4 != 3 | idéntico `92df68518280` |
| `tests.test_e2e_sprint5.TestIncidentToStaging::test_action_registry_exists` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: False is not true | idéntico `deb6e8e954ed` |
| `tests.test_e2e_sprint5.TestIncidentToStaging::test_action_validator_exists` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: False is not true | idéntico `deb6e8e954ed` |
| `tests.test_e2e_sprint5.TestIncidentToStaging::test_policy_engine_has_evaluate` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: False is not true | idéntico `deb6e8e954ed` |
| `tests.test_e2e_sprint5.TestPrometheusMetrics::test_ba_metrics_dont_crash` | Registro Prometheus duplicado | ValueError: Duplicated timeseries in CollectorRegistry: {'runtime_runs_total'} | idéntico `504bac5ad7a5` |
| `tests.test_iol_broker.TestAuthRetry::test_401_after_retry_raises_auth_error` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestAuthRetry::test_401_triggers_refresh_and_retry` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestAuthRetry::test_no_refresh_token_raises_auth_error` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestAuthRetry::test_no_retry_loop` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestAuthRetry::test_non_401_error_not_retried` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestAuthRetry::test_refresh_failure_raises_auth_error` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_get_calls_refresh_when_expired` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_get_includes_auth_header` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_log_written_on_request` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_refresh_posts_to_slash_token` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_refresh_sends_urlencoded_not_json` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_refresh_updates_bearer_token` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_refresh_updates_expiry_with_expires_in` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_refresh_updates_refresh_token` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_token_invalid_when_expired` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_token_invalid_when_none` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_token_invalid_within_margin_60s` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLClientToken::test_token_valid_when_future_expiry` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_comprar_returns_propuesta_not_execution` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_comprar_saves_pending_order_json` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_confirmar_executes_pending_order` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_cotizacion_extracts_ticker` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_portafolio_keyword` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLHandler::test_handle_unknown_returns_ayuda` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_cancelar_calls_delete_with_numero` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_default_mercado_bcba` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_default_plazo_t0` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_endpoint` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_logs_after_post` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_logs_before_post` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_payload_has_required_fields` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_raises_on_http_error` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_comprar_tipo_orden_es_precio_limite` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_format_orden_propuesta_contains_confirmacion_prompt` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_vender_endpoint` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLOrders::test_vender_payload_fields` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_format_cotizacion_telegram_contains_simbolo` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_format_portfolio_telegram_contains_posiciones` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_format_portfolio_telegram_contains_total` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_account_state_endpoint` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_cotizacion_endpoint` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_operations_endpoint` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_portfolio_custom_pais` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_portfolio_default_pais` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_portfolio_raises_on_http_error` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_iol_broker.TestIOLPortfolio::test_get_portfolio_returns_json` | Dependencia/skill ausente | ModuleNotFoundError: No module named 'iol_broker' | idéntico `62b295c169d8` |
| `tests.test_job_runner.JobRunnerTests::test_run_job_success` | Ejecución externa bloqueada por aislamiento | PermissionError: Review isolation: external network and command execution blocked | idéntico `74da9047a54a` |
| `tests.test_job_search_platform::test_discover_ingests_search_results` | Aserción/lógica heredada: mismo síntoma antes del PR | assert 0 == 1 | idéntico `74fec452b458` |
| `tests.test_job_search_platform::test_discover_rejects_spam_aggregates_and_irrelevant_results` | Aserción/lógica heredada: mismo síntoma antes del PR | assert 0 == 1 | idéntico `74fec452b458` |
| `tests.test_job_search_platform::test_search_queries_target_individual_job_pages` | Aserción/lógica heredada: mismo síntoma antes del PR | assert False | idéntico `46436f3a39b3` |
| `tests.test_job_search_platform::test_tam_alias_scores_role_but_company_name_does_not` | Aserción/lógica heredada: mismo síntoma antes del PR | assert 10 >= 50 | idéntico `b2020c131bb9` |
| `tests.test_job_search_watcher::test_run_once_notifies_only_new_ids` | Aserción/lógica heredada: mismo síntoma antes del PR | assert False | idéntico `0a5b00817749` |
| `tests.test_model_router.TestCatalogIntegrity::test_homelab_models_present` | Expectativa de catálogo/política desactualizada | AssertionError: Items in the first set but not the second: | idéntico `78e22775c5e3` |
| `tests.test_model_router.TestGuardrails::test_record_heavy_usage_tracks` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 0 != 1 | idéntico `cf63b544bc90` |
| `tests.test_model_router.TestRouteByRole::test_backward_compat_default_fast` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_code_selects_coder` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen2.5-coder:7b' != 'qwen3:8b' | idéntico `aa299b4645a7` |
| `tests.test_model_router.TestRouteByRole::test_explicit_role_override` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_fast_selects_7b` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_heavy_selects_32b` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_pre_fetched_backend_status` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_reason_selects_deepseek` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRole::test_vision_selects_vl` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-vl:4b' != 'qwen3:8b' | idéntico `2e59adab844b` |
| `tests.test_model_router.TestRouteByRoleWithGuardrails::test_force_heavy_bypasses_in_route` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestRouteByRoleWithGuardrails::test_heavy_degraded_after_cooldown` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' != 'qwen3:8b' | idéntico `597859c465b8` |
| `tests.test_model_router.TestSelectModelGPU::test_system_task_selects_capable_model` | Expectativa de catálogo/política desactualizada | AssertionError: 'qwen3-fast:8b' not found in ('qwen3:4b', 'qwen3:8b', 'qwen2.5:7b') | idéntico `c6becab1c50e` |
| `tests.test_new_agents.NewAgentContractTests::test_new_agents_own_expected_queries_and_return_observation` | Aserción/lógica heredada: mismo síntoma antes del PR | KeyError: 'expected' | idéntico `619635cf2e44` |
| `tests.test_orchestrator.TestModelSelection::test_fallback_when_gpu_offline` | Expectativa de catálogo/política desactualizada | AssertionError: 'local_cpu' != 'cloud' | idéntico `bfa499a5f39f` |
| `tests.test_orchestrator.TestRouterV2Escalation::test_escalation_uses_v2_model` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen3-fast:8b' != 'ollama/qwen3:8b' | idéntico `04413b7faeed` |
| `tests.test_orchestrator.TestRouterV2Integration::test_chat_routes_to_fast_model` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen3-fast:8b' != 'ollama/qwen3:8b' | idéntico `04413b7faeed` |
| `tests.test_orchestrator.TestRouterV2Integration::test_code_prompt_overrides_category` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen2.5-coder:7b' != 'ollama/qwen3:8b' | idéntico `2b6696a6e228` |
| `tests.test_orchestrator.TestRouterV2Integration::test_critical_context_flag` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen3-fast:8b' != 'ollama/qwen3:8b' | idéntico `04413b7faeed` |
| `tests.test_orchestrator.TestRouterV2Integration::test_reasoning_routes_to_deepseek` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen3-fast:8b' != 'ollama/qwen3:8b' | idéntico `04413b7faeed` |
| `tests.test_orchestrator.TestRouterV2Integration::test_vision_context_flag` | Expectativa de catálogo/política desactualizada | AssertionError: 'ollama/qwen3-vl:4b' != 'ollama/qwen3:8b' | idéntico `eec671477195` |
| `tests.test_phase9_metrics.TestPhase9Metrics::test_dashboard_contains_phase9_metric_names` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: 'grafana-tenazas-agent.json' | idéntico `f69b9b054331` |
| `tests.test_phase9_metrics.TestPhase9Metrics::test_metrics_server_reads_agent_metrics_state` | Registro Prometheus duplicado | ValueError: Duplicated timeseries in CollectorRegistry: {'runtime_runs_total'} | idéntico `504bac5ad7a5` |
| `tests.test_proposal_executor.TestGitSandboxEnsureCommit::test_staged_change_commits_normally` | Ejecución externa bloqueada por aislamiento | PermissionError: Review isolation: external network and command execution blocked | idéntico `74da9047a54a` |
| `tests.test_proposal_executor.TestGitSandboxEnsureCommit::test_unstaged_modified_file_does_not_crash_ensure_commit` | Ejecución externa bloqueada por aislamiento | PermissionError: Review isolation: external network and command execution blocked | idéntico `74da9047a54a` |
| `tests.test_proposal_executor.TestGitSandboxEnsureCommit::test_untracked_file_does_not_crash_ensure_commit` | Ejecución externa bloqueada por aislamiento | PermissionError: Review isolation: external network and command execution blocked | idéntico `74da9047a54a` |
| `tests.test_runtime.TestTelegramNotify::test_sends_on_valid_config` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: False is not true | idéntico `deb6e8e954ed` |
| `tests.test_runtime_config.TestCfgGet::test_walks_json_path` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 'default' != 'json_value' | idéntico `77e936e30cb2` |
| `tests.test_self_dev_engine.TestAnalyzeDebugReports::test_import_error_returns_empty` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: Lists differ: [{'module': 'benchmark', 'change_descripti[2766 chars]']}}] != [] | idéntico `d833a12994e0` |
| `tests.test_self_improvement_regression.TestNeverAutoDeploy::test_continuous_learning_never_writes_runtime` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/continuous_learning_loop.py' | idéntico `0b945ece7311` |
| `tests.test_self_improvement_regression.TestNeverAutoDeploy::test_no_runtime_writes_from_improvement_flow` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/improvement_candidates.py' | idéntico `433d4406fc73` |
| `tests.test_sprint10.TestTradingHypothesis::test_evaluate_hit` | Contrato de fecha/fixture heredado | AttributeError: 'function' object has no attribute 'isoformat' | idéntico `1679d0a34d36` |
| `tests.test_sprint10.TestTradingHypothesis::test_evaluate_missed` | Contrato de fecha/fixture heredado | AttributeError: 'function' object has no attribute 'isoformat' | idéntico `1679d0a34d36` |
| `tests.test_sprint10.TestTradingPaper::test_never_calls_binance_auth` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_paper.py' | idéntico `ccdd8c6d0429` |
| `tests.test_sprint10.TestTradingSafety::test_no_module_has_order_endpoints` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_observer.py' | idéntico `66c106942b6d` |
| `tests.test_sprint10.TestTradingSafety::test_no_module_has_trading_functions` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_observer.py' | idéntico `66c106942b6d` |
| `tests.test_sprint10.TestTradingSafety::test_no_module_imports_api_keys` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_observer.py' | idéntico `66c106942b6d` |
| `tests.test_sprint10.TestTradingSafety::test_observer_only_public_urls` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_observer.py' | idéntico `66c106942b6d` |
| `tests.test_sprint10.TestTradingSafety::test_paper_uses_only_public_endpoints` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_paper.py' | idéntico `ccdd8c6d0429` |
| `tests.test_sprint10.TestTradingSafety::test_scorer_never_auto_activates` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_scorer.py' | idéntico `de5a8863df48` |
| `tests.test_sprint10.TestTradingScorer::test_scorer_never_activates_phase6` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/trading_scorer.py' | idéntico `de5a8863df48` |
| `tests.test_sprint5_expanded.TestNoiseFilterExpanded::test_active_ttls_tracked` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 0 not greater than or equal to 2 | idéntico `d16aa4d574a9` |
| `tests.test_sprint5_expanded.TestNoiseFilterExpanded::test_stats_allowed_increments` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 0 != 2 | idéntico `a00e9f78f97e` |
| `tests.test_sprint5_expanded.TestNoiseFilterExpanded::test_suppression_count_accurate_after_mixed` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 1 != 3 | idéntico `813fd22d91bd` |
| `tests.test_sprint5_governance.TestNoiseFilter::test_stats_tracking` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 0 != 2 | idéntico `a00e9f78f97e` |
| `tests.test_sprint7.TestImprovementLoop::test_never_writes_to_runtime` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/improvement_loop.py' | idéntico `1c84d06edf9b` |
| `tests.test_sprint9.TestAgentSafety::test_self_improve_never_writes_runtime` | Ruta/asset heredado fuera del layout de pruebas | FileNotFoundError: [Errno 2] No such file or directory: '/home/tenazas/runtime/agent_self_improve.py' | idéntico `35a336233be2` |
| `tests.test_sprint9.TestProactiveObservations::test_proactive_agents_list_has_5` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 6 != 5 | idéntico `9e4bf2204c56` |
| `tests.test_sprint9.TestProactiveObservations::test_proactive_one_failure_others_continue` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 5 != 4 | idéntico `bf1a235ee4ae` |
| `tests.test_sprint9.TestProactiveObservations::test_proactive_success_with_mock_agents` | Aserción/lógica heredada: mismo síntoma antes del PR | AssertionError: 6 != 5 | idéntico `9e4bf2204c56` |
| `tests.test_tailored_cv::test_prepare_tailored_cv_revalidates_and_generates` | Ejecución externa bloqueada por aislamiento | PermissionError: Review isolation: external network and command execution blocked | idéntico `74da9047a54a` |

## Cache real, productor e aislamiento del rol gateway

El `runtime.update_state()` original persiste resultados de probes sin un contrato de exportación de secretos. Su `state.json` real era modo 0777 con ACL; **no se cambiaron esos permisos ni su contenido**. Los paths originales de infra y alertas no existen en el NAS, por lo que esa lectura real no prueba datos de storage/alertas; los fixtures cubren esos campos y su sanitización.

El nuevo productor separado exporta a un cache dedicado. Exige un inventario de credenciales, elimina campos desconocidos, redacta valores y representaciones URL/Base64/JSON, sustituye el status libre del contenedor y el título libre del incidente, y rechaza cualquier intersección residual con el JSON. El adapter sólo lee ese directorio; no importa al productor ni tiene su capacidad de escritura. El productor no se conectó al scheduler de producción.

Se leyó el estado real en modo lectura con UID/GID 1026 y se exportó a staging. El inventario se obtuvo del entorno del contenedor actual y su configuración OpenClaw: 10 valores únicos. Cinco placeholders de Ollama sin autenticación no son credenciales; `N8N_SECURE_COOKIE` es una política booleana, no una cookie. Los valores se mantuvieron en memoria/stdin del proceso productor y nunca se imprimieron ni se persistieron. El rol gateway no recibió el inventario.

Snapshot comprobado: SHA-256 `18270393ba433a8831a21626a4c9a49b3270c5d4fe55a399117b248e8a67bb4d`; 28 contenedores, incluidos los temporales de revisión si estaban presentes al refrescar el snapshot.

Resultado: cero coincidencias de las credenciales y representaciones inventariadas en snapshot, cache exportado y respuestas de las seis herramientas. Directorio del cache 0700, archivos 0600; lectura y ausencia de permiso de escritura comprobadas por el gateway con UID/GID 1026.

El rol gateway se ejecutó con sólo el adapter y cache sanitizado montados read-only: sin socket Docker, claves SSH accesibles, entorno de credenciales, cache original, configuración OpenClaw, red ni capacidades. No-new-privileges se verificó con prctl porque el kernel NAS no muestra ese campo en /proc. El kernel no permite limitar PIDs vía cgroup; no se afirma que ese límite haya sido probado.

Se probaron enlaces/hardlinks, sustitución por enlace antes de abrir, FIFO, JSON inválido y límites de tamaño, además de credenciales cortas/variantes en todos los campos de exportación. La primera pasada Linux detectó una alerta vacía al rechazar un enlace; se corrigió usando un default no-dict. Un test FIFO dejaba el archivo de prueba en /tmp y bloqueaba un scanner heredado de sandbox; el test ahora lo elimina en finally y la suite termina. El adapter nunca llegó a abrir el FIFO.

La comprobación de cero secretos se limita al inventario descrito y a las reglas de redacción. No demuestra ausencia universal de credenciales desconocidas: una contraseña corta desconocida puede parecer un identificador normal. Antes de exponer el gateway, completar y actualizar el inventario y comprobar permisos/aislamiento del servicio real, no sólo del rol de pruebas.

## Bloqueo de autenticación

No hay servidor MCP, middleware de autenticación, configuración de TLS ni endpoint de staging en la rama. Se pidió repositorio/configuración y URL fuera de producción. No se usó OpenClaw de producción para suplir ese faltante y no se inventaron pruebas de autenticación.

Pendiente: probar identidad válida, identidad ausente/inválida/expirada/revocada, rechazo sin datos antes de listar o ejecutar herramientas, protección de sesiones y logs sin credenciales, y la configuración de aislamiento del gateway remoto real. Hasta entonces el criterio de salida completo no está alcanzado.

## Artefactos y reproducción

Staging NAS: `/volume1/docker/tenazas-mcp-review-d5d1a45/`; evidencia JUnit y logs en `evidence/`. No es el directorio de producción. Los contenedores de pruebas fueron temporales; se conservaron imagen y artefactos para reproducibilidad. Los bundles y la copia Git local están bajo `C:/Users/kevin/codex-reviews/`.

La suite se ejecutó con `python3 -m pytest`; tras clasificar la interrupción, `python3 -m pytest --continue-on-collection-errors`. Para reproducir con seguridad, usar la misma imagen, UID, tmpfs, código read-only, `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges` y el hook que bloquea conexiones/comandos; no montar producción en la suite.

`tools/validate_mcp_cache_producer.py` es el probe del productor confiable: recibe un inventario explícito por stdin y escribe exclusivamente una exportación de staging. `tools/validate_mcp_gateway_cache.py` verifica el rol sin inventario ni cache original. Su opción de output para auditoría devuelve respuestas al cliente de validación en memoria, que comprueba secretos y descarta los datos antes de guardar sólo métricas. No son servidores MCP ni implementan autenticación.

**No hubo push, merge ni deploy. No se modificó código/configuración ni se reiniciaron servicios de producción.**
