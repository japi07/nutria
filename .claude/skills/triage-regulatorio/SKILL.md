---
name: triage-regulatorio
description: Árbol de decisión para clasificar un candidato en el marco europeo de alimentación animal (materia prima, aditivo y grupo funcional, pienso dietético, medicamento veterinario, biocida) y decidir las compuertas regulatoria y de seguridad. Úsalo en el triage de cada oportunidad.
---

# Triage regulatorio (UE)

Referencia: `docs/REGULACION_UE.md`. Verifica siempre el texto consolidado vigente en EUR-Lex y anota la fecha.

## Árbol de decisión

1. **¿El efecto buscado o la alegación es prevenir, tratar o curar una enfermedad?** → medicamento veterinario
   (Reglamento (UE) 2019/6); los piensos no pueden hacer esas alegaciones (Reglamento (CE) 767/2009, art. 13).
   Compuerta `regulatoria` = `bloqueada` salvo que se rediseñe la alegación hacia un efecto nutricional o fisiológico.
2. **¿Actúa como biocida** (p. ej. desinfección del entorno)? → Reglamento (UE) 528/2012: fuera de alcance.
3. **¿Materia prima o aditivo?** Aplica la Recomendación 2011/25/UE: una materia prima cubre necesidades
   nutricionales; si la función principal es un efecto tecnológico, sensorial, nutricional específico o zootécnico a
   baja inclusión, es aditivo. La dosis baja por sí sola no decide.
4. **Si es aditivo**: categoría y grupo funcional del anexo I del Reglamento (CE) 1831/2003 (tecnológicos,
   sensoriales, nutricionales, zootécnicos —incluidos los estabilizadores de las condiciones fisiológicas añadidos
   por el Reglamento (UE) 2019/962—, coccidiostáticos e histomonóstatos).
   - ¿Ya está en el Registro de aditivos de la UE para esa especie y condiciones? ¿Autorización genérica o ligada a
     un titular?
   - Si es nuevo: solicitud según el Reglamento (CE) 429/2008 y las guías vigentes del Panel FEEDAP (identidad,
     seguridad para especie diana, consumidor, usuario y medio ambiente, eficacia) y método analítico evaluado por el
     EURL-FA.
   - Un extracto autorizado como aromatizante (aditivo sensorial) no puede comercializarse alegando efectos zootécnicos.
5. **Pienso dietético**: ¿el objetivo encaja en un uso previsto del Reglamento (UE) 2020/354 (p. ej. reducción del
   riesgo de cetosis o de fiebre de la leche) con sus características nutricionales y condiciones de etiquetado?
6. **Otros marcos**: higiene (183/2005), sustancias indeseables (Directiva 2002/32/CE), Catálogo (68/2013 y
   modificaciones) y Registro de materias primas, subproductos animales (1069/2009, 142/2011), EET (999/2001), OMG
   (1829/2003, 1830/2003), plaguicidas (396/2005), normativa nacional (en España, MAPA) y mercados fuera de la UE.

## Seguridad (esquema FEEDAP)

Especie diana (tolerancia), consumidor (residuos, genotoxicidad), usuario (inhalación, irritación, sensibilización),
medio ambiente (excreción, persistencia). Lista los datos existentes y los que faltan.

## Salida

```
Clasificación propuesta: ... (base legal: artículo/anexo, fecha de consulta)
Situación actual: autorizado / catalogado / nuevo
Vía: ... | Datos exigibles: ... | Plazo y coste orientativos: [SUP] con fuente si existe
Compuerta regulatoria: superada | no_resuelta | bloqueada — motivo
Compuerta seguridad: superada | no_resuelta | bloqueada — motivo
Incertidumbres que requieren revisión profesional: ...
```
Registra: `nutria --agente regulacion-seguridad puntuar compuerta <ID> regulatoria <estado> --motivo "..."`.
