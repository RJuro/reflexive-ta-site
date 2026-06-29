# MASSHINE — 2-interview run (Markdown record)

A plain-text record of one pipeline run over **2 interviews**: DP-40 GRANDE, M.txt, EI-845 RODWIN.txt.

Read the files in order — they are the stages of the pipeline:

1. `1_*_sections.md` — the LLM structure pass splits each transcript into sections.
2. `2_*_sentences.md` — spaCy indexes sentences; every later code points at these IDs.
3. `3_*_codes.md` — each document's codes, after the within-document reconcile.
4. `4_codebook.md` — the project codebook, after the across-document reconcile (stable IDs).
5. `5_themes.md` — candidate themes, built one interview at a time (the theorist reads each transcript + its codes against the themes so far): one claim each, line-anchored, with coverage/scope, a falsification test, and a 'how the themes emerged' trace.

Run: 136 codes in the project codebook, 9 candidate themes; 2 model calls this run (cached steps skipped).

This is the barebones view. The same run, with standpoint provenance and an interface, is on the project page (`index.html`) and the workbench mockup.
