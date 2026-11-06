# Configuración propia del proyecto

Copia aquí `rubrica.toml` o `modelos.toml` desde `src/nutria/data/` para personalizarlos; si un fichero no está aquí,
nutria usa el de `src/nutria/data/`.

- `config/rubrica.toml`: pesos, perfiles y anclas de la rúbrica (`docs/RUBRICA.md` es la salida de
  `nutria puntuar rubrica --markdown` con la rúbrica por defecto). Una rúbrica propia no puede quitar ninguna de las
  seis compuertas obligatorias (`seguridad`, `regulatoria`, `analitica`, `propiedad_intelectual`, `etica` y
  `destino_digestivo`): nutria la rechaza.
- `config/modelos.toml`: modelos de otros proveedores para la segunda opinión (`nutria llm preguntar`) y para medir
  modelos con los bancos de `evals/` (`nutria llm evaluar`). Para cada modelo, el identificador exacto (`modelo`), la
  fecha en que lo comprobaste (`verificado`), su política de datos (`politica_datos`: `local`, `zero_retention` o
  `standard`; la información confidencial solo va a los dos primeros) y `activo = true` para usarlo. Un modelo
  servido en tu máquina (Ollama, LM Studio o vLLM) se declara con `proveedor = "openai_compatible"`, su `base_url` y
  `politica_datos = "local"`: ver «Modelos locales para la segunda opinión» en el README. `nutria llm modelos` dice
  cuáles son utilizables. Los agentes de Claude Code siguen usando Claude; estos modelos solo dan la segunda opinión.

Las claves de API nunca se escriben en estos ficheros: van en variables de entorno (`api_key_env` dice cuál lee cada
modelo).
