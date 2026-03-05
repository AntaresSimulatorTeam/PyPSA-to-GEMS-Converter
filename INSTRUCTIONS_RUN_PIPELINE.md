# Instructions to run pipeline

The **pypsa_eur_gen** script starts a Docker container (PyPSA-Eur dev image), clones the PyPSA-Eur repo inside it if needed, and gives you an interactive shell to run Snakemake and generate networks. Generated `.nc` files appear under **`pypsa-eur/resources/networks/`** in this repo.

**Before running the pipeline command**, all additional settings below must be set up (venv, dependencies, config file, and Docker).

## What you need

- **Docker Desktop** (assumed; the script uses its socket when present).
- **Python 3.11** (or compatible).
- **This repo** with dependencies installed (see Setup).
- **PyPSA-Eur config file** at the repo root (e.g. `config_pypsa_eur.yaml`); adjust it for your run before starting the pipeline.

## Setup (once per machine / venv)

From the **repository root**:

```bash
python3.11 -m venv venv
source venv/bin/activate          # On Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Run the pipeline

Once the setup above is done, from the **repository root** with the venv activated run:

```bash
python3 -m pypsa_eur_gen.run_generation
```

The script will pull the image if missing, start the container with the repo mounted at `/workspace`, clone PyPSA-Eur into `/workspace/pypsa-eur` if not already there, and drop you into a shell in `pypsa-eur`. Your config file at the repo root (e.g. `config_pypsa_eur.yaml`) is available in the container as `/workspace/config_pypsa_eur.yaml`.

## Inside the container: run Snakemake

You will be in `/workspace/pypsa-eur`. Run a dry-run first, then the full build:

```bash
snakemake --configfile /workspace/config_pypsa_eur.yaml -n
snakemake --configfile /workspace/config_pypsa_eur.yaml --cores 2
```

(Use `--cores N` with the number of cores you want.)

## Where to find the result networks

Generated networks are written under:

**Path in repo:** `pypsa-eur/resources/networks/`

| File | Meaning |
|------|--------|
| `base.nc` | Base network: full topology and data, no clustering. |
| `base_s.nc` | Simplified: e.g. snapshot clustering / time reduction applied. |
| `base_s_70.nc` | Simplified with **70 spatial clusters** (bus/network clustering). |
| `base_s_70_elec.nc` | Same 70 spatial clusters, **electricity**-only (or electricity-focused) setup. |
| `base_extended.nc` | Extended network: e.g. more transmission, links, or wider scope. |

These `.nc` files can be used as input for the PyPSA-to-GEMS converter.

## Exit the container

Type `exit` or press Ctrl+D when finished.
