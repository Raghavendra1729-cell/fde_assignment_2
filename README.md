# FDE Data Foundations: exercises and assignment

- [`assignment/`](assignment/) is the main project: when is the JFK queue worth it? A pipeline on NYC TLC yellow taxi
  data (Track B). Start with [assignment/README.md](assignment/README.md).
- [`exercises/`](exercises/) has the FlashEats class notebooks:
  - `FlashEats_Class5_Starter.ipynb`, `FlashEats_Class5_Student.ipynb`: retrieval from SQL, CSV, JSON and the dispatch API
  - `FlashEats_Class6_Student.ipynb`: profiling and validation
  - `FlashEats_Class7_Challenge.ipynb`: workflow model and metrics
  - `FlashEats_Class8_Walkthrough.ipynb`: the dependable pipeline (`FlashEats_Classroom_Pack_V2/Class8_Project/`)
    and the Gate 2 review in `Class8_Project/GATE2_DATA_READINESS.md`

The exercise notebooks run from inside `exercises/`, the data pack is in `exercises/FlashEats_Classroom_Pack_V2/`.

## Running it

The raw taxi data is not in this repo (about 200 MB, public TLC files). The pipeline downloads it into
`assignment/data/raw/` and builds `assignment/data/processed/` the first time it runs:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r assignment/requirements.txt -r exercises/requirements.txt

cd assignment
python -m pipeline.run --months 2026-04 2026-05 2026-06
```

After that the assignment notebooks can be run (see `assignment/README.md`, section 7). The exercise notebooks
don't need any download, their data is already in `exercises/FlashEats_Classroom_Pack_V2/`.
