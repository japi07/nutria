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
   - [ ] antibióticos distintos de coccidiostáticos e histomonóstatos como aditivos: no pueden autorizarse
     (Reglamento (CE) 1831/2003, art. 11, apartado 2; CELEX 32003R1831; pendiente de verificación en el texto
     consolidado). La prohibición no se limita a los antibióticos como promotores del crecimiento: vale sea cual sea
     la finalidad, así que alcanza también a los **moduladores ruminales antimicrobianos para vacuno (p. ej.
     ionóforos como la monensina)** y a los reductores de metano que sean antibióticos. La única excepción es su
     autorización como coccidiostático o histomonóstato (categoría propia de aditivos, para las especies y condiciones
     de esa autorización), que no ampara el uso como modulador ruminal (pendiente de verificación). Para esa finalidad
     no hay vía como pienso ni como aditivo: un "sí" aquí es `bloqueada` salvo rediseño del activo (no basta cambiar la
     alegación); la vía del medicamento veterinario queda fuera de alcance (paso 2). Con otro antimicrobiano
     propuesto como modulador ruminal (p. ej. un extracto vegetal o un compuesto de síntesis), comprueba si encaja en
     la definición de antibiótico del propio reglamento (antimicrobiano producido por un microorganismo o derivado de
     él; art. 2, pendiente de verificación) y si es medicamento por función (paso 2); mientras no esté claro, la
     propuesta es `no_resuelta` con revisión de un profesional, nunca `superada`.
7. **Alegaciones** (Reglamento (CE) 767/2009, art. 13). Redacta la alegación propuesta y comprueba:
   - 13.1: es objetiva, verificable por las autoridades y comprensible, y hay justificación científica que aportar si
     se pide (pruebas científicas públicas o investigación documentada de la empresa);
   - 13.2: es de optimización de la nutrición o de apoyo o protección de las condiciones fisiológicas (permitidas),
     no de prevenir, tratar o curar enfermedades;
   - 13.3.a: no es una alegación de enfermedad (prohibidas, con excepciones limitadas).
8. **Otros marcos**: higiene (183/2005), sustancias indeseables (Directiva 2002/32/CE), Catálogo (68/2013 y
   modificaciones) y Registro de materias primas, subproductos animales (1069/2009, 142/2011), plaguicidas
   (396/2005), normativa nacional (en España, MAPA) y mercados fuera de la UE.
   - **OMG** (Reglamentos (CE) 1829/2003 y 1830/2003; CELEX 32003R1829; pendiente de verificación): no se aplican a
     todo producto de fermentación con cepas modificadas. El considerando 16 del 1829/2003 excluye lo producido "con"
     un OMG (frente a lo producido "a partir de" un OMG): en un producto de fermentación, lo determinante es si
     contiene células viables o ADN recombinante de la cepa de producción (guía de la EFSA sobre la caracterización
     de microorganismos en la cadena alimentaria; referencia exacta pendiente de verificación). Anota cuál es el caso
     y con qué análisis se demostraría.

## Seguridad (esquema FEEDAP)

Especie diana (tolerancia), consumidor (residuos, genotoxicidad), usuario (inhalación, irritación, sensibilización),
medio ambiente (excreción, persistencia). En microorganismos y productos de fermentación, además: identificación de
la cepa, estatus QPS (presunción cualificada de seguridad) de EFSA (pendiente de verificación de la lista vigente),
genes de resistencia a antimicrobianos adquiridos, toxigenicidad o patogenicidad y, si la cepa de producción está
modificada, ausencia de células viables y de ADN recombinante en el producto. Lista los datos existentes y los que
faltan. La compuerta `seguridad` la propone `compuestos-toxicologia`; el triage regulatorio aporta los requisitos.

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

Registra después la clasificación (situación, ruta y grupo funcional) con
`nutria --agente regulacion-seguridad oportunidad clasificar <ID> --ruta <ruta> [--grupo-funcional <categoria.grupo>] [--situacion <situación>] --motivo "..."`
(sin `--firma-humana`; `--grupo-funcional` es obligatorio con una ruta `aditivo_*`; una ruta `fuera_de_alcance_*` bloquea la compuerta regulatoria; sin clasificación, la ruta del registro queda en
`indeterminada` y el workflow lo señala). Comprueba cada acto citado con `nutria --agente regulacion-seguridad normativa eurlex <CELEX>` (título, fecha, ELI y versión consolidada; sin red, la base legal queda pendiente de verificación).

Registra la propuesta, sin `--firma-humana`:
`nutria --agente regulacion-seguridad puntuar compuerta <ID> regulatoria <estado> --motivo "..."`. Una propuesta
`bloqueada` actúa de inmediato; `superada` no cuenta hasta que la firme una persona; salir de `bloqueada` exige firma
humana.
