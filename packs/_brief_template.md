# DEEP RESEARCH BRIEF TEMPLATE — standpoint-coder grounding (DORMANT / generalization)
#
# Fill the {{PLACEHOLDERS}} and run in any deep-research tool to produce a `reference.md` for a new
# domain pack. This is the domain-general generator; it is intentionally NOT wired into the engine
# (see packs/README.md → "GENERALIZATION IS OFF FOR NOW"). The migration pack's reference.md was
# produced from the filled-in version of this template.

PURPOSE
I am building a panel of LLM "standpoint coders" that each read the SAME {{UNIT}} through a distinct
research tradition, code it independently and blind, and whose divergences are analyzed as data (a
structured-disagreement engine, not a consensus tool). I need research-grounded knowledge to write a
defensible SYSTEM PROMPT for each standpoint. Governing principle all output must serve:

  A standpoint decides WHERE TO LOOK and WHAT QUESTIONS TO ASK — never WHAT IS FOUND. The lens
  supplies attention (sensitizing concepts); the data supplies content.

Central risk to defend against: CARICATURE — a persona that stamps its tradition's stock vocabulary
onto every passage instead of reading this text. Every deliverable must keep the lens
attention-directing, not conclusion-asserting.

DOMAIN
{{CORPUS_CONTEXT}} (e.g. patient illness narratives; UX interview transcripts; open survey
responses). Ground every tradition in how scholars ACTUALLY APPLY IT TO {{DOMAIN}} — not the -ism in
the abstract. Language/register: {{LANGUAGE}}.

STANDPOINTS TO PROFILE
{{STANDPOINTS}}  (the candidate roster; include a low-loading "calibration anchor" closest to the
neutral baseline so the distance from it measures how much each lens adds.)

FOR EACH STANDPOINT, PRODUCE:
- Paradigm family & one-line epistemic stance.
- 5–8 SENSITIZING CONCEPTS — the tradition's actual analytic constructs, each with originating
  theorist/work AND a one-line gloss of what it directs ATTENTION to (cited, from real scholarship).
- ANALYTIC QUESTIONS the lens asks of a passage — phrased as questions, never foregone conclusions.
- What it FOREGROUNDS that a neutral coder misses; what it BACKGROUNDS / where the lens legitimately
  goes quiet (for a "where my lens finds nothing" guard).
- Domain application: 2–3 concrete examples of what this lens would code, with the warranting text.
- Key theorists/works (foundational + domain-applied); flag established vs contested; note INTERNAL
  HETEROGENEITY so the persona isn't a monolith.
- CARICATURE TO AVOID: stock tropes/vocabulary + a worked shallow-vs-sophisticated contrast on one excerpt.

ALSO RESEARCH (meta-justification): the paradigm taxonomy (Lincoln & Guba; Creswell); "sensitizing
concepts" (Blumer; Charmaz); the chosen method frame's stance on theoretical lenses & positionality;
perspectivism / disagreement-as-data (Aroyo & Welty; Basile et al.).

CROSS-CUTTING DELIVERABLE — FRICTION MATRIX: 2–3 representative passage types; show how each
standpoint reads each differently, distinguishing INTERPRETIVE friction (same unit, different
meaning) from ATTENTIONAL friction (disagree on what is even codable).

SOURCING: cite real scholarship for every concept; prefer primary/applied sources; distinguish
established from contested; do not invent concepts.

OUTPUT FORMAT: one structured profile per standpoint, then meta-justification, friction matrix, and
a consolidated bibliography — written so an author can turn each profile directly into a system prompt.
