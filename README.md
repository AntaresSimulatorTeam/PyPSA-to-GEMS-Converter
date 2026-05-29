<div align="center">
    <b>PyPSA to GEMS Converter</b> 
</div>

[![License](https://img.shields.io/github/license/AntaresSimulatorTeam/PyPSA-to-GEMS-Converter.svg)](LICENSE)
![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FAntaresSimulatorTeam%2FPyPSA-to-GEMS-Converter%2Fmain%2Fpyproject.toml)
[![PyPSA](https://img.shields.io/badge/PyPSA-1.2.0-blue.svg)](https://pypsa.org/)
[![Antares Simulator](https://img.shields.io/badge/Antares%20Simulator-10.0.1-blue.svg)](https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases/latest)

## About 
The [PyPSA](https://pypsa.org/)-to-[GEMS ](https://gems-energy.readthedocs.io/en/latest/) Converter is an open-source & standalone python package that enables the conversion of studies conducted in PyPSA into the GEMS format: it exports a [PyPSA Network](https://docs.pypsa.org/latest/api/networks/network/) as a [GEMS ](https://gems-energy.readthedocs.io/en/latest/3_User_Guide/3_GEMS_File_Structure/1_overview/) folder.
This converter is based on the representation of the PyPSA models of components as a GEMS library of models: [pypsa_models.yml](https://github.com/AntaresSimulatorTeam/GEMS/blob/main/libraries/pypsa_models.yml).

### Key Features 
- **Conversion of linear optimal power flow & economical dispatch studies**
- **Conversion of two-stage stochastic optimization studies**

## Table of Contents
- [How the Converter Works](#how-the-converter-works)
- [Input and Output of the Converter](#input-and-output-of-the-converter)
- [Current Limitations of the Converter](COMPATIBILITY.md#converter-limitations)
- [Step-by-Step Guide: Manually Executing a Simulation in GEMS Modeler](#step-by-step-guide-manually-executing-a-simulation-in-gems-modeler)
- [Comparing Results Between GEMS Modeler and PyPSA](#comparing-results-between-gems-modeler-and-pypsa)


## How the Converter Works

The PyPSA to GEMS Converter transforms [PyPSA Network](https://docs.pypsa.org/latest/api/networks/network/) into a [GEMS study folder](https://gems-energy.readthedocs.io/en/latest/3_User_Guide/3_GEMS_File_Structure/1_overview/), through the following steps.
### 1. **Input Validation and Preprocessing**
The converter first validates that the PyPSA network meets the requirements for conversion.<br/>
It performs necessary preprocessing steps such as normalizing component names, handling missing attributes, and ensuring data consistency.<br/>
This stage ensures the input PyPSA model is **compatible** with the conversion process.<br/>

### 2. **Component Registration and Data Extraction**
The converter identifies and extracts all relevant components from the PyPSA network, including both **static (constant)** and **dynamic (time-dependent)** parameters. <br/>
It maps PyPSA-specific parameter names to their GEMS equivalents and organizes the data for conversion.

### 3. **Time Series Processing**
For parameters that vary over time, the converter extracts time series data and writes them to separate data files (**CSV** or **TSV** format).<br/> 
The converter handles both deterministic studies (single time series) and stochastic studies (multiple scenarios), maintaining the temporal structure of the original PyPSA model.

### 4. **GEMS Component Generation**
Each PyPSA component is transformed into its corresponding GEMS representation. <br/>
The converter creates GEMS components with appropriate parameters, distinguishing between constant values and time-dependent references. <br/>
Connections between components (such as generators and loads connected to buses) are established through GEMS port connections. <br/>

### 5. **Global Constraints Handling**
If the PyPSA model includes global constraints (such as CO₂ emission limits), the converter identifies these and creates corresponding GEMS constraint components, linking them to the relevant system components.

### 6. **Study Structure Generation**
Finally, the converter generates the complete GEMS study structure.

## Input and Output of the Converter

**Input:**
The converter requires the following inputs: <br/> 
- **PyPSA network object** <br/>
The fully defined PyPSA network that will be converted into a GEMS-compatible study.<br/>
- **Output path** <br/>
The directory where the generated GEMS study will be created.<br/>
- **Time series file format** <br/>
Format used for exported time-dependent data (e.g. csv, tsv).

## Output
The converter generates a **structured GEMS study directory** at the provided output path. <br/>
The directory layout follows the conventions expected by the GEMS modeler: <br/>

```md
    study_directory/
    └── systems/
        └── system_name/
            └── input/
                ├── optim-config.yml--------> Benders decomposition parameters, used by the modeler to generate MPS files
                ├── system.yml -------------> Main system description
                ├── parameters.yml----------> Solver and simulation parameters
                ├── model-libraries/ 
                │   └── pypsa_models.yml---> Model library definitions 
                └── data-series/ ----------> Time and/or scenarion dependent parameters
                    └── ...
```

## Current Limitations of the Converter

For the full list of unsupported components, component restrictions, and network-level constraints, see [COMPATIBILITY.md](COMPATIBILITY.md#converter-limitations).

## Installation

### 1. **Prerequisites**

PyPSA-to-GEMS-Converter needs Python version >=3.11, and it's tested on version 3.11 and 3.12.

Its dependencies are listed inside the [`pyproject.toml`](pyproject.toml) and it is OS independent.

It relies on Antares Simulator binaries findable in the official repo [**link to the v10.0.1 release page**](https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases/tag/v10.0.1).

### 2. **Commands**

pip:
```bash
pip install pypsa-to-gems-converter
```

uv:
```bash
uv add pypsa-to-gems-converter
```

### 3. **CLI Usage**

```bash
pypsa-to-gems network.nc -o path_to_gems_study/
```

Key flags:

| Flag | Description | Default |
|------|-------------|---------|
| `network` | Path to the PyPSA network file (`.nc`) | required |
| `-o` / `--output` | Directory where the GEMS study will be written (created if missing) | required |
| `--series-format` | Time series file format (`.tsv` or `.csv`) | `.tsv` |
| `--solver` | Solver name written to GEMS parameters | `highs` |
|`-h` / `--help`| Show the help message and exit | none|

## Step-by-Step Guide: Manually Executing a Simulation in GEMS Modeler
- Build or load a PyPSA network 
```python
# Setup
study_dir = Path("tmp/my_study")  # Absolute path to the GEMS study directory

# Option A: build the network in code
network = Network()
network.add("Bus", "bus1", v_nom=1)
network.add("Load", "load1", bus="bus1", p_set=[10, 20, 30])
network.add("Generator", "gen1", bus="bus1", p_nom=100, marginal_cost=50)

# Option B: load the network from a file
network = Network("simple_network.nc")  # Absolute path to the PyPSA file
```
- Convert the PyPSA network to a GEMS study
```python
# Convert PyPSA network to GEMS
PyPSAStudyConverter(
    pypsa_network=network,
    study_dir=study_dir,
    series_file_format=".tsv",  # Supported formats: .tsv, .csv
).to_gems_study()
```
- Run the Antares Modeler optimization 
```python
# Path to the Antares modeler binary
modeler_bin = Path("antares-10.0.1-Ubuntu-22.04/bin/antares-modeler")

# Run the optimization
subprocess.run([
    str(modeler_bin),
    str(study_dir / "systems")
])
```
## Comparing Results Between GEMS Modeler and PyPSA
If you want to see detailed statistics and a comparison between **Antares Modeler** and **PyPSA study optimization**, you can check the full analysis here:  

👉 [View benchmark analysis](tests/local_benchmark/benchmark_analysis.ipynb)
