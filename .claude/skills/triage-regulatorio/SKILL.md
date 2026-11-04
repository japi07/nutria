---
name: triage-regulatorio
description: Árbol de decisión para clasificar un candidato en el marco europeo de alimentación animal (pienso dietético, medicamento veterinario por alegación o por función, biocida, materia prima, aditivo y grupo funcional), comprobar sustancias prohibidas o restringidas y alegaciones permitidas, y proponer la compuerta regulatoria. Úsalo en el triage de cada oportunidad.
---

# Triage regulatorio (UE)

Referencia: `docs/REGULACION_UE.md`. Verifica siempre el texto consolidado vigente en EUR-Lex y anota la fecha; lo
que no hayas comprobado en el texto vigente, márcalo "pendiente de verificar". Las conclusiones son preliminares y
requieren revisión de un profesional de asuntos regulatorios.

## Árbol de decisión (en este orden)

1. **¿Pienso para fines de nutrición específicos?** Comprueba primero si el objetivo encaja en un uso previsto del
   Reglamento (UE) 2020/354 (incluye la reducción del riesgo de cetosis y de fiebre de la leche; la entrada de fiebre
   de la leche la modificó el Reglamento (UE) 2024/2899), con sus características nutricionales y sus condiciones de
   etiquetado. El reglamento expresa la DCAD como (Na + K) − (Cl + S), en mEq/kg de MS. Si encaja, formula la
   alegación en los términos de ese uso previsto. Sigue al paso 2 en cualquier caso.
2. **¿Medicamento veterinario?** (Reglamento (UE) 2019/6, art. 4). La definición tiene dos criterios y basta uno:
   - por **alegación o presentación**: se presenta como tratamiento o prevención de enfermedades;
   - por **función**: se usa en animales o se les administra para restablecer, corregir o modificar funciones
     fisiológicas ejerciendo una acción farmacológica, inmunológica o metabólica.
   Si cumple alguno → compuerta `regulatoria` = `bloqueada` (ruta `fuera_de_alcance_medicamento`) salvo rediseño de
   la alegación y del mecanismo. Los piensos no pueden alegar prevenir, tratar o curar enfermedades (Reglamento (CE)
   767/2009, art. 13.3.a, con excepciones limitadas).
3. **¿Biocida** (p. ej. desinfección del entorno)? → Reglamento (UE) 528/2012: fuera de alcance (ruta
   `fuera_de_alcance_biocida`).
4. **¿Materia prima o aditivo?** Aplica la Recomendación 2011/25/UE (directrices para distinguir materia prima,
   aditivo, biocida y medicamento veterinario). "Cubrir necesidades nutricionales" no basta para clasificar algo como
   materia prima: la categoría de aditivos nutricionales del anexo I del Reglamento (CE) 1831/2003 incluye
   vitaminas, compuestos de oligoelementos, aminoácidos y urea. La dosis baja por sí sola tampoco decide.
5. **Si es aditivo**: categoría y grupo funcional del anexo I del Reglamento (CE) 1831/2003 (tecnológicos, incluidos
   los "otros aditivos tecnológicos"; sensoriales; nutricionales; zootécnicos, incluidos los "estabilizadores de las
   condiciones fisiológicas" para animales sanos y su resiliencia frente a factores de estrés —ambos grupos los
   añadió el Reglamento (UE) 2019/962—; coccidiostáticos e histomonóstatos).
   - ¿Ya está en el Registro de aditivos de la UE para esa especie y condiciones? ¿Autorización genérica o ligada a
     un titular?
   - Si es nuevo: solicitud según el Reglamento (CE) 429/2008 y las guías vigentes del Panel FEEDAP (identidad,
     seguridad para especie diana, consumidor, usuario y medio ambiente, eficacia) y método analítico evaluado por el
     EURL-FA. Guía de eficacia vigente: EFSA J 2024;22(7):e8856 (DOI 10.2903/j.efsa.2024.8856), adoptada el
     6-6-2024 y aplicable desde el 1-12-2024; sustituye a la de 2018 (EFSA J 16(5):5274, DOI 10.2903/j.efsa.2018.5274).
   - Reglamento (UE) 2019/1381 (transparencia; aplicable desde el 27-03-2021), que añade al Reglamento (CE) 178/2002
     el asesoramiento previo de EFSA antes de presentar una solicitud (art. 32a) y la obligación de notificar a EFSA
     los estudios encargados para respaldarla (art. 32b): planifica la notificación antes de encargar estudios.
   - Un extracto autorizado como aromatizante (aditivo sensorial) no puede comercializarse alegando efectos zootécnicos.
6. **Sustancias prohibidas o restringidas** (lista de comprobación; un "sí" bloquea la compuerta `regulatoria` salvo
   rediseño):
   - [ ] materiales cuya comercialización o uso en alimentación animal está restringido o prohibido (Reglamento (CE)
     767/2009, art. 6 y anexo III; p. ej. semillas tratadas con fitosanitarios tras la cosecha, madera tratada con
     protectores);
   - [ ] sustancias de efecto hormonal o tireostático y beta-agonistas (Directiva 96/22/CE, modificada por las
     Directivas 2003/74/CE y 2008/97/CE; con excepciones terapéuticas o zootécnicas controladas);
   - [ ] restricciones de EET a las proteínas de origen animal (Reglamento (CE) 999/2001; pendiente de verificar el
     anexo vigente);
   - [ ] antibióticos como promotores del crecimiento: no autorizados (base legal: verificar artículo en el texto
     consolidado).
7. **Alegaciones** (Reglamento (CE) 767/2009, art. 13). Redacta la alegación propuesta y comprueba:
   - 13.1: es objetiva, verificable por las autoridades y comprensible, y hay justificación científica que aportar si
     se pide (pruebas científicas públicas o investigación documentada de la empresa);
   - 13.2: es de optimización de la nutrición o de apoyo o protección de las condiciones fisiológicas (permitidas),
     no de prevenir, tratar o curar enfermedades;
   - 13.3.a: no es una alegación de enfermedad (prohibidas, con excepciones limitadas).
8. **Otros marcos**: higiene (183/2005), sustancias indeseables (Directiva 2002/32/CE), Catálogo (68/2013 y
   modificaciones) y Registro de materias primas, subproductos animales (1069/2009, 142/2011), OMG (1829/2003,
   1830/2003), plaguicidas (396/2005), normativa nacional (en España, MAPA) y mercados fuera de la UE.

## Seguridad (esquema FEEDAP)

Especie diana (tolerancia), consumidor (residuos, genotoxicidad), usuario (inhalación, irritación, sensibilización),
medio ambiente (excreción, persistencia). Lista los datos existentes y los que faltan. La compuerta `seguridad` la
propone `compuestos-toxicologia`; el triage regulatorio aporta los requisitos.

## Salida

```
Uso previsto de pienso dietético (2020/354): sí (cuál) | no — base legal y fecha de consulta
Medicamento veterinario (2019/6, art. 4): por alegación sí | no · por función sí | no — motivo
Biocida: sí | no
Clasificación propuesta: ... (base legal: artículo/anexo, fecha de consulta)
Sustancias prohibidas o restringidas: resultado de cada punto de la lista
Alegación propuesta y comprobación del art. 13 de 767/2009
Situación actual: autorizado | catalogado | nuevo
Vía: ... | Datos exigibles: ... | Asesoramiento previo y notificación de estudios (2019/1381): ...
Plazo y coste orientativos: [SUP] con fuente si existe
Compuerta regulatoria propuesta: superada | no_resuelta | bloqueada — motivo
Requisitos de seguridad para compuestos-toxicologia: ...
Incertidumbres que requieren revisión profesional: ...
```

Registra la propuesta, sin `--firma-humana`:
`nutria --agente regulacion-seguridad puntuar compuerta <ID> regulatoria <estado> --motivo "..."`. Una propuesta
`bloqueada` actúa de inmediato; `superada` no cuenta hasta que la firme una persona; salir de `bloqueada` exige firma
humana.
