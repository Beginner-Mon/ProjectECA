# Safety evaluation set (paper H2, ICIIT 2027)

Plan, research questions and pilot results: `docs/plans/paper-safety-eval-h2.md`.

- `build_dataset.py`: NHS criteria → persona sample → scenarios → generated messages →
  blind verification → no-diacritics variants.
- `run_eval.py`: runs the real planner on the verified items (no database, no AWS),
  then scores them.
- `data/`: generated data. `results/`: planner runs and reports.

## Attribution

- `data/criteria.json` quotes the NHS website (nhs.uk), back / knee / neck / shoulder pain
  pages. Contains public sector information licensed under the Open Government Licence v3.0.
- `data/personas.jsonl` and the messages built from it use *Nemotron-Personas-Vietnam*
  (NVIDIA and FPT, 2026, https://huggingface.co/datasets/nvidia/Nemotron-Personas-Vietnam),
  licensed under CC BY 4.0. The personas are synthetic and describe no real person.
  Only the fields listed in `KEEP_FIELDS` were kept; each row records its revision and row index.
